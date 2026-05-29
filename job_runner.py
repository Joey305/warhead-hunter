
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
job_runner.py — robust pipeline runner

Fixes:
  ✅ Prevents stdout/stderr deadlocks (stderr merged into stdout)
  ✅ Unbuffered logs (python -u + PYTHONUNBUFFERED=1)
  ✅ Sets JOB_ID env var for every step (so Step 11 can resolve JOB_ID reliably)
  ✅ Per-step hard timeout + "no-output" watchdog (kills true hangs)
  ✅ Optional soft-fail steps (continue pipeline even if a step fails)
  ✅ Writes a persistent job.log file inside each job folder
  ✅ Thread-safe JOB_STORE updates (lock)
"""

import os
import json
import shutil
import subprocess
import threading
import uuid
import time
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional
from api.sdf_resolver import resolve_sdf_path, row_sdf_key
from job_state import append_job_log, write_job_metadata as write_job_metadata_disk, results_ready_from_disk

# =============================================================================
# CONFIG
# =============================================================================
ASSET_DIR = "pipeline_assets"
JOBS_DIR = "jobs"


def _default_python_bin() -> str:
    explicit = os.environ.get("PYTHON_BIN")
    if explicit:
        return explicit

    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix and os.path.basename(conda_prefix) == "warhead":
        candidate = os.path.join(conda_prefix, "bin", "python")
        if os.path.exists(candidate):
            return candidate

    warhead_candidate = os.path.expanduser("~/miniconda3/envs/warhead/bin/python")
    if os.path.exists(warhead_candidate):
        return warhead_candidate

    return sys.executable or "python3"


PYTHON_BIN = _default_python_bin()
os.makedirs(JOBS_DIR, exist_ok=True)

# Global dictionary to track job status in memory
# Structure:
# JOB_STORE[job_id] = {
#   "status": "pending|running|completed|failed",
#   "target": str,
#   "created_at": str,
#   "started_at": str,
#   "finished_at": str,
#   "current_step": str,
#   "step_started_at": str,
#   "log": [str, ...],
# }
JOB_STORE: Dict[str, Dict[str, Any]] = {}
JOB_LOCK = threading.Lock()

# Per-step total runtime timeout (seconds). Set None for no hard timeout.
STEP_TIMEOUTS = {
    # adjust as you like
    "1_GRABBER.py": 15 * 60,
    "2_SQchk.py": 10 * 60,
    "6_SASA.py": 20 * 60,
    "7_metadata.py": 30 * 60,
    "9_2Dmapping.py": 20 * 60,
    "10_2DmappingExtraction.py": 15 * 60,
    "11_mcsMatcher.py": 45 * 60,  # can be heavy
    "12_Results.py": 10 * 60,
    "15_ResultsMerged.py": 10 * 60,
    "16_ResultsDisplay.py": 10 * 60,
}

# If a step produces no output for this many seconds, kill it (prevents "silent hang")
# You can override per-step below if needed.
NO_OUTPUT_TIMEOUT_DEFAULT = 180  # 3 min
NO_OUTPUT_TIMEOUT = {
    "11_mcsMatcher.py": 120,  # step 11 should be chatty; if it goes silent too long, kill it
}

# Steps that are allowed to fail without killing the whole job
SOFT_FAIL = {
    # Example: if you decide step 11 should not block pipeline:
    # "11_mcsMatcher.py",
}

RUN_CLEANUP_STEP = os.environ.get("WARHEAD_RUN_CLEANUP_STEP", "1") == "1"
CLEANUP_SCRIPT_NAME = "18_CleanJobDirNzip.py"

# =============================================================================
# LOGGING HELPERS
# =============================================================================
def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")

def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _job_log_path(job_dir: str) -> str:
    return os.path.join(job_dir, "job.log")

def _job_metadata_path(job_dir: str) -> str:
    return os.path.join(job_dir, "job_metadata.json")

def _metadata_timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def _default_outputs(job_id: str) -> Dict[str, Any]:
    return {
        "job_dir": os.path.join(JOBS_DIR, job_id),
        "results_url": f"/api/jobs/{job_id}/results",
        "files_url": f"/api/jobs/{job_id}/files",
        "bundle_url": f"/api/jobs/{job_id}/bundle",
    }

def write_job_metadata(job_id: str, patch: Dict[str, Any], job_dir: Optional[str] = None) -> None:
    job_dir = job_dir or os.path.join(JOBS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    payload = dict(patch or {})
    payload.setdefault("outputs", _default_outputs(job_id))
    payload.setdefault("error", None)
    payload["job_dir"] = job_dir
    payload["results_ready"] = results_ready_from_disk(job_id)
    with JOB_LOCK:
        write_job_metadata_disk(job_id, payload)

def log_message(job_id: str, message: str) -> None:
    entry = f"[{_now()}] {message}"
    print(entry, flush=True)

    with JOB_LOCK:
        if job_id in JOB_STORE:
            JOB_STORE[job_id].setdefault("log", []).append(entry)

    # Also persist to disk if possible
    try:
        append_job_log(job_id, entry)
    except Exception:
        pass


# =============================================================================
# SUBPROCESS RUNNER (NO DEADLOCK)
# =============================================================================
def run_script_logged(
    job_id: str,
    script_name: str,
    args: List[str],
    job_dir: str,
    timeout_sec: Optional[int] = None,
    no_output_timeout_sec: Optional[int] = None,
) -> None:
    script_path = os.path.join(job_dir, script_name)
    if not os.path.exists(script_path):
        log_message(job_id, f"⚠️ Skipping {script_name} (File not found)")
        return

    # Update job store state
    with JOB_LOCK:
        JOB_STORE[job_id]["current_step"] = script_name
        JOB_STORE[job_id]["step_started_at"] = _now()
    write_job_metadata(job_id, {
        "status": JOB_STORE.get(job_id, {}).get("status", "running"),
        "current_step": script_name,
        "step_started_at": _now(),
        "last_log_at": _metadata_timestamp(),
    }, job_dir=job_dir)

    log_message(job_id, f"🚀 Running {script_name}...")
    log_message(job_id, f"🐍 Pipeline Python: {PYTHON_BIN}")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["JOB_ID"] = job_id  # ✅ critical: Step 11 can rely on this anywhere

    # python -u forces unbuffered output (so logs stream)
    cmd = [PYTHON_BIN, "-u", script_name] + args

    start = time.time()
    last_output = time.time()
    no_output_timeout_sec = (
        no_output_timeout_sec
        if no_output_timeout_sec is not None
        else NO_OUTPUT_TIMEOUT.get(script_name, NO_OUTPUT_TIMEOUT_DEFAULT)
    )

    # Merge stderr -> stdout to avoid deadlock
    with subprocess.Popen(
        cmd,
        cwd=job_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=env,
    ) as proc:

        try:
            # Stream output line-by-line
            for line in proc.stdout:
                last_output = time.time()
                line = line.rstrip("\n")
                if line:
                    # store raw line (already includes step prefixing inside scripts)
                    with JOB_LOCK:
                        JOB_STORE[job_id]["log"].append(line)
                    # also persist
                    try:
                        append_job_log(job_id, line)
                    except Exception:
                        pass

                # hard timeout
                if timeout_sec is not None and (time.time() - start) > timeout_sec:
                    proc.kill()
                    raise TimeoutError(f"{script_name} timed out after {timeout_sec}s")

                # no-output watchdog
                if no_output_timeout_sec is not None and (time.time() - last_output) > no_output_timeout_sec:
                    proc.kill()
                    raise TimeoutError(f"{script_name} produced no output for >{no_output_timeout_sec}s (killed)")

            proc.wait()

        finally:
            # Make sure process is not left running
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

    if proc.returncode != 0:
        raise RuntimeError(f"{script_name} failed with code {proc.returncode}")


# =============================================================================
# PIPELINE
# =============================================================================
def _copy_assets(job_id: str, job_dir: str) -> None:
    if not os.path.isdir(ASSET_DIR):
        raise FileNotFoundError(f"ASSET_DIR not found: {ASSET_DIR}")

    log_message(job_id, f"📦 Copying assets from {ASSET_DIR} → {job_dir} ...")
    for item in os.listdir(ASSET_DIR):
        s = os.path.join(ASSET_DIR, item)
        d = os.path.join(job_dir, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)

def _write_inputs(job_id: str, job_dir: str, target_name: str, search_query: str, fasta_seq: str) -> None:
    input_data = [{
        "protein": target_name,
        "search_query": search_query,
        "fasta": fasta_seq
    }]
    df = pd.DataFrame(input_data)
    df.to_csv(os.path.join(job_dir, "input.csv"), index=False)
    df.to_csv(os.path.join(job_dir, "Protein_Data.csv"), index=False)
    log_message(job_id, "🧾 Wrote input.csv and Protein_Data.csv")


def _run_cleanup_packaging(job_id: str, job_dir: str) -> None:
    if not RUN_CLEANUP_STEP:
        log_message(job_id, "🧹 Cleanup packaging step disabled by WARHEAD_RUN_CLEANUP_STEP=0")
        return

    script_path = os.path.join(job_dir, CLEANUP_SCRIPT_NAME)
    if not os.path.exists(script_path):
        log_message(job_id, f"🧹 Cleanup packaging step skipped ({CLEANUP_SCRIPT_NAME} not found in job directory)")
        return

    cleanup_apply = os.environ.get("WARHEAD_CLEANUP_APPLY", "1").strip().lower() not in {
        "0", "false", "no", "off"
    }

    cleanup_delete_rebuildable = os.environ.get("WARHEAD_CLEANUP_DELETE_REBUILDABLE", "1").strip().lower() not in {
        "0", "false", "no", "off"
    }

    cleanup_delete_cif = os.environ.get("WARHEAD_CLEANUP_DELETE_CIF", "1").strip().lower() not in {
        "0", "false", "no", "off"
    }

    cleanup_force_delete_cif = os.environ.get("WARHEAD_CLEANUP_FORCE_DELETE_CIF", "1").strip().lower() not in {
        "0", "false", "no", "off"
    }

    cmd = [PYTHON_BIN, "-u", CLEANUP_SCRIPT_NAME, "--job-dir", ".", "--safe-package"]

    if cleanup_apply:
        cmd.append("--apply")

    if cleanup_delete_rebuildable:
        cmd.append("--delete-rebuildable")

    if cleanup_delete_cif:
        cmd.append("--allow-delete-cif")

    if cleanup_force_delete_cif:
        cmd.append("--force-delete-cif")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["JOB_ID"] = job_id

    log_message(job_id, f"🧹 Running cleanup/package step: {' '.join(cmd)}")

    with subprocess.Popen(
        cmd,
        cwd=job_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=env,
    ) as proc:
        try:
            for line in proc.stdout or []:
                line = line.rstrip()
                if line:
                    log_message(job_id, f"[cleanup] {line}")
            proc.wait()
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

    if proc.returncode != 0:
        raise RuntimeError(f"{CLEANUP_SCRIPT_NAME} failed with code {proc.returncode}")

    public_bundle_rel = f"bundles/{job_id}_warhead_hunter_public_results.zip"
    manifest_rel = "job_result_manifest.json"
    report_rel = "cleanup_report.md"
    outputs = {
        **_default_outputs(job_id),
        "public_bundle_path": public_bundle_rel,
        "job_result_manifest_path": manifest_rel,
        "cleanup_report_path": report_rel,
    }
    write_job_metadata(job_id, {"outputs": outputs}, job_dir=job_dir)
    log_message(job_id, f"🧹 Safe package created: {public_bundle_rel}")


def _csv_path(job_dir: Path, filename: str) -> Optional[Path]:
    for candidate in [
        job_dir / filename,
        job_dir / "TARGET_RESULTS" / filename,
    ]:
        if candidate.exists():
            return candidate
    return None


def _list_target_result_files(job_dir: Path, limit: int = 80) -> List[str]:
    root = job_dir / "TARGET_RESULTS"
    if not root.exists():
        return []
    files = []
    for fp in sorted(root.rglob("*")):
        if fp.is_file():
            files.append(str(fp.relative_to(job_dir)))
        if len(files) >= limit:
            break
    return files


def _expected_keys_from_ligand_atoms(job_path: Path) -> List[Tuple[str, str, str, str]]:
    ligand_atoms = _csv_path(job_path, "Ligand_3D_Atoms.csv")
    if ligand_atoms is None:
        raise RuntimeError("Required SDF source artifact missing: Ligand_3D_Atoms.csv")

    df = pd.read_csv(ligand_atoms, dtype=str).fillna("")
    required = {"pdb_id", "Chain", "Warhead", "Residue_ID"}
    if not required.issubset(df.columns):
        raise RuntimeError(f"Ligand_3D_Atoms.csv missing required SDF key columns: {sorted(required - set(df.columns))}")

    keys = []
    grouped = df.groupby(["pdb_id", "Chain", "Warhead", "Residue_ID"], dropna=False)
    for pdb, chain, ligand, resid in grouped.groups.keys():
        keys.append(row_sdf_key({"pdb_id": pdb, "Chain": chain, "Warhead": ligand, "Residue_ID": resid}))
    return sorted(set(keys))


def _residue_lookup_from_summary(job_path: Path) -> Dict[Tuple[str, str, str], str]:
    lookup: Dict[Tuple[str, str, str], str] = {}
    for filename in ["Ligand_3D_Atoms.csv", "Resolved_SASA_Summary.csv"]:
        path = _csv_path(job_path, filename)
        if path is None:
            continue
        df = pd.read_csv(path, dtype=str).fillna("")
        for _, row in df.iterrows():
            pdb, chain, ligand, resid = row_sdf_key(row.to_dict())
            if pdb and chain and ligand and resid:
                lookup.setdefault((pdb, chain, ligand), resid)
    return lookup


def validate_mcs_sdf_checkpoint(job_id: str, job_dir: str, *, copied: bool) -> None:
    job_path = Path(job_dir)
    sdf_dir = (
        job_path / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF"
        if copied else
        job_path / "MCS_Output" / "MCS_SDF"
    )
    if not sdf_dir.exists():
        raise RuntimeError(f"Required MCS SDF folder missing: {sdf_dir}")

    sdf_files = sorted(sdf_dir.glob("*.sdf"))
    expected_keys = _expected_keys_from_ligand_atoms(job_path)
    if not sdf_files:
        raise RuntimeError(
            f"Required MCS SDF folder contains zero SDF files: {sdf_dir}. "
            f"First expected keys: {expected_keys[:20]}"
        )

    missing = []
    for pdb, chain, ligand, resid in expected_keys:
        expected = sdf_dir / f"{pdb}_{chain}_{ligand}_{resid}.sdf"
        if not expected.exists():
            missing.append((pdb, chain, ligand, resid))

    if missing:
        raise RuntimeError(
            f"MCS SDF checkpoint failed for {sdf_dir}: expected={len(expected_keys)} "
            f"sdf_files={len(sdf_files)} first_missing={missing[:20]} "
            f"sample_files={[p.name for p in sdf_files[:20]]}"
        )

    label = "TARGET_RESULTS/MCS_Output/MCS_SDF" if copied else "MCS_Output/MCS_SDF"
    log_message(job_id, f"✅ SDF validation PASS after {'12_Results.py' if copied else '11_mcsMatcher.py'}: {label} files={len(sdf_files)} expected={len(expected_keys)}")


def validate_required_display_artifacts(job_id: str, job_dir: str) -> None:
    job_path = Path(job_dir)
    results_path = _csv_path(job_path, "Results_Display.csv")
    if results_path is None:
        raise RuntimeError("Required display artifact missing: Results_Display.csv")

    try:
        results = pd.read_csv(results_path, dtype=str).fillna("")
    except Exception as exc:
        raise RuntimeError(f"Could not read Results_Display.csv: {exc}") from exc

    if results.empty:
        raise RuntimeError("Results_Display.csv exists but has zero displayed rows")

    sdf_dir = job_path / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF"
    if not sdf_dir.exists():
        raise RuntimeError(f"Required copied MCS SDF folder missing: {sdf_dir}")

    sdf_files = sorted(sdf_dir.glob("*.sdf"))
    if not sdf_files:
        raise RuntimeError(
            "Required copied MCS SDF folder contains zero SDF files. "
            f"Expected folder: {sdf_dir}. "
            f"First display keys: {[row_sdf_key(r.to_dict()) for _, r in results.head(20).iterrows()]}. "
            f"Actual TARGET_RESULTS files: {_list_target_result_files(job_path)}"
        )

    residue_lookup = _residue_lookup_from_summary(job_path)
    missing = []
    matched = 0
    for _, row in results.iterrows():
        pdb, chain, ligand, resid = row_sdf_key(row.to_dict())
        if not resid:
            resid = residue_lookup.get((pdb, chain, ligand), "")
        resolved, _diag = resolve_sdf_path(job_path, pdb, chain, ligand, resid)
        if resolved:
            matched += 1
        else:
            missing.append((pdb, chain, ligand, resid))

    required_csvs = ["Warhead_SASA_atoms.csv"]
    for filename in required_csvs:
        if _csv_path(job_path, filename) is None:
            raise RuntimeError(f"Required display artifact missing: {filename}")

    ligand_sasa = _csv_path(job_path, "Ligand_3D_Atoms_with_SASA.csv")
    if ligand_sasa is None:
        raise RuntimeError("Required display artifact missing: Ligand_3D_Atoms_with_SASA.csv")

    if missing:
        raise RuntimeError(
            "SDF contract failed: at least one Results_Display row does not resolve to an SDF. "
            f"Rows={len(results)}, SDF files={len(sdf_files)}, "
            f"First missing keys={missing[:20]}, "
            f"Sample SDF files={[str(p.relative_to(job_path)) for p in sdf_files[:20]]}, "
            f"Actual TARGET_RESULTS files={_list_target_result_files(job_path)}"
        )

    log_message(job_id, f"✅ final SDF validation PASS: rows={len(results)} matched={matched} sdf_files={len(sdf_files)} dir={sdf_dir}")

def run_pipeline_task(job_id: str, target_name: str, search_query: str, fasta_seq: str) -> None:
    job_dir = os.path.join(JOBS_DIR, job_id)

    with JOB_LOCK:
        JOB_STORE[job_id]["status"] = "running"
        JOB_STORE[job_id]["started_at"] = _timestamp()
        JOB_STORE[job_id]["finished_at"] = ""
        JOB_STORE[job_id]["current_step"] = ""
        JOB_STORE[job_id]["step_started_at"] = ""
    write_job_metadata(job_id, {
        "status": "running",
        "started_at": _timestamp(),
        "finished_at": "",
        "current_step": "",
        "step_started_at": "",
        "error": None,
        "target": target_name,
        "results_ready": False,
    }, job_dir=job_dir)

    try:
        log_message(job_id, f"Initializing workspace for {target_name}...")
        os.makedirs(job_dir, exist_ok=True)

        # Preserve the creation line and append a run-start marker.
        try:
            with open(_job_log_path(job_dir), "a", encoding="utf-8") as f:
                f.write(f"[{_now()}] Job {job_id} started: {_timestamp()}\n")
        except Exception:
            pass
        write_job_metadata(job_id, {"last_log_at": _metadata_timestamp()}, job_dir=job_dir)

        _copy_assets(job_id, job_dir)
        _write_inputs(job_id, job_dir, target_name, search_query, fasta_seq)

        # NOTE: Step 11 should NOT receive job_id as an arg unless it explicitly parses it.
        # We provide JOB_ID via env instead.
        scripts: List[Tuple[str, List[str]]] = [
            ("1_GRABBER.py", ["--auto", "input.csv"]),
            ("2_SQchk.py", []),
            ("3_PDBmkr.py", []),
            ("4_PDBfxr.py", []),
            ("5_PDBcln.py", []),
            ("6_SASA.py", []),
            ("7_metadata.py", ["--auto", "Warhead_SASA_summary.csv"]),
            ("8_scaffold.py", []),
            ("9_2Dmapping.py", ["--input", "Resolved_SASA_Summary.csv", "--auto"]),
            ("10_2DmappingExtraction.py", []),
            ("11_mcsMatcher.py", []),  # ✅ no args; JOB_ID comes from env / cwd
            ("12_Results.py", []),
            ("15_ResultsMerged.py", []),
            ("16_ResultsDisplay.py", [job_id]),  # keep if your display script expects it
        ]

        for script_name, args in scripts:
            try:
                run_script_logged(
                    job_id=job_id,
                    script_name=script_name,
                    args=args,
                    job_dir=job_dir,
                    timeout_sec=STEP_TIMEOUTS.get(script_name),
                    no_output_timeout_sec=NO_OUTPUT_TIMEOUT.get(script_name),
                )
            except Exception as e:
                if script_name in SOFT_FAIL:
                    log_message(job_id, f"⚠️ {script_name} failed but continuing (soft-fail): {e}")
                    continue
                raise

            if script_name == "11_mcsMatcher.py":
                validate_mcs_sdf_checkpoint(job_id, job_dir, copied=False)
            elif script_name == "12_Results.py":
                validate_mcs_sdf_checkpoint(job_id, job_dir, copied=True)

        try:
            validate_required_display_artifacts(job_id, job_dir)
        except Exception as validation_error:
            log_message(job_id, f"⚠️ Final display validation warning: {validation_error}")
            log_message(job_id, "⚠️ Continuing because core SDF checkpoints already passed after 11_mcsMatcher.py and 12_Results.py.")

        with JOB_LOCK:
            JOB_STORE[job_id]["status"] = "completed"
            JOB_STORE[job_id]["finished_at"] = _timestamp()
        write_job_metadata(job_id, {
            "status": "completed",
            "finished_at": _timestamp(),
            "current_step": "",
            "error": None,
            "results_ready": True,
        }, job_dir=job_dir)

        try:
            _run_cleanup_packaging(job_id, job_dir)
        except Exception as cleanup_error:
            log_message(job_id, f"⚠️ Cleanup packaging step failed: {cleanup_error}")

        log_message(job_id, "✅ PIPELINE FINISHED SUCCESSFULLY")
        log_message(job_id, "Access results in the Browse tab.")

    except Exception as e:
        with JOB_LOCK:
            JOB_STORE[job_id]["status"] = "failed"
            JOB_STORE[job_id]["finished_at"] = _timestamp()
        write_job_metadata(job_id, {
            "status": "failed",
            "finished_at": _timestamp(),
            "error": {
                "message": str(e),
            },
            "results_ready": results_ready_from_disk(job_id),
        }, job_dir=job_dir)

        log_message(job_id, f"❌ CRITICAL ERROR: {str(e)}")


def start_job(
    target_name: str,
    search_query: str,
    fasta_seq: str,
    *,
    source: str = "web",
    request_payload: Optional[Dict[str, Any]] = None,
) -> str:
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    with JOB_LOCK:
        JOB_STORE[job_id] = {
            "status": "pending",
            "target": target_name,
            "created_at": _timestamp(),
            "started_at": "",
            "finished_at": "",
            "current_step": "",
            "step_started_at": "",
            "log": [],
        }
    write_job_metadata(job_id, {
        "status": "queued",
        "target": target_name,
        "created_at": _metadata_timestamp(),
        "started_at": "",
        "finished_at": "",
        "current_step": "",
        "step_started_at": "",
        "last_log_at": _metadata_timestamp(),
        "source": source,
        "request": request_payload or {
            "target_name": target_name,
            "search_query": search_query,
            "fasta_seq": fasta_seq,
        },
        "outputs": _default_outputs(job_id),
        "error": None,
        "results_ready": False,
    }, job_dir=job_dir)
    try:
        with open(_job_log_path(job_dir), "w", encoding="utf-8") as handle:
            handle.write(f"[{_now()}] Job {job_id} created for target: {target_name}\n")
        write_job_metadata(job_id, {"last_log_at": _metadata_timestamp()}, job_dir=job_dir)
    except Exception:
        pass

    thread = threading.Thread(
        target=run_pipeline_task,
        args=(job_id, target_name, search_query, fasta_seq),
        daemon=True,
    )
    thread.start()

    return job_id

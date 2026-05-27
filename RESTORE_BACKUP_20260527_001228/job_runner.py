
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
import sys
import json
import shutil
import subprocess
import threading
import uuid
import time
import pandas as pd
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

# =============================================================================
# CONFIG
# =============================================================================
ASSET_DIR = "pipeline_assets"
JOBS_DIR = "jobs"
os.makedirs(JOBS_DIR, exist_ok=True)
PYTHON_BIN = os.environ.get("PYTHON_BIN") or sys.executable or "python3"

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
    fp = _job_metadata_path(job_dir)

    with JOB_LOCK:
        data: Dict[str, Any] = {}
        if os.path.exists(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        data.update(patch or {})
        data["job_id"] = job_id
        data["updated_at"] = _metadata_timestamp()
        data.setdefault("outputs", _default_outputs(job_id))
        data.setdefault("error", None)

        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)

def log_message(job_id: str, message: str) -> None:
    entry = f"[{_now()}] {message}"
    print(entry, flush=True)

    with JOB_LOCK:
        if job_id in JOB_STORE:
            JOB_STORE[job_id].setdefault("log", []).append(entry)

    # Also persist to disk if possible
    try:
        job_dir = os.path.join(JOBS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        with open(_job_log_path(job_dir), "a", encoding="utf-8") as f:
            f.write(entry + "\n")
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
    }, job_dir=job_dir)

    log_message(job_id, f"🚀 Running {script_name}...")

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
                        with open(_job_log_path(job_dir), "a", encoding="utf-8") as f:
                            f.write(line + "\n")
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

    cmd = [PYTHON_BIN, "-u", CLEANUP_SCRIPT_NAME, "--job-dir", ".", "--safe-package"]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["JOB_ID"] = job_id

    log_message(job_id, "🧹 Running cleanup/package step in safe mode...")

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
                line = line.rstrip("\n")
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
    }, job_dir=job_dir)

    try:
        log_message(job_id, f"Initializing workspace for {target_name}...")
        os.makedirs(job_dir, exist_ok=True)

        # Reset persistent log file for this run
        try:
            with open(_job_log_path(job_dir), "w", encoding="utf-8") as f:
                f.write(f"[{_now()}] Job {job_id} started: {_timestamp()}\n")
        except Exception:
            pass

        _copy_assets(job_id, job_dir)
        _write_inputs(job_id, job_dir, target_name, search_query, fasta_seq)
        log_message(job_id, f"🐍 Pipeline Python: {PYTHON_BIN}")

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

        with JOB_LOCK:
            JOB_STORE[job_id]["status"] = "completed"
            JOB_STORE[job_id]["finished_at"] = _timestamp()
        write_job_metadata(job_id, {
            "status": "completed",
            "finished_at": _timestamp(),
            "current_step": "",
            "error": None,
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
        "created_at": _metadata_timestamp(),
        "source": source,
        "request": request_payload or {
            "target_name": target_name,
            "search_query": search_query,
            "fasta_seq": fasta_seq,
        },
        "outputs": _default_outputs(job_id),
        "error": None,
    }, job_dir=job_dir)

    thread = threading.Thread(
        target=run_pipeline_task,
        args=(job_id, target_name, search_query, fasta_seq),
        daemon=True,
    )
    thread.start()

    return job_id

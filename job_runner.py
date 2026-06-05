
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
import signal
import selectors
from copy import deepcopy
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional
from api.randy_backup_client import (
    archive_required,
    backup_job_directory,
    backup_on_complete,
    backup_on_failure,
    initial_backup_status,
)
from api.sdf_resolver import expected_mcs_sdf_filename, resolve_sdf_path, row_sdf_key
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
IS_HEROKU = bool(os.environ.get("DYNO"))


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


MAX_IN_MEMORY_LOG_LINES = max(100, _env_int("WARHEAD_JOB_LOG_TAIL_LINES", 800))
DEFAULT_LOG_API_TAIL = max(100, _env_int("WARHEAD_JOB_LOG_API_TAIL", 250 if IS_HEROKU else 400))
MEMORY_WARN_MB = max(0, _env_int("WARHEAD_MEMORY_WARN_MB", 360 if IS_HEROKU else 0))
MEMORY_GUARD_MB = max(0, _env_int("WARHEAD_MEMORY_GUARD_MB", 430 if IS_HEROKU else 0))
CHILD_MEMORY_WARN_MB = max(0, _env_int("WARHEAD_CHILD_MEMORY_WARN_MB", MEMORY_WARN_MB))
CHILD_MEMORY_GUARD_MB = max(0, _env_int("WARHEAD_CHILD_MEMORY_GUARD_MB", MEMORY_GUARD_MB))
DYNO_MEMORY_WARN_MB = max(0, _env_int("WARHEAD_DYNO_MEMORY_WARN_MB", MEMORY_GUARD_MB))
DYNO_MEMORY_GUARD_MB = max(0, _env_int("WARHEAD_DYNO_MEMORY_GUARD_MB", DYNO_MEMORY_WARN_MB))
MEMORY_SAMPLE_INTERVAL_SEC = max(0.1, float(os.environ.get("WARHEAD_MEMORY_SAMPLE_INTERVAL_SEC", "0.5") or "0.5"))
RUN_CLEANUP_STEP = os.environ.get("WARHEAD_RUN_CLEANUP_STEP", "0" if IS_HEROKU else "1") == "1"
CLEANUP_SCRIPT_NAME = "18_CleanJobDirNzip.py"
CLEANUP_TIMEOUT_SEC = _env_int("WARHEAD_CLEANUP_TIMEOUT_SEC", 8 * 60)
CLEANUP_NO_OUTPUT_TIMEOUT_SEC = _env_int("WARHEAD_CLEANUP_NO_OUTPUT_TIMEOUT_SEC", 120)
DEFAULT_PIPELINE_MAX_WORKERS = 2 if IS_HEROKU else 3
PIPELINE_MAX_WORKERS = max(1, _env_int("WARHEAD_PIPELINE_MAX_WORKERS", DEFAULT_PIPELINE_MAX_WORKERS))
PIPELINE_WORKER_ENV_VARS = (
    "WARHEAD_SQCHK_MAX_WORKERS",
    "WARHEAD_PDBMKR_MAX_WORKERS",
    "WARHEAD_SASA_MAX_WORKERS",
    "WARHEAD_METADATA_MAX_WORKERS",
    "WARHEAD_MCS_MAX_WORKERS",
)
DEFAULT_STAGE_WORKER_CAPS = {
    "WARHEAD_SQCHK_MAX_WORKERS": 2 if IS_HEROKU else PIPELINE_MAX_WORKERS,
    "WARHEAD_PDBMKR_MAX_WORKERS": 2 if IS_HEROKU else PIPELINE_MAX_WORKERS,
    "WARHEAD_SASA_MAX_WORKERS": 1 if IS_HEROKU else PIPELINE_MAX_WORKERS,
    "WARHEAD_METADATA_MAX_WORKERS": 2 if IS_HEROKU else PIPELINE_MAX_WORKERS,
    "WARHEAD_MCS_MAX_WORKERS": 1 if IS_HEROKU else PIPELINE_MAX_WORKERS,
}

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
_LAST_LOG_METADATA_TOUCH: Dict[str, float] = {}
_MEMORY_WARN_ONCE: Dict[str, float] = {}

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
STEP11_NO_OUTPUT_TIMEOUT_SEC = _env_int("WARHEAD_STEP11_NO_OUTPUT_TIMEOUT_SEC", 300 if IS_HEROKU else 120)
NO_OUTPUT_TIMEOUT = {
    "11_mcsMatcher.py": STEP11_NO_OUTPUT_TIMEOUT_SEC,
}

# Steps that are allowed to fail without killing the whole job
SOFT_FAIL = {
    # Example: if you decide step 11 should not block pipeline:
    # "11_mcsMatcher.py",
}

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


def _deepcopy_jsonable(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return value

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
    existing = None
    metadata_path = _job_metadata_path(job_dir)
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as handle:
                existing = json.load(handle)
        except Exception:
            existing = None
    if isinstance(existing, dict):
        existing_backup = existing.get("backup")
        new_backup = payload.get("backup")
        if isinstance(existing_backup, dict) and isinstance(new_backup, dict):
            merged_backup = _deepcopy_jsonable(existing_backup)
            merged_backup.update(_deepcopy_jsonable(new_backup))
            payload["backup"] = merged_backup
    payload.setdefault("outputs", _default_outputs(job_id))
    payload["job_dir"] = job_dir
    payload["results_ready"] = results_ready_from_disk(job_id)
    with JOB_LOCK:
        write_job_metadata_disk(job_id, payload)


def _backup_state_patch(backup_result: Dict[str, Any], *, results_ready: bool) -> Dict[str, Any]:
    backup_ok = bool(backup_result.get("ok"))
    attempted = bool(backup_result.get("attempted"))
    status = str(backup_result.get("status") or "unknown")

    if backup_ok:
        archive_status = "verified"
    elif attempted:
        archive_status = "backup_failed"
    elif status.startswith("skipped"):
        archive_status = "backup_skipped"
    else:
        archive_status = "backup_pending"

    return {
        "backup": backup_result,
        "archive_status": archive_status,
        "results_available_not_backed_up": bool(results_ready and archive_status != "verified"),
    }

class MemoryGuardError(RuntimeError):
    pass


def _current_rss_mb(pid: Optional[int] = None) -> float:
    target_pid = int(pid or os.getpid())
    status_path = Path(f"/proc/{target_pid}/status")
    if status_path.exists():
        try:
            for line in status_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024.0, 1)
        except Exception:
            pass
    try:
        proc = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(target_pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        raw = (proc.stdout or "").strip()
        if raw:
            return round(int(raw.splitlines()[-1].strip()) / 1024.0, 1)
    except Exception:
        pass
    return 0.0


def _child_pids(pid: int) -> List[int]:
    proc_root = Path("/proc")
    if not proc_root.exists():
        return []
    children: Dict[int, List[int]] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            ppid = None
            for line in (entry / "status").read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("PPid:"):
                    ppid = int(line.split()[1])
                    break
            if ppid is not None:
                children.setdefault(ppid, []).append(int(entry.name))
        except Exception:
            continue

    out: List[int] = []
    stack = list(children.get(pid, []))
    seen = set()
    while stack:
        child = stack.pop()
        if child in seen:
            continue
        seen.add(child)
        out.append(child)
        stack.extend(children.get(child, []))
    return out


def _process_tree_rss_mb(root_pid: int) -> float:
    pids = [root_pid, *_child_pids(root_pid)]
    total = 0.0
    for pid in pids:
        total += _current_rss_mb(pid)
    return round(total, 1)


def _read_cgroup_memory_mb() -> Optional[float]:
    candidates = [
        Path("/sys/fs/cgroup/memory.current"),
        Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            raw = candidate.read_text(encoding="utf-8", errors="ignore").strip()
            if not raw or raw == "max":
                continue
            return round(int(raw) / (1024.0 * 1024.0), 1)
        except Exception:
            continue
    return None


def _read_cgroup_limit_mb() -> Optional[float]:
    candidates = [
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            raw = candidate.read_text(encoding="utf-8", errors="ignore").strip()
            if not raw or raw == "max":
                return None
            limit_bytes = int(raw)
            if limit_bytes <= 0 or limit_bytes >= (1 << 60):
                return None
            return round(limit_bytes / (1024.0 * 1024.0), 1)
        except Exception:
            continue
    return None


def _memory_snapshot(proc_pid: Optional[int] = None) -> Dict[str, Optional[float]]:
    parent_mb = _current_rss_mb()
    child_tree_mb = _process_tree_rss_mb(proc_pid) if proc_pid else 0.0
    combined_mb = round(parent_mb + child_tree_mb, 1)
    dyno_mb = _read_cgroup_memory_mb()
    return {
        "parent_mb": parent_mb,
        "child_tree_mb": child_tree_mb,
        "combined_mb": combined_mb,
        "dyno_mb": dyno_mb,
        "cgroup_limit_mb": _read_cgroup_limit_mb(),
    }


def _format_snapshot(snapshot: Dict[str, Optional[float]], *, include_delta_from: Optional[float] = None, child_peak_mb: Optional[float] = None, dyno_peak_mb: Optional[float] = None, combined_peak_mb: Optional[float] = None) -> str:
    parts = [f"parent={snapshot['parent_mb']:.1f}MB"]
    if include_delta_from is not None:
        parts.append(f"delta={snapshot['parent_mb'] - include_delta_from:+.1f}MB")
    if snapshot.get("child_tree_mb"):
        parts.append(f"child_tree={snapshot['child_tree_mb']:.1f}MB")
    if combined_peak_mb is not None:
        parts.append(f"combined_peak={combined_peak_mb:.1f}MB")
    elif snapshot.get("combined_mb") is not None:
        parts.append(f"combined={snapshot['combined_mb']:.1f}MB")
    if child_peak_mb is not None:
        parts.append(f"child_peak={child_peak_mb:.1f}MB")
    if snapshot.get("dyno_mb") is not None:
        parts.append(f"dyno={snapshot['dyno_mb']:.1f}MB")
    if dyno_peak_mb is not None:
        parts.append(f"dyno_peak={dyno_peak_mb:.1f}MB")
    return " ".join(parts)


def _append_live_log(job_id: str, line: str, *, persist: bool = True) -> None:
    with JOB_LOCK:
        if job_id in JOB_STORE:
            state = JOB_STORE[job_id]
            lines = state.setdefault("log", [])
            lines.append(line)
            if len(lines) > MAX_IN_MEMORY_LOG_LINES:
                del lines[:-MAX_IN_MEMORY_LOG_LINES]
                state["log_truncated"] = True
            state["log_line_count"] = int(state.get("log_line_count", 0)) + 1

    if persist:
        try:
            append_job_log(job_id, line, touch_metadata=False)
        except Exception:
            pass


def _touch_last_log_at(job_id: str, job_dir: str, *, force: bool = False) -> None:
    now = time.time()
    if not force and (now - _LAST_LOG_METADATA_TOUCH.get(job_id, 0.0)) < 10.0:
        return
    _LAST_LOG_METADATA_TOUCH[job_id] = now
    try:
        write_job_metadata(job_id, {"last_log_at": _metadata_timestamp()}, job_dir=job_dir)
    except Exception:
        pass


def _memory_status(job_id: str, script_name: str, phase: str, snapshot: Dict[str, Optional[float]], *, before_parent_mb: Optional[float] = None, child_peak_mb: Optional[float] = None, dyno_peak_mb: Optional[float] = None, combined_peak_mb: Optional[float] = None) -> float:
    log_message(
        job_id,
        f"[mem] {phase} {script_name} "
        + _format_snapshot(
            snapshot,
            include_delta_from=before_parent_mb,
            child_peak_mb=child_peak_mb,
            dyno_peak_mb=dyno_peak_mb,
            combined_peak_mb=combined_peak_mb,
        ),
    )
    return snapshot["parent_mb"] or 0.0


def _remember_memory_warning(job_id: str, key: str) -> bool:
    now = time.time()
    warn_key = f"{job_id}:{key}"
    last = _MEMORY_WARN_ONCE.get(warn_key, 0.0)
    if now - last < 10.0:
        return False
    _MEMORY_WARN_ONCE[warn_key] = now
    return True


def _record_memory_failure(job_id: str, job_dir: str, script_name: str, phase: str, metric: str, value_mb: float, guard_mb: float, snapshot: Dict[str, Optional[float]]) -> str:
    message = (
        f"Job failed safely before dyno crash: {script_name} exceeded {metric} memory guard during {phase}. "
        + _format_snapshot(snapshot)
        + f" guard={guard_mb:.1f}MB"
    )
    write_job_metadata(job_id, {
        "memory_failure": {
            "step": script_name,
            "phase": phase,
            "metric": metric,
            "value_mb": value_mb,
            "guard_mb": guard_mb,
            "parent_mb": snapshot.get("parent_mb"),
            "child_tree_mb": snapshot.get("child_tree_mb"),
            "combined_mb": snapshot.get("combined_mb"),
            "dyno_mb": snapshot.get("dyno_mb"),
            "cgroup_limit_mb": snapshot.get("cgroup_limit_mb"),
        }
    }, job_dir=job_dir)
    return message


def _check_memory_guard(job_id: str, script_name: str, job_dir: str, *, phase: str, proc_pid: Optional[int] = None) -> Dict[str, Optional[float]]:
    snapshot = _memory_snapshot(proc_pid)
    checks = [
        ("parent", snapshot["parent_mb"], MEMORY_WARN_MB, MEMORY_GUARD_MB),
        ("child", snapshot["child_tree_mb"], CHILD_MEMORY_WARN_MB, CHILD_MEMORY_GUARD_MB),
        ("combined", snapshot["combined_mb"], 0, 0),
        ("dyno", snapshot["dyno_mb"], DYNO_MEMORY_WARN_MB, DYNO_MEMORY_GUARD_MB),
    ]
    for metric, value, warn_mb, guard_mb in checks:
        if value is None:
            continue
        if warn_mb and value >= warn_mb and _remember_memory_warning(job_id, f"{script_name}:{metric}:{warn_mb}"):
            log_message(job_id, f"⚠️ [mem] {phase} {script_name} {metric}={value:.1f}MB exceeds warn threshold {warn_mb}MB")
            _touch_last_log_at(job_id, job_dir, force=True)
        if guard_mb and value >= guard_mb:
            raise MemoryGuardError(_record_memory_failure(job_id, job_dir, script_name, phase, metric, value, guard_mb, snapshot))
    return snapshot


def _terminate_process_tree(proc: subprocess.Popen, *, grace_sec: float = 5.0) -> None:
    if proc.poll() is not None:
        return

    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass

    deadline = time.time() + grace_sec
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    if proc.poll() is None:
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def log_message(job_id: str, message: str) -> None:
    entry = f"[{_now()}] {message}"
    print(entry, flush=True)
    _append_live_log(job_id, entry, persist=True)


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
    before_snapshot = _check_memory_guard(job_id, script_name, job_dir, phase="before")
    before_parent_rss = _memory_status(job_id, script_name, "before", before_snapshot)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["JOB_ID"] = job_id  # ✅ critical: Step 11 can rely on this anywhere
    for worker_env_name in PIPELINE_WORKER_ENV_VARS:
        env.setdefault(worker_env_name, str(DEFAULT_STAGE_WORKER_CAPS.get(worker_env_name, PIPELINE_MAX_WORKERS)))
    log_message(
        job_id,
        f"🧵 Worker caps for {script_name}: pipeline_max={PIPELINE_MAX_WORKERS} "
        + " ".join(f"{name}={env.get(name)}" for name in PIPELINE_WORKER_ENV_VARS),
    )

    # python -u forces unbuffered output (so logs stream)
    cmd = [PYTHON_BIN, "-u", script_name] + args

    start = time.time()
    last_output = time.time()
    no_output_timeout_sec = (
        no_output_timeout_sec
        if no_output_timeout_sec is not None
        else NO_OUTPUT_TIMEOUT.get(script_name, NO_OUTPUT_TIMEOUT_DEFAULT)
    )
    log_message(job_id, f"⏱ No-output watchdog for {script_name}: {no_output_timeout_sec}s")

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
        start_new_session=(os.name != "nt"),
    ) as proc:
        peak_child_rss_mb = 0.0
        peak_dyno_mb = before_snapshot.get("dyno_mb") or 0.0
        peak_combined_mb = before_snapshot.get("combined_mb") or before_parent_rss
        selector = selectors.DefaultSelector()
        if proc.stdout is not None:
            selector.register(proc.stdout, selectors.EVENT_READ)
        last_memory_sample = 0.0
        last_running_log = 0.0
        try:
            while True:
                now = time.time()
                if timeout_sec is not None and (time.time() - start) > timeout_sec:
                    _terminate_process_tree(proc)
                    raise TimeoutError(f"{script_name} timed out after {timeout_sec}s")

                if no_output_timeout_sec is not None and (time.time() - last_output) > no_output_timeout_sec:
                    _terminate_process_tree(proc)
                    raise TimeoutError(f"{script_name} produced no output for >{no_output_timeout_sec}s (killed)")

                if proc.poll() is None and (now - last_memory_sample) >= MEMORY_SAMPLE_INTERVAL_SEC:
                    snapshot = _check_memory_guard(job_id, script_name, job_dir, phase="running", proc_pid=proc.pid)
                    peak_child_rss_mb = max(peak_child_rss_mb, snapshot.get("child_tree_mb") or 0.0)
                    peak_combined_mb = max(peak_combined_mb, snapshot.get("combined_mb") or 0.0)
                    peak_dyno_mb = max(peak_dyno_mb, snapshot.get("dyno_mb") or 0.0)
                    last_memory_sample = now
                    if (now - last_running_log) >= 5.0:
                        _memory_status(job_id, script_name, "running", snapshot)
                        last_running_log = now
                if proc.poll() is not None:
                    if proc.stdout is not None:
                        remainder = proc.stdout.read() or ""
                        for line in remainder.splitlines():
                            if line:
                                _append_live_log(job_id, line, persist=True)
                                _touch_last_log_at(job_id, job_dir)
                    break

                events = selector.select(timeout=1.0)
                if not events:
                    continue

                for _key, _mask in events:
                    line = proc.stdout.readline() if proc.stdout is not None else ""
                    if line == "":
                        continue
                    last_output = time.time()
                    line = line.rstrip("\n")
                    if line:
                        _append_live_log(job_id, line, persist=True)
                        _touch_last_log_at(job_id, job_dir)

            proc.wait()

        finally:
            try:
                selector.close()
            except Exception:
                pass
            if proc.poll() is None:
                _terminate_process_tree(proc)

    if proc.returncode != 0:
        raise RuntimeError(f"{script_name} failed with code {proc.returncode}")
    after_snapshot = _check_memory_guard(job_id, script_name, job_dir, phase="after")
    _memory_status(
        job_id,
        script_name,
        "after",
        after_snapshot,
        before_parent_mb=before_parent_rss,
        child_peak_mb=peak_child_rss_mb,
        dyno_peak_mb=peak_dyno_mb if peak_dyno_mb > 0 else None,
        combined_peak_mb=peak_combined_mb,
    )


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

    if MEMORY_WARN_MB and _current_rss_mb() >= MEMORY_WARN_MB:
        log_message(job_id, f"🧹 Cleanup packaging skipped because current RSS is already {_current_rss_mb():.1f}MB (warn={MEMORY_WARN_MB}MB)")
        return

    run_script_logged(
        job_id=job_id,
        script_name=CLEANUP_SCRIPT_NAME,
        args=cmd[3:],
        job_dir=job_dir,
        timeout_sec=CLEANUP_TIMEOUT_SEC,
        no_output_timeout_sec=CLEANUP_NO_OUTPUT_TIMEOUT_SEC,
    )

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

    required = {"pdb_id", "Chain", "Warhead", "Residue_ID"}
    try:
        df = pd.read_csv(ligand_atoms, dtype=str, usecols=list(required)).fillna("")
    except ValueError as exc:
        raise RuntimeError(f"Ligand_3D_Atoms.csv missing required SDF key columns: {sorted(required)}") from exc

    keys = []
    grouped = df.groupby(["pdb_id", "Chain", "Warhead", "Residue_ID"], dropna=False)
    for pdb, chain, ligand, resid in grouped.groups.keys():
        keys.append(row_sdf_key({"pdb_id": pdb, "Chain": chain, "Warhead": ligand, "Residue_ID": resid}))
    return sorted(set(keys))


def _list_nonempty_sdfs(sdf_dir: Path) -> List[Path]:
    files: List[Path] = []
    if not sdf_dir.exists():
        return files
    for fp in sorted(sdf_dir.glob("*.sdf")):
        try:
            if fp.is_file() and fp.stat().st_size > 0:
                files.append(fp)
        except Exception:
            continue
    return files


def _read_step11_sdf_failure_keys(job_path: Path, *, copied: bool) -> Dict[Tuple[str, str, str, str], str]:
    failure_path = (
        job_path / "TARGET_RESULTS" / "MCS_Output" / "Ligand_MCS_SDF_Failures.csv"
        if copied else
        job_path / "MCS_Output" / "Ligand_MCS_SDF_Failures.csv"
    )
    out: Dict[Tuple[str, str, str, str], str] = {}
    if not failure_path.exists():
        return out
    try:
        df = pd.read_csv(failure_path, dtype=str).fillna("")
    except Exception:
        return out
    required = {"pdb_id", "Chain", "Ligand", "Residue_ID"}
    if not required.issubset(df.columns):
        return out
    for _, row in df.iterrows():
        key = row_sdf_key(row.to_dict())
        if all(key):
            out[key] = str(row.get("Error") or "").strip()
    return out


def _residue_lookup_from_summary(job_path: Path) -> Dict[Tuple[str, str, str], str]:
    lookup: Dict[Tuple[str, str, str], str] = {}
    for filename in ["Ligand_3D_Atoms.csv", "Resolved_SASA_Summary.csv"]:
        path = _csv_path(job_path, filename)
        if path is None:
            continue
        try:
            df = pd.read_csv(path, dtype=str, usecols=["pdb_id", "Chain", "Warhead", "Residue_ID"]).fillna("")
        except ValueError:
            continue
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

    sdf_files = _list_nonempty_sdfs(sdf_dir)
    expected_keys = _expected_keys_from_ligand_atoms(job_path)
    failed_keys = _read_step11_sdf_failure_keys(job_path, copied=copied)
    if expected_keys and not sdf_files:
        raise RuntimeError(
            f"Required MCS SDF folder contains zero non-empty SDF files: {sdf_dir}. "
            f"Expected keys={len(expected_keys)} first_expected={expected_keys[:10]} "
            f"recorded_failures={len(failed_keys)}"
        )

    actual_names = {fp.name for fp in sdf_files}
    missing_unaccounted = []
    for key in expected_keys:
        expected_name = expected_mcs_sdf_filename(*key)
        if expected_name in actual_names:
            continue
        if key in failed_keys:
            continue
        missing_unaccounted.append(key)

    if missing_unaccounted:
        raise RuntimeError(
            f"MCS SDF checkpoint failed for {sdf_dir}: expected={len(expected_keys)} "
            f"sdf_files={len(sdf_files)} recorded_failures={len(failed_keys)} "
            f"first_missing={missing_unaccounted[:10]} "
            f"sample_files={[p.name for p in sdf_files[:10]]}"
        )

    label = "TARGET_RESULTS/MCS_Output/MCS_SDF" if copied else "MCS_Output/MCS_SDF"
    log_message(
        job_id,
        f"✅ SDF validation PASS after {'12_Results.py' if copied else '11_mcsMatcher.py'}: "
        f"{label} files={len(sdf_files)} expected={len(expected_keys)} recorded_failures={len(failed_keys)}"
    )


def validate_required_display_artifacts(job_id: str, job_dir: str) -> None:
    job_path = Path(job_dir)
    results_path = _csv_path(job_path, "Results_Display.csv")
    if results_path is None:
        raise RuntimeError("Required display artifact missing: Results_Display.csv")

    try:
        results = pd.read_csv(results_path, dtype=str, usecols=["pdb_id", "Chain", "Warhead", "Residue_ID"]).fillna("")
    except Exception as exc:
        raise RuntimeError(f"Could not read Results_Display.csv: {exc}") from exc

    if results.empty:
        raise RuntimeError("Results_Display.csv exists but has zero displayed rows")

    sdf_dir = job_path / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF"
    if not sdf_dir.exists():
        raise RuntimeError(f"Required copied MCS SDF folder missing: {sdf_dir}")

    sdf_files = _list_nonempty_sdfs(sdf_dir)
    if not sdf_files:
        raise RuntimeError(
            "Required copied MCS SDF folder contains zero non-empty SDF files. "
            f"Expected folder: {sdf_dir}. "
            f"First display keys: {[row_sdf_key(r.to_dict()) for _, r in results.head(10).iterrows()]}. "
            f"Actual TARGET_RESULTS files: {_list_target_result_files(job_path, limit=30)}"
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
            f"First missing keys={missing[:10]}, "
            f"Sample SDF files={[str(p.relative_to(job_path)) for p in sdf_files[:10]]}, "
            f"Actual TARGET_RESULTS files={_list_target_result_files(job_path, limit=30)}"
        )

    log_message(job_id, f"✅ final SDF validation PASS: rows={len(results)} matched={matched} sdf_files={len(sdf_files)} dir={sdf_dir}")


def _attempt_randy_backup(job_id: str, job_dir: str, *, status: str, required_result_tables: bool) -> Dict[str, Any]:
    backup_state = initial_backup_status(job_id)
    backup_state.update({
        "status": "skipped",
        "reason": backup_state.get("reason") or "",
        "started_at": _metadata_timestamp(),
    })
    write_job_metadata(job_id, {"backup": backup_state}, job_dir=job_dir)

    result = backup_job_directory(
        job_id,
        Path(job_dir),
        status=status,
        dry_run=False,
        log=lambda message: log_message(job_id, message),
    )
    result["provider"] = "randy"
    if status == "completed" and required_result_tables and result.get("status") == "uploaded_unverified":
        result["ok"] = False
        result["status"] = "failed_verification"
        result["reason"] = result.get("reason") or "uploaded_but_verification_incomplete"
    write_job_metadata(
        job_id,
        _backup_state_patch(result, results_ready=results_ready_from_disk(job_id)),
        job_dir=job_dir,
    )

    outcome = str(result.get("status") or "unknown")
    endpoint = str(result.get("endpoint") or result.get("base_url") or "")
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    verify_status = str(verification.get("status") or "not_attempted")
    if result.get("attempted"):
        summary = (
            f"configured={bool(result.get('configured'))} "
            f"ok={bool(result.get('ok'))} "
            f"status={outcome} "
            f"attempts={int(result.get('attempts') or 0)}/{int(result.get('max_attempts') or 0)} "
            f"verify={verify_status} "
            f"endpoint={endpoint or 'unconfigured'} "
            f"files={int(result.get('files') or result.get('uploaded_files') or 0)} "
            f"bytes={int(result.get('bytes') or result.get('uploaded_bytes') or 0)}"
        )
        if result.get("error"):
            summary += f" error={str(result.get('error'))}"
        log_message(job_id, f"🗄️ RANDY backup result: {summary}")
    else:
        reason = str(result.get("reason") or result.get("error") or "not configured")
        log_message(
            job_id,
            f"🗄️ RANDY backup skipped: {reason} endpoint={endpoint or 'unconfigured'} "
            f"configured={bool(result.get('configured'))}",
        )
    return result

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
        "backup": initial_backup_status(job_id),
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
        _check_memory_guard(job_id, "pipeline", job_dir, phase="startup")

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
                    no_output_timeout_sec=None,
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

        validate_required_display_artifacts(job_id, job_dir)

        with JOB_LOCK:
            JOB_STORE[job_id]["status"] = "completed"
            JOB_STORE[job_id]["finished_at"] = _timestamp()
            JOB_STORE[job_id]["current_step"] = ""
        write_job_metadata(job_id, {
            "status": "completed",
            "finished_at": _timestamp(),
            "current_step": "",
            "error": None,
            "results_ready": True,
            "archive_status": "backup_pending" if backup_on_complete() else "backup_skipped",
            "results_available_not_backed_up": bool(backup_on_complete()),
        }, job_dir=job_dir)
        _touch_last_log_at(job_id, job_dir, force=True)

        backup_result: Optional[Dict[str, Any]] = None
        if backup_on_complete():
            backup_result = _attempt_randy_backup(
                job_id,
                job_dir,
                status="completed",
                required_result_tables=results_ready_from_disk(job_id),
            )
            if archive_required() and not backup_result.get("ok"):
                raise RuntimeError(
                    f"RANDY backup required but failed after successful pipeline run: {backup_result.get('error') or backup_result.get('status') or 'unknown backup failure'}"
                )
        else:
            write_job_metadata(job_id, {
                **_backup_state_patch(
                    {
                        **initial_backup_status(job_id, reason="WARHEAD_BACKUP_ON_COMPLETE=0"),
                        "status": "skipped",
                    },
                    results_ready=True,
                )
            }, job_dir=job_dir)
            log_message(job_id, "🗄️ RANDY backup skipped: WARHEAD_BACKUP_ON_COMPLETE=0")

        try:
            _run_cleanup_packaging(job_id, job_dir)
        except Exception as cleanup_error:
            log_message(job_id, f"⚠️ Cleanup packaging step failed: {cleanup_error}")

        if backup_result and backup_result.get("ok"):
            log_message(job_id, "✅ PIPELINE FINISHED SUCCESSFULLY — RANDY backup verified.")
        elif backup_on_complete():
            log_message(job_id, "⚠️ PIPELINE FINISHED; results are available, but RANDY backup failed.")
        else:
            log_message(job_id, "✅ PIPELINE FINISHED SUCCESSFULLY")
        log_message(job_id, "Access results in the Browse tab.")

    except Exception as e:
        existing_meta = {}
        try:
            meta_path = _job_metadata_path(job_dir)
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as handle:
                    existing_meta = json.load(handle) or {}
        except Exception:
            existing_meta = {}
        memory_failure = existing_meta.get("memory_failure") if isinstance(existing_meta, dict) else None
        with JOB_LOCK:
            JOB_STORE[job_id]["status"] = "failed"
            JOB_STORE[job_id]["finished_at"] = _timestamp()
            JOB_STORE[job_id]["current_step"] = ""
        write_job_metadata(job_id, {
            "status": "failed",
            "finished_at": _timestamp(),
            "current_step": "",
            "error": {
                "message": str(e),
            },
            "results_ready": results_ready_from_disk(job_id),
            "memory_failure": memory_failure,
        }, job_dir=job_dir)

        log_message(job_id, f"❌ CRITICAL ERROR: {str(e)}")
        _touch_last_log_at(job_id, job_dir, force=True)
        if backup_on_failure():
            backup_result = _attempt_randy_backup(
                job_id,
                job_dir,
                status="failed",
                required_result_tables=False,
            )
            if archive_required() and not backup_result.get("ok"):
                write_job_metadata(job_id, {
                    "error": {
                        "message": (
                            f"{str(e)}; RANDY backup required but failed: "
                            f"{backup_result.get('error') or backup_result.get('status') or 'unknown backup failure'}"
                        )
                    }
                }, job_dir=job_dir)
        else:
            write_job_metadata(job_id, {
                **_backup_state_patch(
                    {
                        **initial_backup_status(job_id, reason="WARHEAD_BACKUP_ON_FAILURE=0"),
                        "status": "skipped",
                    },
                    results_ready=results_ready_from_disk(job_id),
                )
            }, job_dir=job_dir)


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
            "log_line_count": 0,
            "log_truncated": False,
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

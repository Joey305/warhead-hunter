from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_JOBS_ROOT = APP_ROOT / "jobs"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_jobs_root(jobs_root: Optional[Path | str] = None) -> Path:
    root = Path(jobs_root) if jobs_root is not None else DEFAULT_JOBS_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_job_id(job_id: str) -> bool:
    if not job_id:
        return False
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        return False
    return True


def job_dir_for(job_id: str, jobs_root: Optional[Path | str] = None) -> Path:
    if not safe_job_id(job_id):
        raise ValueError(f"Invalid job_id: {job_id!r}")
    root = get_jobs_root(jobs_root).resolve()
    job_dir = (root / job_id).resolve()
    if not str(job_dir).startswith(str(root)):
        raise ValueError(f"Unsafe job_id path: {job_id!r}")
    return job_dir


def target_results_dir(job_id: str, jobs_root: Optional[Path | str] = None) -> Path:
    return job_dir_for(job_id, jobs_root) / "TARGET_RESULTS"


def job_metadata_path(job_id: str, jobs_root: Optional[Path | str] = None) -> Path:
    return job_dir_for(job_id, jobs_root) / "job_metadata.json"


def job_log_path(job_id: str, jobs_root: Optional[Path | str] = None) -> Path:
    return job_dir_for(job_id, jobs_root) / "job.log"


def job_exists_on_disk(job_id: str, jobs_root: Optional[Path | str] = None) -> bool:
    try:
        return job_dir_for(job_id, jobs_root).exists()
    except ValueError:
        return False


def load_job_metadata(job_id: str, jobs_root: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    fp = job_metadata_path(job_id, jobs_root)
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_job_metadata(
    job_id: str,
    updates: Dict[str, Any],
    jobs_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    job_dir = job_dir_for(job_id, jobs_root)
    job_dir.mkdir(parents=True, exist_ok=True)
    fp = job_metadata_path(job_id, jobs_root)
    data = load_job_metadata(job_id, jobs_root) or {}
    data.update(updates or {})
    data["job_id"] = job_id
    data["job_dir"] = str(job_dir)
    data["updated_at"] = utc_now_iso()
    data.setdefault("created_at", utc_now_iso())
    data.setdefault("error", None)
    fp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return data


def load_job_log_lines(job_id: str, jobs_root: Optional[Path | str] = None) -> List[str]:
    fp = job_log_path(job_id, jobs_root)
    if not fp.exists():
        return []
    try:
        return fp.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []


def append_job_log(job_id: str, line: str, jobs_root: Optional[Path | str] = None) -> Path:
    job_dir = job_dir_for(job_id, jobs_root)
    job_dir.mkdir(parents=True, exist_ok=True)
    fp = job_log_path(job_id, jobs_root)
    with fp.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")
    write_job_metadata(job_id, {"last_log_at": utc_now_iso()}, jobs_root=jobs_root)
    return fp


def _protein_data_row(job_id: str, jobs_root: Optional[Path | str] = None) -> Dict[str, str]:
    fp = job_dir_for(job_id, jobs_root) / "Protein_Data.csv"
    if not fp.exists():
        return {}
    try:
        with fp.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.DictReader(handle)
            row = next(reader, None) or {}
        return {str(k): str(v or "") for k, v in row.items()}
    except Exception:
        return {}


def results_ready_from_disk(job_id: str, jobs_root: Optional[Path | str] = None) -> bool:
    job_dir = job_dir_for(job_id, jobs_root)
    candidates = [
        job_dir / "TARGET_RESULTS" / "Results_Display.csv",
        job_dir / "Results_Display.csv",
        job_dir / "TARGET_RESULTS" / "Resolved_SASA_Summary.csv",
        job_dir / "Resolved_SASA_Summary.csv",
    ]
    for fp in candidates:
        if fp.exists() and fp.is_file():
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                text = ""
            if len(text.strip().splitlines()) > 1:
                return True
    return False


def infer_job_status_from_disk(job_id: str, jobs_root: Optional[Path | str] = None) -> str:
    meta = load_job_metadata(job_id, jobs_root) or {}
    status = str(meta.get("status") or "").strip().lower()
    if status:
        return status

    log_lines = load_job_log_lines(job_id, jobs_root)
    if any("PIPELINE FINISHED SUCCESSFULLY" in line for line in log_lines):
        return "completed"
    if any("CRITICAL ERROR" in line or " failed with code " in line for line in log_lines):
        return "failed"
    if results_ready_from_disk(job_id, jobs_root):
        return "completed"
    if log_lines:
        return "running"
    return "unknown"


def hydrate_job_from_disk(
    job_id: str,
    jobs_root: Optional[Path | str] = None,
    live_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not job_exists_on_disk(job_id, jobs_root):
        return None

    meta = load_job_metadata(job_id, jobs_root) or {}
    protein_row = _protein_data_row(job_id, jobs_root)
    live = dict(live_state or {})

    target = (
        str(meta.get("target") or "").strip()
        or str((meta.get("request") or {}).get("target_name") or "").strip()
        or str(protein_row.get("protein") or "").strip()
    )
    status = infer_job_status_from_disk(job_id, jobs_root)
    current_step = str(meta.get("current_step") or "").strip()
    if not current_step and status in {"queued", "pending", "running"}:
        current_step = str(live.get("current_step") or "").strip()

    log_lines = load_job_log_lines(job_id, jobs_root)
    ready = results_ready_from_disk(job_id, jobs_root)

    hydrated = {
        "job_id": job_id,
        "target": target,
        "status": status,
        "created_at": meta.get("created_at") or "",
        "started_at": meta.get("started_at") or "",
        "finished_at": meta.get("finished_at") or "",
        "current_step": current_step,
        "step_started_at": meta.get("step_started_at") or "",
        "last_log_at": meta.get("last_log_at") or "",
        "error": meta.get("error"),
        "job_dir": str(job_dir_for(job_id, jobs_root)),
        "results_ready": ready,
        "log": log_lines,
        "request": meta.get("request") or {},
        "outputs": meta.get("outputs") or {},
    }
    patch = {}
    for key in [
        "target",
        "status",
        "job_dir",
        "results_ready",
        "current_step",
        "step_started_at",
        "created_at",
        "started_at",
        "finished_at",
        "last_log_at",
        "error",
    ]:
        if meta.get(key) != hydrated.get(key):
            patch[key] = hydrated.get(key)
    if patch:
        write_job_metadata(job_id, patch, jobs_root=jobs_root)
    return hydrated

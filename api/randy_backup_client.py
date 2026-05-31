#!/usr/bin/env python3
from __future__ import annotations

import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import quote

import requests

from api import randy_archive_client


DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_BYTES = 300_000_000
SAFE_SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "_randy_backup",
}
SAFE_SKIP_FILES = {
    ".ds_store",
    "thumbs.db",
}
PREFERRED_ROOTS = [
    "TARGET_RESULTS",
    "WAR_PDB",
    "MCS_Output",
    "bundles",
]
PREFERRED_FILES = [
    "job_metadata.json",
    "job.log",
    "Results_Display.csv",
    "Resolved_SASA_Summary.csv",
    "Resolved_SASA_Summary.tsv",
    "Warhead_SASA_atoms.csv",
    "Ligand_3D_Atoms.csv",
    "Ligand_3D_Atoms_with_SASA.csv",
    "3DSASAmapped.csv",
    "Ligand_Metadata.csv",
    "Protein_Data.csv",
    "job_result_manifest.json",
    "cleanup_report.md",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _configured_token() -> str:
    return (
        os.environ.get("RANDY_BACKUP_TOKEN", "").strip()
        or os.environ.get("RANDY_ARCHIVE_TOKEN", "").strip()
        or os.environ.get("WARHEAD_HANDOFF_TOKEN", "").strip()
        or os.environ.get("PROTAC_BACKUP_TOKEN", "").strip()
    )


def _configured_base_url() -> str:
    for raw in [
        os.environ.get("RANDY_BACKUP_BASE_URL", ""),
        os.environ.get("RANDY_ARCHIVE_BASE_URL", ""),
    ]:
        value = str(raw or "").strip().rstrip("/")
        if not value:
            continue
        if value.endswith("/backup"):
            return value
        return f"{value}/backup"

    handoff_storage = str(os.environ.get("WARHEAD_HANDOFF_STORAGE_URL", "") or "").strip().rstrip("/")
    if handoff_storage.endswith("/backup/hunter-job-files"):
        return handoff_storage[: -len("/hunter-job-files")]
    return ""


def backup_enabled() -> bool:
    return bool(_configured_base_url() and _configured_token())


def backup_endpoint() -> str:
    base = _configured_base_url()
    return f"{base}/hunter-job-archive" if base else ""


def backup_timeout_seconds() -> int:
    return max(5, _env_int("WARHEAD_BACKUP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))


def backup_max_bytes() -> int:
    return max(1_000_000, _env_int("WARHEAD_BACKUP_MAX_BYTES", DEFAULT_MAX_BYTES))


def archive_required() -> bool:
    return _env_flag("WARHEAD_BACKUP_REQUIRED", False)


def backup_on_complete() -> bool:
    return _env_flag("WARHEAD_BACKUP_ON_COMPLETE", True)


def backup_on_failure() -> bool:
    return _env_flag("WARHEAD_BACKUP_ON_FAILURE", True)


def _headers() -> Dict[str, str]:
    token = _configured_token()
    headers = {"User-Agent": "warhead-hunter-randy-backup-client/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _safe_rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _skip_dir_name(name: str) -> bool:
    low = str(name or "").strip().lower()
    return (
        not low
        or low in SAFE_SKIP_DIRS
        or low.startswith(".tox")
        or low.startswith(".nox")
    )


def _skip_file(path: Path) -> bool:
    name = path.name.lower()
    if name in SAFE_SKIP_FILES:
        return True
    if name.startswith("._"):
        return True
    if path.suffix.lower() in {".pyc", ".pyo"}:
        return True
    return False


def _preferred_file(path: Path, rel: str) -> bool:
    rel_low = rel.lower()
    if any(rel_low == item.lower() for item in PREFERRED_FILES):
        return True
    return any(rel_low.startswith(f"{root.lower()}/") for root in PREFERRED_ROOTS)


@dataclass
class CandidateFile:
    path: Path
    rel: str
    size_bytes: int
    preferred: bool


def iter_backup_candidates(job_dir: Path) -> Iterator[CandidateFile]:
    root = job_dir.resolve()
    for current_root, dirnames, filenames in os.walk(root):
        current = Path(current_root)
        dirnames[:] = [
            name
            for name in dirnames
            if not _skip_dir_name(name)
        ]
        for filename in filenames:
            path = current / filename
            if not path.is_file() or _skip_file(path):
                continue
            try:
                rel = _safe_rel(path, root)
                size = path.stat().st_size
            except Exception:
                continue
            yield CandidateFile(
                path=path,
                rel=rel,
                size_bytes=size,
                preferred=_preferred_file(path, rel),
            )


def build_backup_plan(job_dir: Path, *, max_bytes: Optional[int] = None) -> Dict[str, Any]:
    max_bytes = int(max_bytes or backup_max_bytes())
    preferred: List[CandidateFile] = []
    other: List[CandidateFile] = []
    skipped_count = 0
    skipped_bytes = 0

    for item in iter_backup_candidates(job_dir):
        if item.preferred:
            preferred.append(item)
        else:
            other.append(item)

    selected: List[CandidateFile] = []
    selected_bytes = 0
    for group in [preferred, other]:
        for item in sorted(group, key=lambda rec: rec.rel):
            if selected_bytes + item.size_bytes > max_bytes:
                skipped_count += 1
                skipped_bytes += item.size_bytes
                continue
            selected.append(item)
            selected_bytes += item.size_bytes

    required_names = {
        "job_metadata.json",
        "job.log",
    }
    selected_names = {item.rel for item in selected}
    for required_name in required_names:
        if required_name not in selected_names and (job_dir / required_name).exists():
            return {
                "ok": False,
                "reason": f"Required backup file could not fit within WARHEAD_BACKUP_MAX_BYTES: {required_name}",
                "selected_files": selected,
                "selected_file_count": len(selected),
                "selected_bytes": selected_bytes,
                "skipped_file_count": skipped_count,
                "skipped_bytes": skipped_bytes,
                "curated_only": skipped_count > 0,
                "max_bytes": max_bytes,
            }

    return {
        "ok": True,
        "selected_files": selected,
        "selected_file_count": len(selected),
        "selected_bytes": selected_bytes,
        "skipped_file_count": skipped_count,
        "skipped_bytes": skipped_bytes,
        "curated_only": skipped_count > 0,
        "max_bytes": max_bytes,
    }


def _create_archive(job_id: str, job_dir: Path, plan: Dict[str, Any]) -> Path:
    fd, archive_name = tempfile.mkstemp(prefix=f"{job_id}_randy_backup_", suffix=".zip")
    os.close(fd)
    archive_path = Path(archive_name)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for item in plan.get("selected_files", []):
            zf.write(item.path, arcname=item.rel)
    return archive_path


def _verify_archive(job_id: str) -> Dict[str, Any]:
    randy_archive_client.reset_job_cache(job_id)
    exists = randy_archive_client.job_exists(job_id)
    detail = randy_archive_client.get_job_index(job_id) if exists else None
    status = {
        "job_exists": bool(exists),
        "table_ok": False,
        "artifact_ok": False,
        "table_path": "",
    }
    if not detail:
        return status

    table_df = randy_archive_client.get_table_dataframe(job_id, ["Results_Display.csv"])
    table_diag = randy_archive_client.last_table_diagnostic()
    status["table_ok"] = bool(table_df is not None and not table_df.empty)
    if isinstance(table_diag, dict):
        status["table_path"] = str(table_diag.get("resolved_path") or "")

    options = detail.get("options") if isinstance(detail.get("options"), list) else []
    if options:
        first = next((item for item in options if isinstance(item, dict)), None)
        if first:
            pdb = str(first.get("pdb") or "").strip()
            chain = str(first.get("chain") or "").strip()
            ligand = str(first.get("ligand") or first.get("warhead") or "").strip()
            resid = str(first.get("resid") or "").strip()
            protein = randy_archive_client.find_protein_pdb_asset(job_id, pdb=pdb, chain=chain, ligand=ligand)
            sdf = randy_archive_client.find_asset(job_id, pdb=pdb, chain=chain, ligand=ligand, resid=resid, kind="sdf")
            status["artifact_ok"] = bool(protein and sdf)
    return status


def initial_backup_status(job_id: str, *, reason: str = "") -> Dict[str, Any]:
    configured = backup_enabled()
    return {
        "provider": "randy",
        "configured": configured,
        "attempted": False,
        "ok": False,
        "status": "pending" if configured else "skipped",
        "job_id": job_id,
        "remote_job_id": "",
        "uploaded_files": 0,
        "uploaded_bytes": 0,
        "archive_path": "",
        "error": None,
        "reason": reason or ("" if configured else "RANDY backup endpoint/token not configured"),
    }


def backup_job_directory(
    job_id: str,
    job_dir: Path | str,
    *,
    status: str = "completed",
    dry_run: bool = False,
) -> Dict[str, Any]:
    job_dir = Path(job_dir).resolve()
    result = initial_backup_status(job_id)
    result["started_at"] = _utc_now_iso()
    result["status"] = "skipped"

    if not job_dir.exists():
        result["error"] = "Local job directory does not exist"
        result["finished_at"] = _utc_now_iso()
        return result

    if not result["configured"]:
        result["finished_at"] = _utc_now_iso()
        return result

    result["status"] = "planning"
    plan = build_backup_plan(job_dir)
    result["selected_files"] = int(plan.get("selected_file_count") or 0)
    result["selected_bytes"] = int(plan.get("selected_bytes") or 0)
    result["curated_only"] = bool(plan.get("curated_only"))
    if not plan.get("ok"):
        result["attempted"] = False
        result["status"] = "failed_planning"
        result["error"] = str(plan.get("reason") or "Backup planning failed")
        result["finished_at"] = _utc_now_iso()
        return result

    if dry_run:
        result["status"] = "dry_run"
        result["ok"] = True
        result["finished_at"] = _utc_now_iso()
        return result

    endpoint = backup_endpoint()
    archive_path: Optional[Path] = None
    try:
        archive_path = _create_archive(job_id, job_dir, plan)
        archive_size = archive_path.stat().st_size
        result["attempted"] = True
        result["status"] = "uploading"
        result["uploaded_files"] = int(plan.get("selected_file_count") or 0)
        result["uploaded_bytes"] = archive_size

        with archive_path.open("rb") as handle:
            resp = requests.post(
                endpoint,
                headers=_headers(),
                data={
                    "job_id": job_id,
                    "source": "warhead-hunter-job-runner",
                    "status": status,
                },
                files={
                    "archive": (f"{job_id}_warhead_hunter_full_job.zip", handle, "application/zip"),
                },
                timeout=backup_timeout_seconds(),
            )
        result["status_code"] = int(resp.status_code)
        try:
            payload = resp.json()
        except Exception:
            payload = {"ok": False, "error": resp.text[:500]}
        result["remote_job_id"] = str(payload.get("job_id") or job_id)
        result["archive_path"] = str(payload.get("archive_file") or "")
        if resp.status_code >= 400 or not payload.get("ok"):
            result["status"] = "upload_failed"
            result["error"] = f"HTTP {resp.status_code}: {str(payload.get('error') or 'upload failed')[:240]}"
            result["finished_at"] = _utc_now_iso()
            return result

        verify = _verify_archive(job_id)
        result["verification"] = verify
        if verify.get("job_exists") and (verify.get("table_ok") or status != "completed"):
            result["ok"] = True
            result["status"] = "completed"
        elif verify.get("job_exists"):
            result["ok"] = True
            result["status"] = "uploaded_unverified"
            result["error"] = "Archive uploaded but result-table verification did not succeed"
        else:
            result["ok"] = False
            result["status"] = "failed_verification"
            result["error"] = "Archive upload completed but RANDY verification could not find the job"
        result["finished_at"] = _utc_now_iso()
        return result
    except Exception as exc:
        result["attempted"] = True
        result["status"] = "exception"
        result["error"] = str(exc)[:240]
        result["finished_at"] = _utc_now_iso()
        return result
    finally:
        if archive_path is not None:
            try:
                archive_path.unlink(missing_ok=True)
            except Exception:
                pass


def backup_detail_url(job_id: str) -> str:
    base = _configured_base_url()
    return f"{base}/hunter-job/{quote(job_id, safe='')}" if base else ""

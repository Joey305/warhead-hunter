#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.parse import quote, urlsplit

import requests
from requests import RequestException
from urllib3.util import Timeout as Urllib3Timeout

from api import randy_archive_client


DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_CONNECT_TIMEOUT_SECONDS = 25.0
DEFAULT_READ_TIMEOUT_SECONDS = 20 * 60.0
DEFAULT_UPLOAD_TIMEOUT_SECONDS = 20 * 60.0
DEFAULT_TOTAL_TIMEOUT_SECONDS = 30 * 60.0
DEFAULT_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 15.0
DEFAULT_MAX_BYTES = 300_000_000
RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
TIMEOUT_ENV_NAMES = {
    "connect_timeout": ["RANDY_BACKUP_CONNECT_TIMEOUT", "WARHEAD_BACKUP_CONNECT_TIMEOUT_SECONDS"],
    "read_timeout": [
        "RANDY_BACKUP_READ_TIMEOUT",
        "RANDY_BACKUP_UPLOAD_TIMEOUT",
        "WARHEAD_BACKUP_READ_TIMEOUT_SECONDS",
        "WARHEAD_BACKUP_TIMEOUT_SECONDS",
    ],
    "upload_timeout": [
        "RANDY_BACKUP_UPLOAD_TIMEOUT",
        "RANDY_BACKUP_READ_TIMEOUT",
        "WARHEAD_BACKUP_UPLOAD_TIMEOUT_SECONDS",
        "WARHEAD_BACKUP_TIMEOUT_SECONDS",
    ],
    "total_timeout": ["RANDY_BACKUP_TOTAL_TIMEOUT", "WARHEAD_BACKUP_TOTAL_TIMEOUT_SECONDS"],
    "retries": ["RANDY_BACKUP_RETRIES", "WARHEAD_BACKUP_RETRIES"],
    "retry_backoff": ["RANDY_BACKUP_RETRY_BACKOFF_SECONDS", "WARHEAD_BACKUP_RETRY_BACKOFF_SECONDS"],
}
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
    "archives",
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


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _env_float_aliases(names: List[str], default: float) -> float:
    for name in names:
        raw = str(os.environ.get(name, "") or "").strip()
        if not raw:
            continue
        try:
            return float(raw)
        except Exception:
            continue
    return default


def _configured_token() -> str:
    return (
        os.environ.get("RANDY_BACKUP_TOKEN", "").strip()
        or os.environ.get("RANDY_ARCHIVE_TOKEN", "").strip()
        or os.environ.get("WARHEAD_HANDOFF_TOKEN", "").strip()
        or os.environ.get("PROTAC_BACKUP_TOKEN", "").strip()
    )


def _token_present() -> bool:
    return bool(_configured_token())


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


def _base_url_host_path() -> str:
    return _configured_base_url()


def backup_enabled() -> bool:
    return bool(_configured_base_url() and _configured_token())


def backup_endpoint() -> str:
    base = _configured_base_url()
    return f"{base}/hunter-job-archive" if base else ""


def backup_timeout_seconds() -> int:
    return int(round(backup_timeout_config()["read_timeout_seconds"]))


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


def backup_timeout_config() -> Dict[str, float]:
    legacy_timeout = max(5.0, _env_float("WARHEAD_BACKUP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    connect_timeout = max(
        1.0,
        _env_float_aliases(
            TIMEOUT_ENV_NAMES["connect_timeout"],
            DEFAULT_CONNECT_TIMEOUT_SECONDS,
        ),
    )
    read_timeout = max(
        5.0,
        _env_float_aliases(
            TIMEOUT_ENV_NAMES["read_timeout"],
            max(DEFAULT_READ_TIMEOUT_SECONDS, legacy_timeout),
        ),
    )
    upload_timeout = max(
        5.0,
        _env_float_aliases(
            TIMEOUT_ENV_NAMES["upload_timeout"],
            max(DEFAULT_UPLOAD_TIMEOUT_SECONDS, read_timeout),
        ),
    )
    total_timeout = max(
        connect_timeout,
        _env_float_aliases(
            TIMEOUT_ENV_NAMES["total_timeout"],
            max(DEFAULT_TOTAL_TIMEOUT_SECONDS, connect_timeout, read_timeout, upload_timeout),
        ),
    )
    return {
        "connect_timeout_seconds": connect_timeout,
        "read_timeout_seconds": max(read_timeout, upload_timeout),
        "upload_timeout_seconds": upload_timeout,
        "total_timeout_seconds": max(total_timeout, connect_timeout, read_timeout, upload_timeout),
    }


def backup_retry_config() -> Dict[str, float | int]:
    retries = max(
        0,
        _env_int("RANDY_BACKUP_RETRIES", _env_int("WARHEAD_BACKUP_RETRIES", DEFAULT_RETRIES)),
    )
    return {
        "retries": retries,
        "max_attempts": retries + 1,
        "retry_backoff_seconds": max(
            0.0,
            _env_float_aliases(
                ["RANDY_BACKUP_RETRY_BACKOFF_SECONDS", "WARHEAD_BACKUP_RETRY_BACKOFF_SECONDS"],
                DEFAULT_RETRY_BACKOFF_SECONDS,
            ),
        ),
    }


def _timeout_model_summary() -> Dict[str, Any]:
    # requests/urllib3 supports connect, read, and total deadlines here.
    # Large multipart uploads can still fail during socket writes or proxy
    # buffering, so RANDY_BACKUP_UPLOAD_TIMEOUT is preserved as a separate knob
    # for configuration clarity and is folded into the effective request window.
    return {
        "client_library": "requests+urllib3",
        "supports_connect_timeout": True,
        "supports_read_timeout": True,
        "supports_total_timeout": True,
        "supports_dedicated_write_timeout": False,
        "upload_timeout_behavior": (
            "RANDY_BACKUP_UPLOAD_TIMEOUT is treated as the intended upload/read window. "
            "Python requests does not expose a separate socket write timeout knob, so large "
            "archive uploads can still fail as connection/write/read/total timeouts depending "
            "on proxy buffering and receiver behavior."
        ),
    }


def backup_configuration_summary() -> Dict[str, Any]:
    base_url = _base_url_host_path()
    endpoint = backup_endpoint()
    timeout_cfg = backup_timeout_config()
    retry_cfg = backup_retry_config()
    return {
        "provider": "randy",
        "configured": bool(base_url and _token_present()),
        "base_url": base_url,
        "endpoint": endpoint,
        "endpoint_host_path": _endpoint_host_path(endpoint),
        "token_present": _token_present(),
        "backup_on_complete": backup_on_complete(),
        "backup_on_failure": backup_on_failure(),
        "archive_required": archive_required(),
        "timeout_seconds": int(round(timeout_cfg["read_timeout_seconds"])),
        **timeout_cfg,
        **retry_cfg,
        "max_bytes": backup_max_bytes(),
        "timeout_model": _timeout_model_summary(),
    }


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


def _emit(log: Optional[Callable[[str], None]], message: str) -> None:
    if log is not None:
        log(message)


def _endpoint_host_path(endpoint: str) -> str:
    if not endpoint:
        return ""
    parsed = urlsplit(endpoint)
    return f"{parsed.netloc}{parsed.path}" if parsed.netloc else endpoint


def _request_timeout_object() -> Urllib3Timeout:
    cfg = backup_timeout_config()
    return Urllib3Timeout(
        connect=cfg["connect_timeout_seconds"],
        read=cfg["read_timeout_seconds"],
        total=cfg["total_timeout_seconds"],
    )


def _error_text(exc: BaseException) -> str:
    parts = [str(exc).strip()]
    reason = getattr(exc, "reason", None)
    if reason is not None:
        reason_text = str(reason).strip()
        if reason_text and reason_text not in parts:
            parts.append(reason_text)
    return " | ".join(part for part in parts if part)[:240]


def _exception_is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError, socket.timeout, TimeoutError, ConnectionError)):
        return True
    text = _error_text(exc).lower()
    retry_fragments = [
        "timed out",
        "timeout",
        "connection aborted",
        "connection reset",
        "temporarily unavailable",
        "temporary failure",
        "name or service not known",
        "nodename nor servname provided",
        "remote disconnected",
        "broken pipe",
        "network is unreachable",
    ]
    return any(fragment in text for fragment in retry_fragments)


def _planning_failure_status(reason: str) -> str:
    text = str(reason or "").lower()
    if "could not fit within warhead_backup_max_bytes" in text:
        return "archive_too_large"
    return "failed_planning"


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
        "ok": False,
        "status": "job_not_found",
        "job_exists": bool(exists),
        "table_ok": False,
        "artifact_ok": False,
        "table_path": "",
        "checked_at": _utc_now_iso(),
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
    if status["job_exists"] and status["table_ok"]:
        status["ok"] = True
        status["status"] = "verified"
    elif status["job_exists"]:
        status["status"] = "uploaded_unverified"
    return status


def initial_backup_status(job_id: str, *, reason: str = "") -> Dict[str, Any]:
    config = backup_configuration_summary()
    configured = bool(config["configured"])
    return {
        **config,
        "configured": configured,
        "attempted": False,
        "ok": False,
        "status": "pending" if configured else "skipped",
        "job_id": job_id,
        "remote_job_id": "",
        "remote_archive_path": "",
        "archive_path": "",
        "attempts": 0,
        "attempt_details": [],
        "uploaded_files": 0,
        "uploaded_bytes": 0,
        "files": 0,
        "bytes": 0,
        "started_at": "",
        "finished_at": "",
        "verify": "not_attempted",
        "verification": {
            "ok": False,
            "status": "not_attempted",
            "job_exists": False,
            "table_ok": False,
            "artifact_ok": False,
            "table_path": "",
        },
        "error": None,
        "reason": reason or ("" if configured else "RANDY backup endpoint/token not configured"),
    }


def backup_job_directory(
    job_id: str,
    job_dir: Path | str,
    *,
    status: str = "completed",
    dry_run: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    job_dir = Path(job_dir).resolve()
    result = initial_backup_status(job_id)
    timeout_cfg = backup_timeout_config()
    retry_cfg = backup_retry_config()
    endpoint = backup_endpoint()
    endpoint_host_path = _endpoint_host_path(endpoint)
    max_attempts = int(retry_cfg["max_attempts"])
    retry_backoff = float(retry_cfg["retry_backoff_seconds"])

    result["started_at"] = _utc_now_iso()
    result["status"] = "skipped"
    result["reason"] = result.get("reason") or ""
    result["endpoint_host_path"] = endpoint_host_path

    if not job_dir.exists():
        result["attempted"] = False
        result["configured"] = backup_enabled()
        result["status"] = "failed_local_job_missing"
        result["error"] = "Local job directory does not exist"
        result["reason"] = "local_job_directory_missing"
        result["finished_at"] = _utc_now_iso()
        return result

    result["status"] = "planning"
    plan = build_backup_plan(job_dir)
    result["selected_files"] = int(plan.get("selected_file_count") or 0)
    result["selected_bytes"] = int(plan.get("selected_bytes") or 0)
    result["curated_only"] = bool(plan.get("curated_only"))
    result["files"] = int(plan.get("selected_file_count") or 0)
    result["bytes"] = int(plan.get("selected_bytes") or 0)
    if not plan.get("ok"):
        result["attempted"] = False
        result["status"] = _planning_failure_status(str(plan.get("reason") or ""))
        result["error"] = str(plan.get("reason") or "Backup planning failed")
        result["reason"] = "backup_planning_failed"
        result["finished_at"] = _utc_now_iso()
        return result

    if dry_run:
        result["status"] = "dry_run"
        result["ok"] = True
        result["reason"] = "dry_run_only"
        result["finished_at"] = _utc_now_iso()
        return result

    if not result["configured"]:
        result["attempted"] = False
        result["status"] = "skipped_not_configured"
        result["reason"] = result.get("reason") or "RANDY backup endpoint/token not configured"
        result["finished_at"] = _utc_now_iso()
        return result

    archive_path: Optional[Path] = None
    try:
        archive_path = _create_archive(job_id, job_dir, plan)
        archive_size = archive_path.stat().st_size
        result["uploaded_files"] = int(plan.get("selected_file_count") or 0)
        result["uploaded_bytes"] = archive_size
        result["files"] = int(plan.get("selected_file_count") or 0)
        result["bytes"] = archive_size

        last_error: Optional[str] = None
        last_reason = ""
        last_status = "request_exception"
        last_verification = result["verification"]

        for attempt in range(1, max_attempts + 1):
            result["attempted"] = True
            result["attempts"] = attempt
            attempt_info: Dict[str, Any] = {
                "attempt": attempt,
                "max_attempts": max_attempts,
                "started_at": _utc_now_iso(),
                "endpoint": endpoint,
                "endpoint_host_path": endpoint_host_path,
                "files": result["files"],
                "bytes": result["bytes"],
                **timeout_cfg,
            }
            _emit(
                log,
                (
                    f"🗄️ RANDY backup attempt {attempt}/{max_attempts}: "
                    f"endpoint={endpoint_host_path or 'unconfigured'} "
                    f"files={result['files']} bytes={result['bytes']} "
                    f"connect={timeout_cfg['connect_timeout_seconds']}s "
                    f"read={timeout_cfg['read_timeout_seconds']}s "
                    f"upload={timeout_cfg['upload_timeout_seconds']}s "
                    f"total={timeout_cfg['total_timeout_seconds']}s"
                ),
            )
            try:
                # "Quick internet" is not enough for archive backup uploads.
                # A 70-300 MB multipart ZIP still has to finish TLS writes,
                # proxy buffering, remote disk/extract work, and verification.
                # requests only gives us connect/read/total timeouts here, so
                # write-side stalls often surface as ConnectionError/Timeout.
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
                        timeout=_request_timeout_object(),
                    )
                attempt_info["status_code"] = int(resp.status_code)
                try:
                    payload = resp.json()
                except Exception:
                    payload = {"ok": False, "error": resp.text[:500]}

                result["status_code"] = int(resp.status_code)
                result["remote_job_id"] = str(payload.get("job_id") or job_id)
                result["remote_archive_path"] = str(payload.get("archive_file") or "")
                result["archive_path"] = result["remote_archive_path"]
                attempt_info["remote_job_id"] = result["remote_job_id"]
                attempt_info["remote_archive_path"] = result["remote_archive_path"]

                if resp.status_code >= 400 or not payload.get("ok"):
                    http_status = int(resp.status_code)
                    error_text = str(payload.get("error") or f"HTTP {http_status} upload failed")[:240]
                    retryable = http_status in RETRYABLE_HTTP_STATUS_CODES
                    status_name = "auth_failed" if http_status in {401, 403} else "http_error"
                    reason_name = "upload_http_error"
                    attempt_info.update({
                        "ok": False,
                        "status": status_name,
                        "reason": reason_name,
                        "error": error_text,
                        "retryable": retryable,
                    })
                    result["attempt_details"].append(attempt_info)
                    last_error = f"HTTP {http_status}: {error_text}"[:240]
                    last_reason = reason_name
                    last_status = status_name
                    if retryable and attempt < max_attempts:
                        attempt_info["retry_delay_seconds"] = retry_backoff
                        _emit(
                            log,
                            f"🗄️ RANDY backup attempt {attempt}/{max_attempts} failed: "
                            f"status={status_name} status_code={http_status} retryable=True "
                            f"retry_delay={retry_backoff}s error={error_text}",
                        )
                        if retry_backoff > 0:
                            time.sleep(retry_backoff)
                        continue
                    result["status"] = status_name
                    result["error"] = last_error
                    result["reason"] = last_reason
                    result["finished_at"] = _utc_now_iso()
                    return result

                verify = _verify_archive(job_id)
                last_verification = verify
                result["verification"] = verify
                result["verify"] = str(verify.get("status") or "not_attempted")
                attempt_info["verification"] = verify
                attempt_info["verify"] = result["verify"]
                if bool(verify.get("ok")):
                    result["ok"] = True
                    result["status"] = "completed"
                    result["reason"] = "upload_and_verification_succeeded"
                    attempt_info["ok"] = True
                    attempt_info["status"] = result["status"]
                    attempt_info["reason"] = result["reason"]
                    attempt_info["finished_at"] = _utc_now_iso()
                    result["attempt_details"].append(attempt_info)
                    result["finished_at"] = attempt_info["finished_at"]
                    _emit(
                        log,
                        f"🗄️ RANDY backup attempt {attempt}/{max_attempts} verified successfully: "
                        f"verify={result['verify']} remote_job_id={result['remote_job_id']}",
                    )
                    return result

                result["ok"] = False
                result["status"] = "verification_failed"
                if verify.get("job_exists"):
                    result["error"] = "Archive uploaded but result-table verification did not succeed"
                    result["reason"] = "uploaded_but_verification_incomplete"
                else:
                    result["error"] = "Archive upload completed but RANDY verification could not find the job"
                    result["reason"] = "uploaded_but_job_missing_in_verification"
                attempt_info.update({
                    "ok": False,
                    "status": result["status"],
                    "reason": result["reason"],
                    "error": result["error"],
                    "retryable": False,
                    "finished_at": _utc_now_iso(),
                })
                result["attempt_details"].append(attempt_info)
                result["finished_at"] = attempt_info["finished_at"]
                _emit(
                    log,
                    f"🗄️ RANDY backup attempt {attempt}/{max_attempts} failed verification: "
                    f"verify={result['verify']} error={result['error']}",
                )
                return result
            except RequestException as exc:
                retryable = _exception_is_retryable(exc)
                error_text = _error_text(exc)
                attempt_info.update({
                    "ok": False,
                    "status": "request_exception",
                    "reason": "request_exception",
                    "error": error_text,
                    "retryable": retryable,
                    "finished_at": _utc_now_iso(),
                })
                result["attempt_details"].append(attempt_info)
                last_error = error_text
                last_reason = "request_exception"
                last_status = "request_exception"
                _emit(
                    log,
                    f"🗄️ RANDY backup attempt {attempt}/{max_attempts} failed: "
                    f"status=request_exception retryable={retryable} error={error_text}",
                )
                if retryable and attempt < max_attempts:
                    attempt_info["retry_delay_seconds"] = retry_backoff
                    _emit(log, f"🗄️ RANDY backup retry scheduled in {retry_backoff}s")
                    if retry_backoff > 0:
                        time.sleep(retry_backoff)
                    continue
                break
            except Exception as exc:
                error_text = _error_text(exc)
                attempt_info.update({
                    "ok": False,
                    "status": "exception",
                    "reason": "unexpected_exception",
                    "error": error_text,
                    "retryable": False,
                    "finished_at": _utc_now_iso(),
                })
                result["attempt_details"].append(attempt_info)
                result["status"] = "exception"
                result["error"] = error_text
                result["reason"] = "unexpected_exception"
                result["finished_at"] = attempt_info["finished_at"]
                return result

        result["verification"] = last_verification
        result["verify"] = str(last_verification.get("status") or "not_attempted")
        result["ok"] = False
        result["status"] = "max_retries_exceeded" if max_attempts > 1 else last_status
        result["error"] = last_error
        result["reason"] = last_reason or "max_retries_exceeded"
        result["finished_at"] = _utc_now_iso()
        return result
    except Exception as exc:
        result["attempted"] = True
        result["status"] = "exception"
        result["error"] = _error_text(exc)
        result["reason"] = "unexpected_exception"
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

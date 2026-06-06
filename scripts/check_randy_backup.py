#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from api import randy_archive_client
from api.randy_backup_client import (
    TIMEOUT_ENV_NAMES,
    backup_configuration_summary,
    backup_job_directory,
    build_backup_plan,
)


def _safe_url(raw: str) -> str:
    raw = str(raw or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return raw


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _safe_env_value(name: str) -> str:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return "<missing>"
    if "TOKEN" in name:
        return f"<set:redacted:length={len(str(raw))}>"
    return str(raw)


def _env_names_payload() -> dict[str, Any]:
    exact_names = [
        "RANDY_BACKUP_BASE_URL",
        "RANDY_BACKUP_TOKEN",
        "RANDY_ARCHIVE_BASE_URL",
        "RANDY_ARCHIVE_TOKEN",
        "RANDY_BACKUP_CONNECT_TIMEOUT",
        "RANDY_BACKUP_READ_TIMEOUT",
        "RANDY_BACKUP_UPLOAD_TIMEOUT",
        "RANDY_BACKUP_TOTAL_TIMEOUT",
        "RANDY_BACKUP_RETRIES",
        "RANDY_BACKUP_RETRY_BACKOFF_SECONDS",
        "WARHEAD_BACKUP_TIMEOUT_SECONDS",
        "WARHEAD_BACKUP_MAX_BYTES",
        "WARHEAD_JOB_BACKUP_EXCLUDE_CIF",
        "WARHEAD_BACKUP_REQUIRED",
        "WARHEAD_BACKUP_ON_COMPLETE",
        "WARHEAD_BACKUP_ON_FAILURE",
        "WARHEAD_RUN_CLEANUP_STEP",
        "DYNO",
    ]
    return {name: _safe_env_value(name) for name in exact_names}


def _env_payload() -> dict[str, Any]:
    cfg = backup_configuration_summary()
    return {
        "provider": "randy",
        "configured": bool(cfg.get("configured")),
        "base_url": _safe_url(str(cfg.get("base_url") or "")),
        "endpoint": _safe_url(str(cfg.get("endpoint") or "")),
        "token_present": bool(cfg.get("token_present")),
        "backup_on_complete": bool(cfg.get("backup_on_complete")),
        "backup_on_failure": bool(cfg.get("backup_on_failure")),
        "archive_required": bool(cfg.get("archive_required")),
        "timeout_seconds": int(cfg.get("timeout_seconds") or 0),
        "connect_timeout_seconds": float(cfg.get("connect_timeout_seconds") or 0),
        "read_timeout_seconds": float(cfg.get("read_timeout_seconds") or 0),
        "upload_timeout_seconds": float(cfg.get("upload_timeout_seconds") or 0),
        "total_timeout_seconds": float(cfg.get("total_timeout_seconds") or 0),
        "retries": int(cfg.get("retries") or 0),
        "max_attempts": int(cfg.get("max_attempts") or 0),
        "retry_backoff_seconds": float(cfg.get("retry_backoff_seconds") or 0),
        "max_bytes": int(cfg.get("max_bytes") or 0),
        "endpoint_host_path": str(cfg.get("endpoint_host_path") or ""),
        "timeout_model": cfg.get("timeout_model") or {},
        "env": _env_names_payload(),
        "supported_timeout_env_names": TIMEOUT_ENV_NAMES,
    }


def _headers() -> dict[str, str]:
    return {"User-Agent": "warhead-hunter-randy-check/1.0"}


def _health_check() -> int:
    cfg = backup_configuration_summary()
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    if not base_url:
        _print_json({
            "ok": False,
            "status": "not_configured",
            "error": "RANDY backup base URL is not configured.",
            **_env_payload(),
        })
        return 1

    probes: list[dict[str, Any]] = []
    failure_statuses: list[str] = []
    for url, auth in [
        (base_url.replace("/backup", "") + "/healthz", False),
        (base_url + "/summary", True),
    ]:
        try:
            resp = requests.get(
                url,
                headers=randy_archive_client._headers() if auth else _headers(),
                timeout=min(int(cfg.get("timeout_seconds") or 15), 15),
            )
            try:
                body = resp.json()
            except Exception:
                body = {"text": resp.text[:200]}
            if auth and resp.status_code in {401, 403}:
                status = "auth_failed"
            elif resp.status_code == 408:
                status = "timeout"
            elif resp.status_code >= 500:
                status = "receiver_unhealthy"
            elif resp.status_code >= 400:
                status = "receiver_unreachable"
            else:
                status = "ok"
            if status != "ok":
                failure_statuses.append(status)
            probes.append({
                "url": _safe_url(url),
                "auth": auth,
                "status_code": resp.status_code,
                "ok": bool(resp.ok),
                "status": status,
                "body": body,
            })
        except requests.Timeout as exc:
            failure_statuses.append("timeout")
            probes.append({
                "url": _safe_url(url),
                "auth": auth,
                "status_code": 0,
                "ok": False,
                "status": "timeout",
                "error": str(exc),
            })
        except requests.ConnectionError as exc:
            failure_statuses.append("receiver_unreachable")
            probes.append({
                "url": _safe_url(url),
                "auth": auth,
                "status_code": 0,
                "ok": False,
                "status": "receiver_unreachable",
                "error": str(exc),
            })
        except Exception as exc:
            failure_statuses.append("receiver_unreachable")
            probes.append({
                "url": _safe_url(url),
                "auth": auth,
                "status_code": 0,
                "ok": False,
                "status": "receiver_unreachable",
                "error": str(exc),
            })

    public_ok = any(item.get("status_code") == 200 and item.get("auth") is False for item in probes)
    auth_ok = any(
        item.get("status_code") == 200 and item.get("auth") is True and bool(item.get("body", {}).get("ok"))
        for item in probes
    )
    overall_ok = bool(public_ok and auth_ok)
    overall_status = "ok" if overall_ok else (failure_statuses[0] if failure_statuses else "receiver_unhealthy")
    _print_json({
        "ok": overall_ok,
        "status": overall_status,
        "public_health_ok": public_ok,
        "authenticated_summary_ok": auth_ok,
        "checks": probes,
        **_env_payload(),
    })
    return 0 if overall_ok else 1


def _job_dir(job_id: str) -> Path:
    return APP_ROOT / "jobs" / job_id


def _plan_payload(job_id: str) -> dict[str, Any]:
    plan = build_backup_plan(_job_dir(job_id))
    return {
        "job_id": job_id,
        "job_dir": str(_job_dir(job_id)),
        "plan_ok": bool(plan.get("ok")),
        "plan_status": str(plan.get("plan_status") or ""),
        "plan_reason": str(plan.get("plan_reason") or plan.get("reason") or ""),
        "archive_profile": str(plan.get("archive_profile") or ""),
        "selected_file_count": int(plan.get("selected_file_count") or 0),
        "selected_bytes": int(plan.get("selected_bytes") or 0),
        "skipped_file_count": int(plan.get("skipped_file_count") or 0),
        "skipped_bytes": int(plan.get("skipped_bytes") or 0),
        "curated_only": bool(plan.get("curated_only")),
        "max_bytes": int(plan.get("max_bytes") or 0),
        "required_selected": list(plan.get("required_selected") or []),
        "required_missing": list(plan.get("required_missing") or []),
        "required_skipped": list(plan.get("required_skipped") or []),
        "largest_selected_files": list(plan.get("largest_selected_files") or []),
        "largest_skipped_files": list(plan.get("largest_skipped_files") or []),
        "preferred_file_count": int(plan.get("preferred_file_count") or 0),
        "preferred_bytes": int(plan.get("preferred_bytes") or 0),
        "other_file_count": int(plan.get("other_file_count") or 0),
        "other_bytes": int(plan.get("other_bytes") or 0),
        "contains_target_results": bool(plan.get("contains_target_results")),
        "contains_mcs_sdf": bool(plan.get("contains_mcs_sdf")),
        "contains_mcs_svg": bool(plan.get("contains_mcs_svg")),
        "contains_war_pdb": bool(plan.get("contains_war_pdb")),
        "cif_excluded": bool(plan.get("cif_excluded")),
        "selected_path_kinds": plan.get("selected_path_kinds") or {},
        "skipped_path_kinds": plan.get("skipped_path_kinds") or {},
        "route_critical_checks": plan.get("route_critical_checks") or {},
        "sample_paths": [item.rel for item in plan.get("selected_files", [])[:25]],
        "reason": str(plan.get("reason") or ""),
    }


def _dry_run(job_id: str, status: str) -> int:
    job_dir = _job_dir(job_id)
    if not job_dir.exists():
        _print_json({
            **_env_payload(),
            **_plan_payload(job_id),
            "ok": False,
            "error": f"Local job directory not found: {job_dir}",
        })
        return 1

    result = backup_job_directory(job_id, job_dir, status=status, dry_run=True, log=print)
    _print_json({
        **_env_payload(),
        **_plan_payload(job_id),
        "ok": bool(result.get("ok")),
        "result": result,
    })
    return 0 if result.get("ok") else 1


def _upload_test(job_id: str, status: str) -> int:
    job_dir = _job_dir(job_id)
    if not job_dir.exists():
        _print_json({"ok": False, "error": f"Local job directory not found: {job_dir}", "job_id": job_id})
        return 1

    result = backup_job_directory(job_id, job_dir, status=status, dry_run=False, log=print)
    _print_json({
        "mode": "upload_test",
        **_env_payload(),
        **_plan_payload(job_id),
        "ok": bool(result.get("ok")),
        "result": result,
    })
    return 0 if result.get("ok") else 1


def _verify(job_id: str) -> int:
    randy_archive_client.reset_job_cache(job_id)
    detail = randy_archive_client.get_job_index(job_id)
    table = randy_archive_client.get_table_dataframe(job_id, ["Results_Display.csv"])
    diag = randy_archive_client.last_table_diagnostic()
    payload = {
        "job_id": job_id,
        **_env_payload(),
        "ok": bool(detail),
        "detail_found": bool(detail),
        "option_count": int(detail.get("option_count") or len(detail.get("options") or [])) if detail else 0,
        "archive_layout": detail.get("archive_layout") if isinstance(detail, dict) else {},
        "available_tables": detail.get("available_tables") if isinstance(detail, dict) else {},
        "results_table_rows": 0 if table is None else int(len(table.index)),
        "table_diagnostic": diag,
    }
    _print_json(payload)
    return 0 if detail else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Check RANDY backup configuration, connectivity, upload, and verification.")
    parser.add_argument("--env-check", action="store_true", help="Print safe RANDY backup configuration details.")
    parser.add_argument("--health", action="store_true", help="Probe RANDY health and authenticated summary endpoints.")
    parser.add_argument("--job", help="Local or archived job id to inspect.")
    parser.add_argument("--status", default="completed", choices=["completed", "failed"], help="Backup status label to send with --dry-run or --upload-test.")
    parser.add_argument("--dry-run", action="store_true", help="Build a backup plan for the selected job without uploading.")
    parser.add_argument("--upload-test", action="store_true", help="Explicitly upload the selected local job to RANDY.")
    parser.add_argument("--verify", action="store_true", help="Verify an archived job through the RANDY readback API.")
    args = parser.parse_args()

    actions = [args.env_check, args.health, args.dry_run, args.upload_test, args.verify]
    chosen = sum(1 for item in actions if item)
    if chosen != 1:
        parser.error("Choose exactly one of --env-check, --health, --dry-run, --upload-test, or --verify.")
    if (args.dry_run or args.upload_test or args.verify) and not args.job:
        parser.error("--job is required with --dry-run, --upload-test, or --verify.")

    if args.env_check:
        _print_json(_env_payload())
        return 0
    if args.health:
        return _health_check()
    if args.dry_run:
        return _dry_run(str(args.job).strip(), str(args.status).strip())
    if args.upload_test:
        return _upload_test(str(args.job).strip(), str(args.status).strip())
    return _verify(str(args.job).strip())


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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
        "max_bytes": int(cfg.get("max_bytes") or 0),
    }


def _headers() -> dict[str, str]:
    return {"User-Agent": "warhead-hunter-randy-check/1.0"}


def _health_check() -> int:
    cfg = backup_configuration_summary()
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    if not base_url:
        _print_json({
            "ok": False,
            "error": "RANDY backup base URL is not configured.",
            **_env_payload(),
        })
        return 1

    probes: list[dict[str, Any]] = []
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
            probes.append({
                "url": _safe_url(url),
                "auth": auth,
                "status_code": resp.status_code,
                "ok": bool(resp.ok),
                "body": body,
            })
        except Exception as exc:
            probes.append({
                "url": _safe_url(url),
                "auth": auth,
                "status_code": 0,
                "ok": False,
                "error": str(exc),
            })

    overall_ok = any(
        item.get("status_code") == 200 and (item.get("auth") is False or bool(item.get("body", {}).get("ok")))
        for item in probes
    )
    _print_json({
        "ok": overall_ok,
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
        "selected_file_count": int(plan.get("selected_file_count") or 0),
        "selected_bytes": int(plan.get("selected_bytes") or 0),
        "skipped_file_count": int(plan.get("skipped_file_count") or 0),
        "skipped_bytes": int(plan.get("skipped_bytes") or 0),
        "curated_only": bool(plan.get("curated_only")),
        "max_bytes": int(plan.get("max_bytes") or 0),
        "sample_paths": [item.rel for item in plan.get("selected_files", [])[:25]],
        "reason": str(plan.get("reason") or ""),
    }


def _dry_run(job_id: str) -> int:
    job_dir = _job_dir(job_id)
    if not job_dir.exists():
        _print_json({"ok": False, "error": f"Local job directory not found: {job_dir}", "job_id": job_id})
        return 1

    result = backup_job_directory(job_id, job_dir, status="completed", dry_run=True)
    _print_json({
        **_env_payload(),
        **_plan_payload(job_id),
        "ok": bool(result.get("ok")),
        "result": result,
    })
    return 0 if result.get("ok") else 1


def _upload_test(job_id: str) -> int:
    job_dir = _job_dir(job_id)
    if not job_dir.exists():
        _print_json({"ok": False, "error": f"Local job directory not found: {job_dir}", "job_id": job_id})
        return 1

    result = backup_job_directory(job_id, job_dir, status="completed", dry_run=False)
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
        return _dry_run(str(args.job).strip())
    if args.upload_test:
        return _upload_test(str(args.job).strip())
    return _verify(str(args.job).strip())


if __name__ == "__main__":
    raise SystemExit(main())

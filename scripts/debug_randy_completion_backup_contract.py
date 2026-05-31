#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from api import randy_archive_client
from api.randy_backup_client import backup_job_directory, build_backup_plan


def _print_json(payload):
    print(json.dumps(payload, indent=2, sort_keys=True))


def _verify(job_id: str) -> int:
    randy_archive_client.reset_job_cache(job_id)
    detail = randy_archive_client.get_job_index(job_id)
    if not detail:
        _print_json({
            "ok": False,
            "job_id": job_id,
            "error": "RANDY job index not found",
        })
        return 1

    table = randy_archive_client.get_table_dataframe(job_id, ["Results_Display.csv"])
    diag = randy_archive_client.last_table_diagnostic()
    payload = {
        "ok": True,
        "job_id": job_id,
        "archive_layout": detail.get("archive_layout") or {},
        "available_tables": detail.get("available_tables") or {},
        "option_count": int(detail.get("option_count") or len(detail.get("options") or [])),
        "results_table_rows": 0 if table is None else int(len(table.index)),
        "table_diagnostic": diag,
    }
    if isinstance(detail.get("options"), list):
        first = next((item for item in detail["options"] if isinstance(item, dict)), None)
        if first:
            pdb = str(first.get("pdb") or "").strip()
            chain = str(first.get("chain") or "").strip()
            ligand = str(first.get("ligand") or first.get("warhead") or "").strip()
            resid = str(first.get("resid") or "").strip()
            payload["first_option_artifacts"] = {
                "protein": randy_archive_client.find_protein_pdb_asset(job_id, pdb=pdb, chain=chain, ligand=ligand),
                "sdf": randy_archive_client.find_asset(job_id, pdb=pdb, chain=chain, ligand=ligand, resid=resid, kind="sdf"),
                "svg": randy_archive_client.find_asset(job_id, pdb=pdb, chain=chain, ligand=ligand, resid=resid, kind="svg"),
            }
    _print_json(payload)
    return 0 if payload["results_table_rows"] >= 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run, upload, or verify the RANDY completion-backup contract.")
    parser.add_argument("job_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    selected = [args.dry_run, args.upload, args.verify]
    if sum(1 for item in selected if item) != 1:
        parser.error("Choose exactly one of --dry-run, --upload, or --verify")

    job_dir = APP_ROOT / "jobs" / args.job_id
    if args.verify:
        return _verify(args.job_id)

    if not job_dir.exists():
        _print_json({
            "ok": False,
            "job_id": args.job_id,
            "error": f"Local job directory not found: {job_dir}",
        })
        return 1

    if args.dry_run:
        plan = build_backup_plan(job_dir)
        sample = [item.rel for item in plan.get("selected_files", [])[:25]]
        _print_json({
            "ok": bool(plan.get("ok")),
            "job_id": args.job_id,
            "job_dir": str(job_dir),
            "selected_file_count": int(plan.get("selected_file_count") or 0),
            "selected_bytes": int(plan.get("selected_bytes") or 0),
            "skipped_file_count": int(plan.get("skipped_file_count") or 0),
            "skipped_bytes": int(plan.get("skipped_bytes") or 0),
            "curated_only": bool(plan.get("curated_only")),
            "max_bytes": int(plan.get("max_bytes") or 0),
            "reason": plan.get("reason") or "",
            "sample_paths": sample,
        })
        return 0 if plan.get("ok") else 1

    result = backup_job_directory(args.job_id, job_dir, status="completed", dry_run=False)
    _print_json(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

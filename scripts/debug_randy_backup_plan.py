#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from api.randy_backup_client import build_backup_plan


def _job_dir_from_args(job_id: str | None, job_dir: str | None) -> Path:
    if job_dir:
        return Path(job_dir).expanduser().resolve()
    if not job_id:
        raise ValueError("Provide --job-id or --job-dir")
    return (APP_ROOT / "jobs" / job_id.strip()).resolve()


def _recommended_config(plan: dict[str, Any]) -> list[str]:
    max_bytes = int(plan.get("max_bytes") or 0)
    recommendations: list[str] = []
    if not plan.get("ok"):
        if str(plan.get("plan_status") or "") == "archive_too_large" and max_bytes < 1_073_741_824:
            recommendations.append("Consider setting WARHEAD_BACKUP_MAX_BYTES=1073741824 on Heroku for large completed jobs.")
        if not bool(plan.get("cif_excluded")):
            recommendations.append("Set WARHEAD_JOB_BACKUP_EXCLUDE_CIF=1 to keep downloaded CIFs out of large backups.")
    return recommendations


def _json_payload(job_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_dir": str(job_dir),
        "plan_ok": bool(plan.get("ok")),
        "plan_status": str(plan.get("plan_status") or ""),
        "plan_reason": str(plan.get("plan_reason") or plan.get("reason") or ""),
        "archive_profile": str(plan.get("archive_profile") or ""),
        "selected_file_count": int(plan.get("selected_file_count") or 0),
        "selected_bytes": int(plan.get("selected_bytes") or 0),
        "skipped_file_count": int(plan.get("skipped_file_count") or 0),
        "skipped_bytes": int(plan.get("skipped_bytes") or 0),
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
        "recommended_config": _recommended_config(plan),
    }


def _print_text(job_dir: Path, plan: dict[str, Any]) -> None:
    payload = _json_payload(job_dir, plan)
    print(f"job_dir: {payload['job_dir']}")
    print(f"plan_ok: {payload['plan_ok']}")
    print(f"plan_status: {payload['plan_status']}")
    print(f"plan_reason: {payload['plan_reason']}")
    print(f"archive_profile: {payload['archive_profile']}")
    print(
        "selected: "
        f"{payload['selected_file_count']} files, {payload['selected_bytes']} bytes"
    )
    print(
        "skipped: "
        f"{payload['skipped_file_count']} files, {payload['skipped_bytes']} bytes"
    )
    print(f"max_bytes: {payload['max_bytes']}")
    print(f"required_selected: {len(payload['required_selected'])}")
    print(f"required_missing: {len(payload['required_missing'])}")
    print(f"required_skipped: {len(payload['required_skipped'])}")
    print(f"preferred: {payload['preferred_file_count']} files, {payload['preferred_bytes']} bytes")
    print(f"other: {payload['other_file_count']} files, {payload['other_bytes']} bytes")
    print(
        "contains: "
        f"TARGET_RESULTS={payload['contains_target_results']} "
        f"MCS_SDF={payload['contains_mcs_sdf']} "
        f"MCS_SVG={payload['contains_mcs_svg']} "
        f"WAR_PDB={payload['contains_war_pdb']}"
    )
    print(
        "path_kinds: "
        f"selected={payload['selected_path_kinds']} "
        f"skipped={payload['skipped_path_kinds']}"
    )
    print(f"route_critical_checks: {payload['route_critical_checks']}")
    print("required_selected_files:")
    for rel in payload["required_selected"]:
        print(f"  - {rel}")
    print("required_skipped_files:")
    for rel in payload["required_skipped"]:
        print(f"  - {rel}")
    print("required_missing_files:")
    for rel in payload["required_missing"]:
        print(f"  - {rel}")
    print("largest_selected_files:")
    for item in payload["largest_selected_files"]:
        print(f"  - {item['size_bytes']}\t{item['category']}\t{item['rel']}")
    print("largest_skipped_files:")
    for item in payload["largest_skipped_files"]:
        print(f"  - {item['size_bytes']}\t{item['category']}\t{item['rel']}")
    if payload["recommended_config"]:
        print("recommended_config:")
        for line in payload["recommended_config"]:
            print(f"  - {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect RANDY backup planning for a local job without network access.")
    parser.add_argument("--job-id", help="Job id under the local jobs/ directory.")
    parser.add_argument("--job-dir", help="Explicit local job directory.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument("--max-bytes", type=int, help="Override WARHEAD_BACKUP_MAX_BYTES for the plan.")
    args = parser.parse_args()

    job_dir = _job_dir_from_args(args.job_id, args.job_dir)
    plan = build_backup_plan(job_dir, max_bytes=args.max_bytes)
    payload = _json_payload(job_dir, plan)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_text(job_dir, plan)
    return 0 if payload["plan_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

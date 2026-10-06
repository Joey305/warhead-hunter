#!/usr/bin/env python3
"""Backfill RANDY analytics facts from archived Warhead Hunter job folders.

Run on RANDY:
  .venv/bin/python scripts/backfill_randy_hunter_analytics.py --jobs-dir warhead_hunter/hunter_jobs
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

from backup_receiver import app as receiver


def _first_existing(paths: Iterable[Path]) -> Path | None:
    return next((path for path in paths if path.is_file()), None)


def _read_json(path: Path | None) -> Dict[str, Any]:
    if not path:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _number(row: Dict[str, str], key: str) -> float:
    try:
        return float(str(row.get(key, "") or "0"))
    except ValueError:
        return 0.0


def _metrics(results: Path | None) -> Dict[str, Any]:
    if not results:
        return {}
    try:
        with results.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return {}
    ligand_key = next((key for key in ("Ligand_Resolved", "Warhead", "Ligand5", "Ligand") if rows and key in rows[0]), "")
    structures = {str(row.get("pdb_id", "")).strip() for row in rows if str(row.get("pdb_id", "")).strip()}
    ligands = {str(row.get(ligand_key, "")).strip() for row in rows if ligand_key and str(row.get(ligand_key, "")).strip()}
    exposure = [_number(row, "%Exposed") for row in rows]
    return {
        "structure_count": len(structures), "pose_count": len(rows), "unique_ligand_count": len(ligands),
        "total_ligand_atoms": int(sum(_number(row, "Total_atoms") for row in rows)),
        "exposed_atom_count": int(sum(_number(row, "Exposed_atoms") for row in rows)),
        "mean_percent_exposed": (sum(exposure) / len(exposure)) if exposure else 0,
        "high_exposure_pose_count": sum(value >= 0.5 for value in exposure),
    }


def _event_time(metadata: Dict[str, Any], fallback_path: Path) -> str:
    for key in ("finished_at", "updated_at", "created_at", "started_at"):
        value = str(metadata.get(key, "")).strip()
        if value:
            return value.replace(" ", "T") if "T" not in value else value
    return datetime.fromtimestamp(fallback_path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs-dir", type=Path, default=receiver.HUNTER_JOBS_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    processed = skipped = 0
    for job_dir in sorted((path for path in args.jobs_dir.iterdir() if path.is_dir()), key=lambda path: path.name):
        metadata_path = _first_existing([job_dir / "job_files" / "job_metadata.json", job_dir / "job_metadata.json"])
        metadata = _read_json(metadata_path)
        results = _first_existing([job_dir / "job_files" / "TARGET_RESULTS" / "Resolved_SASA_Summary.csv", job_dir / "TARGET_RESULTS" / "Resolved_SASA_Summary.csv", job_dir / "job_files" / "Resolved_SASA_Summary.csv", job_dir / "Resolved_SASA_Summary.csv"])
        status = str(metadata.get("status", "")).lower()
        if results:
            event_type = "hunter_job_completed"
        elif status == "failed":
            event_type = "hunter_job_failed"
        else:
            skipped += 1
            continue
        target = str(metadata.get("target") or metadata.get("target_name") or (metadata.get("request") or {}).get("target_name") or "Unknown")
        payload = {
            "event_type": event_type, "job_id": job_dir.name, "target_name": target,
            "source": "randy_archive_backfill", "occurred_at_utc": _event_time(metadata, job_dir),
            "idempotency_key": f"randy-archive-backfill:v1:{job_dir.name}:{event_type}",
            **_metrics(results),
        }
        if event_type == "hunter_job_failed":
            payload["failed_step"] = str(metadata.get("current_step") or "unknown")
        if not args.dry_run:
            receiver.store_analytics_event(payload)
        processed += 1
    print(json.dumps({"processed": processed, "skipped": skipped, "dry_run": args.dry_run}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

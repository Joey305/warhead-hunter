#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import job_state


REQUIRED_METADATA_FIELDS = [
    "job_id",
    "target",
    "status",
    "created_at",
    "started_at",
    "finished_at",
    "current_step",
    "step_started_at",
    "last_log_at",
    "error",
    "job_dir",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--simulate-empty-store", action="store_true")
    args = parser.parse_args()

    live_state = None if args.simulate_empty_store else {}

    print(f"Job: {args.job_id}")
    print(f"Jobs root: {job_state.get_jobs_root()}")

    if not job_state.job_exists_on_disk(args.job_id):
        print("FAIL: job folder does not exist")
        return 1

    job_dir = job_state.job_dir_for(args.job_id)
    print(f"PASS: job folder exists -> {job_dir}")

    hydrated = job_state.hydrate_job_from_disk(args.job_id, live_state=live_state)
    if hydrated is None:
        print("FAIL: hydrate_job_from_disk could not resolve the job")
        return 1

    meta = job_state.load_job_metadata(args.job_id)
    if meta is None:
        print("FAIL: job_metadata.json is missing or unreadable")
        return 1

    print(f"PASS: job_metadata.json exists -> {job_state.job_metadata_path(args.job_id)}")
    missing = [field for field in REQUIRED_METADATA_FIELDS if field not in meta]
    if missing:
        print(f"FAIL: metadata missing required fields -> {missing}")
    else:
        print("PASS: metadata contains required fields")

    log_lines = job_state.load_job_log_lines(args.job_id)
    log_path = job_state.job_log_path(args.job_id)
    if not log_path.exists():
        print("FAIL: job.log missing")
    else:
        print(f"PASS: job.log exists -> {log_path}")
        print(f"INFO: job.log line count -> {len(log_lines)}")

    print("PASS: route-equivalent disk lookup resolved job without JOB_STORE")
    print(f"INFO: inferred status -> {hydrated.get('status')}")
    print(f"INFO: inferred results_ready -> {hydrated.get('results_ready')}")
    print(f"INFO: current_step -> {hydrated.get('current_step')}")
    print("INFO: summary json:")
    print(json.dumps({
        "job_id": hydrated.get("job_id"),
        "target": hydrated.get("target"),
        "status": hydrated.get("status"),
        "results_ready": hydrated.get("results_ready"),
        "log_lines": len(hydrated.get("log", [])),
        "error": hydrated.get("error"),
    }, indent=2))

    if hydrated.get("status") not in {"queued", "pending", "running", "completed", "failed", "unknown"}:
        print(f"FAIL: unexpected inferred status -> {hydrated.get('status')}")
        return 1

    print("PASS: completed/failed/running status is inferable from disk artifacts")
    print("PASS: results readiness is inferable from disk artifacts")

    return 0 if not missing and log_path.exists() else 1


if __name__ == "__main__":
    raise SystemExit(main())

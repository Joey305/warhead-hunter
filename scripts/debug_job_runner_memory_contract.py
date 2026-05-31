#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import job_runner
import job_state


def make_job(job_id: str, target: str = "MEMTEST") -> Path:
    job_dir = ROOT / "jobs" / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    job_runner.JOB_STORE[job_id] = {
        "status": "pending",
        "target": target,
        "created_at": job_runner._timestamp(),
        "started_at": "",
        "finished_at": "",
        "current_step": "",
        "step_started_at": "",
        "log": [],
        "log_line_count": 0,
        "log_truncated": False,
    }
    job_runner.write_job_metadata(job_id, {"status": "queued", "target": target}, job_dir=str(job_dir))
    return job_dir


def cleanup_job(job_id: str) -> None:
    job_runner.JOB_STORE.pop(job_id, None)
    job_dir = ROOT / "jobs" / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir)


def main() -> int:
    failures: list[str] = []

    cap_job = f"memcap{uuid.uuid4().hex[:2]}"
    make_job(cap_job)
    try:
        total_lines = job_runner.MAX_IN_MEMORY_LOG_LINES + 75
        for idx in range(total_lines):
            job_runner._append_live_log(cap_job, f"line {idx}", persist=True)

        live_tail = job_runner.JOB_STORE[cap_job]["log"]
        disk_count = job_state.count_job_log_lines(cap_job)

        if len(live_tail) > job_runner.MAX_IN_MEMORY_LOG_LINES:
            failures.append("in-memory log tail exceeded configured cap")
        else:
            print(f"PASS: in-memory log tail capped at {len(live_tail)} <= {job_runner.MAX_IN_MEMORY_LOG_LINES}")

        if disk_count != total_lines:
            failures.append(f"disk log line count mismatch ({disk_count} != {total_lines})")
        else:
            print(f"PASS: disk job.log preserved full output ({disk_count} lines)")

        if not job_runner.JOB_STORE[cap_job].get("log_truncated"):
            failures.append("log_truncated flag was not set after cap rollover")
        else:
            print("PASS: log_truncated flag set after cap rollover")
    finally:
        cleanup_job(cap_job)

    fail_job = f"memfail{uuid.uuid4().hex[:2]}"
    job_dir = make_job(fail_job, target="FAILTEST")

    original_copy_assets = job_runner._copy_assets
    original_write_inputs = job_runner._write_inputs
    original_run_script_logged = job_runner.run_script_logged
    original_validate_checkpoint = job_runner.validate_mcs_sdf_checkpoint
    original_validate_display = job_runner.validate_required_display_artifacts
    original_cleanup = job_runner._run_cleanup_packaging

    try:
        job_runner._copy_assets = lambda *args, **kwargs: None
        job_runner._write_inputs = lambda *args, **kwargs: None
        job_runner.validate_mcs_sdf_checkpoint = lambda *args, **kwargs: None
        job_runner.validate_required_display_artifacts = lambda *args, **kwargs: None
        job_runner._run_cleanup_packaging = lambda *args, **kwargs: None

        def fail_run(*args, **kwargs):
            raise job_runner.MemoryGuardError("simulated memory guard failure")

        job_runner.run_script_logged = fail_run
        job_runner.run_pipeline_task(fail_job, "FAILTEST", "FAILTEST", "")

        meta = job_state.load_job_metadata(fail_job)
        if not meta or str(meta.get("status") or "").lower() != "failed":
            failures.append("simulated memory guard failure did not persist failed status")
        else:
            print("PASS: simulated memory guard failure persisted failed status")

        err_value = (meta or {}).get("error")
        err = err_value.get("message") if isinstance(err_value, dict) else str(err_value or "")
        if "simulated memory guard failure" not in str(err):
            failures.append("simulated memory guard failure did not persist readable error message")
        else:
            print("PASS: simulated memory guard failure persisted readable error message")
    finally:
        job_runner._copy_assets = original_copy_assets
        job_runner._write_inputs = original_write_inputs
        job_runner.run_script_logged = original_run_script_logged
        job_runner.validate_mcs_sdf_checkpoint = original_validate_checkpoint
        job_runner.validate_required_display_artifacts = original_validate_display
        job_runner._run_cleanup_packaging = original_cleanup
        cleanup_job(fail_job)

    if job_runner.MEMORY_GUARD_MB:
        print(f"PASS: memory guard configured -> {job_runner.MEMORY_GUARD_MB}MB")
    else:
        print("INFO: memory guard disabled in current environment defaults")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("PASS: job runner memory contract checks completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import job_runner


def _top_child_processes(root_pid: int, limit: int = 5) -> list[dict]:
    rows = []
    for pid in [root_pid, *job_runner._child_pids(root_pid)]:
        rss = job_runner._current_rss_mb(pid)
        if rss <= 0:
            continue
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        cmd = ""
        try:
            cmd = cmdline_path.read_text(encoding="utf-8", errors="ignore").replace("\x00", " ").strip()
        except Exception:
            cmd = ""
        rows.append({"pid": pid, "rss_mb": rss, "cmd": cmd[:160]})
    rows.sort(key=lambda row: row["rss_mb"], reverse=True)
    return rows[:limit]


def profile_step(job_dir: Path, step_name: str, interval_sec: float, max_seconds: int | None) -> Path:
    script_path = job_dir / step_name
    if not script_path.exists():
        raise FileNotFoundError(f"Step script not found: {script_path}")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    python_bin = job_runner.PYTHON_BIN
    cmd = [python_bin, "-u", step_name]
    diagnostics_dir = job_dir / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    report_path = diagnostics_dir / f"{step_name}.memory_profile.json"

    start = time.time()
    samples: list[dict] = []
    peak_sample: dict | None = None
    stdout_tail: list[str] = []
    stderr_tail: list[str] = []

    with subprocess.Popen(
        cmd,
        cwd=str(job_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=env,
        start_new_session=(os.name != "nt"),
    ) as proc:
        selector = selectors.DefaultSelector()
        if proc.stdout is not None:
            selector.register(proc.stdout, selectors.EVENT_READ, data=("stdout", stdout_tail))
        if proc.stderr is not None:
            selector.register(proc.stderr, selectors.EVENT_READ, data=("stderr", stderr_tail))

        try:
            while True:
                now = time.time()
                elapsed = now - start
                if max_seconds is not None and elapsed > max_seconds:
                    job_runner._terminate_process_tree(proc)
                    raise TimeoutError(f"{step_name} exceeded profiler timeout of {max_seconds}s")

                parent_rss = job_runner._current_rss_mb()
                child_tree = job_runner._process_tree_rss_mb(proc.pid) if proc.poll() is None else 0.0
                combined = round(parent_rss + child_tree, 1)
                dyno = job_runner._read_cgroup_memory_mb()
                sample = {
                    "elapsed_sec": round(elapsed, 2),
                    "parent_rss_mb": parent_rss,
                    "child_tree_rss_mb": child_tree,
                    "combined_rss_mb": combined,
                    "dyno_memory_mb": dyno,
                    "top_pids": _top_child_processes(proc.pid),
                }
                samples.append(sample)
                if peak_sample is None or (sample["child_tree_rss_mb"] or 0.0) >= (peak_sample["child_tree_rss_mb"] or 0.0):
                    peak_sample = sample

                events = selector.select(timeout=interval_sec)
                for key, _mask in events:
                    stream_name, sink = key.data
                    line = key.fileobj.readline()
                    if line:
                        sink.append(line.rstrip("\n"))
                        if len(sink) > 80:
                            del sink[:-80]
                if proc.poll() is not None:
                    break

            return_code = proc.wait()
        finally:
            try:
                selector.close()
            except Exception:
                pass
            if proc.poll() is None:
                job_runner._terminate_process_tree(proc)

    report = {
        "job_dir": str(job_dir),
        "step_name": step_name,
        "command": cmd,
        "python_bin": python_bin,
        "sample_interval_sec": interval_sec,
        "returncode": return_code,
        "peak_sample": peak_sample,
        "sample_count": len(samples),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "samples": samples,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile one Warhead Hunter pipeline step's process-tree memory.")
    parser.add_argument("job_dir", type=Path, help="Job directory path, for example jobs/<job_id>")
    parser.add_argument("step_name", help="Pipeline script name, for example 2_SQchk.py")
    parser.add_argument("--interval-sec", type=float, default=0.5)
    parser.add_argument("--max-seconds", type=int, default=None)
    args = parser.parse_args()

    job_dir = args.job_dir
    if not job_dir.is_absolute():
        job_dir = (ROOT / job_dir).resolve()
    if not job_dir.exists():
        print(f"Job directory not found: {job_dir}")
        return 1

    try:
        report = profile_step(job_dir, args.step_name, args.interval_sec, args.max_seconds)
    except Exception as exc:
        print(f"Profiler could not run {args.step_name}: {exc}")
        return 1

    print(f"Wrote memory profile: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

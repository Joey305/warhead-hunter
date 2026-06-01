#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STEP11 = ROOT / "pipeline_assets" / "11_mcsMatcher.py"
JOB_RUNNER = ROOT / "job_runner.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def check(condition: bool, ok_msg: str, fail_msg: str, failures: list[str]) -> None:
    if condition:
        print(f"PASS: {ok_msg}")
    else:
        print(f"FAIL: {fail_msg}")
        failures.append(fail_msg)


def main() -> int:
    parser = argparse.ArgumentParser(description="Statically verify the Step 11 heartbeat/no-output contract.")
    parser.add_argument("--simulate", action="store_true", help="Print representative heartbeat lines after static checks.")
    args = parser.parse_args()

    failures: list[str] = []
    code = read(STEP11)
    runner = read(JOB_RUNNER)

    for token in [
        "WARHEAD_MCS_NO_OUTPUT_SAFE",
        "WARHEAD_MCS_HEARTBEAT_SEC",
        "WARHEAD_MCS_PROGRESS_EVERY",
        "WARHEAD_MCS_SKIP_BAD_ITEMS",
        "WARHEAD_MCS_SKIP_STUCK_ITEMS",
        "WARHEAD_MCS_ITEM_ISOLATION",
    ]:
        check(token in code, f"Step 11 exposes {token}.", f"Step 11 missing {token}.", failures)

    check("⏱ Step 11 heartbeat:" in code, "Step 11 contains non-debug heartbeat output.", "Step 11 missing heartbeat print text.", failures)
    check("heartbeat_worker" in code and "threading.Thread" in code, "Step 11 starts a lightweight heartbeat thread.", "Step 11 missing heartbeat thread wiring.", failures)
    check("flush=True" in code and "print(msg, flush=True)" in code, "Heartbeat output is flushed.", "Heartbeat output is not clearly flushed.", failures)
    check("🔬 Step 11 item" in code and "⏳ Step 11 progress:" in code, "Step 11 contains non-debug lifecycle progress logs.", "Step 11 missing concise lifecycle progress logs.", failures)
    check("if not MCS_DEBUG_MEMORY" in code and "[mcs-mem]" in code, "Detailed memory/debug logs remain gated.", "Detailed memory/debug logs no longer appear gated.", failures)
    check("run_compute_mcs_item" in code and "_item_worker_main" in code, "Step 11 has parent-enforced item isolation hooks.", "Step 11 missing parent-enforced item isolation hooks.", failures)
    check("ProcessPoolExecutor" not in code and "multiprocessing.Pool" not in code and "with Pool(" not in code, "No unbounded process pool was reintroduced.", "Detected process-pool fanout in Step 11.", failures)
    check("WARHEAD_STEP11_NO_OUTPUT_TIMEOUT_SEC" in runner, "job_runner.py exposes configurable Step 11 no-output timeout.", "job_runner.py missing WARHEAD_STEP11_NO_OUTPUT_TIMEOUT_SEC.", failures)
    check("\"11_mcsMatcher.py\": STEP11_NO_OUTPUT_TIMEOUT_SEC" in runner, "Step 11 watchdog default is env-driven.", "Step 11 watchdog still appears hardcoded.", failures)

    if args.simulate:
        print("SIMULATE: ⏱ Step 11 heartbeat: processed=3/284 current=6g1u_B_E1K_608 stage=MCS elapsed=105s item_elapsed=105s sdf_written=3 sdf_failed=0 svg_written=2 last_done=6g1u_A_E1K_608")
        print("SIMULATE: 🔬 Step 11 item 4/284 start 6g1u_B_E1K_608")
        print("SIMULATE: 🔬 Step 11 item 4/284 done 6g1u_B_E1K_608 sdf=written mapped=23 all_atoms=29 dt=7.4s")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

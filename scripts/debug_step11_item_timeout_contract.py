#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STEP11 = ROOT / "pipeline_assets" / "11_mcsMatcher.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def check(condition: bool, ok_msg: str, fail_msg: str, failures: list[str]) -> None:
    if condition:
        print(f"PASS: {ok_msg}")
    else:
        print(f"FAIL: {fail_msg}")
        failures.append(fail_msg)


def main() -> int:
    code = read(STEP11)
    failures: list[str] = []

    for token in [
        "WARHEAD_MCS_SKIP_BAD_ITEMS",
        "WARHEAD_MCS_SKIP_STUCK_ITEMS",
        "WARHEAD_MCS_ITEM_ISOLATION",
        "WARHEAD_MCS_PER_KEY_TIMEOUT",
        "WARHEAD_MCS_MAX_ITEM_FAILURES",
        "WARHEAD_MCS_ENABLE_GRAPH_FULL",
        "WARHEAD_MCS_GRAPH_FULL_MAX_ATOMS",
    ]:
        check(token in code, f"Step 11 exposes {token}.", f"Step 11 missing {token}.", failures)

    check("run_compute_mcs_item" in code and "_item_worker_main" in code, "Step 11 uses parent-enforced item execution.", "Step 11 missing isolated item execution path.", failures)
    check("ctx.Process" in code and "os.killpg" in code, "Stuck item child processes can be killed by the parent.", "Stuck item kill path not found.", failures)
    check("ITEM_TIMEOUT" in code and "Step 11 item skipped:" in code, "Timed-out items are logged as skips.", "Timed-out items are not clearly logged as skips.", failures)
    check("Ligand_MCS_Item_Failures.csv" in code and "item_failure_header" in code, "Rich item failure CSV is present.", "Rich item failure CSV is missing.", failures)
    check("MCS_MAX_ITEM_FAILURES > 0" in code, "Unlimited item-failure mode is supported when max item failures is 0.", "Unlimited item-failure guard logic not found.", failures)
    check("GRAPH_FULL skipped" in code, "GRAPH_FULL guardrail skip path is present.", "GRAPH_FULL guardrail skip path missing.", failures)
    check("DictWriter" in code, "Step 11 still streams outputs.", "Step 11 output streaming path missing.", failures)
    check("heartbeat_worker" in code and "⏱ Step 11 heartbeat:" in code, "Heartbeat remains present with item isolation.", "Heartbeat missing after item-isolation changes.", failures)
    check("with Pool(" not in code and "multiprocessing.Pool" not in code, "No unbounded process pool was reintroduced.", "Detected process-pool fanout.", failures)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

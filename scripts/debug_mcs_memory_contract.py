#!/usr/bin/env python3
from __future__ import annotations

import ast
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STEP11 = ROOT / "pipeline_assets" / "11_mcsMatcher.py"
STEP12 = ROOT / "pipeline_assets" / "12_Results.py"
STEP15 = ROOT / "pipeline_assets" / "15_ResultsMerged.py"
STEP16 = ROOT / "pipeline_assets" / "16_ResultsDisplay.py"
APP = ROOT / "app.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def main() -> int:
    failures: list[str] = []
    code = read(STEP11)
    _tree = ast.parse(code, filename=str(STEP11))

    if "with Pool(" in code or "multiprocessing.Pool" in code or "ProcessPoolExecutor" in code:
        failures.append("Step 11 still appears to use process-based worker fanout.")
    else:
        print("PASS: Step 11 no longer uses process-based worker fanout by default.")

    for token in [
        "WARHEAD_MCS_MAX_WORKERS",
        "WARHEAD_MCS_USE_THREADS",
        "WARHEAD_MCS_OBABEL_MAX_CONCURRENT",
        "WARHEAD_MCS_STREAM_OUTPUT",
        "WARHEAD_MCS_SKIP_EXISTING",
        "WARHEAD_MCS_DEBUG_MEMORY",
    ]:
        if token not in code:
            failures.append(f"Step 11 missing env control: {token}.")
        else:
            print(f"PASS: Step 11 exposes {token}.")

    if "[mcs-mem]" not in code:
        failures.append("Step 11 missing gated memory checkpoint logging.")
    else:
        print("PASS: Step 11 has gated memory checkpoints.")

    if "DictWriter" not in code or "Ligand_MCS_Map.csv" not in code or "Ligand_MCS_SASA_ALL_ATOMS.csv" not in code:
        failures.append("Step 11 does not appear to stream/bound CSV outputs.")
    else:
        print("PASS: Step 11 supports bounded CSV output writing.")

    if "BoundedSemaphore" not in code or "OBABEL_MAX_CONCURRENT" not in code:
        failures.append("Step 11 does not appear to bound obabel concurrency.")
    else:
        print("PASS: Step 11 bounds obabel concurrency.")

    downstream_tokens = [
        (STEP12, "Ligand_MCS_Map.csv"),
        (STEP12, "Warhead_SASA_atoms.csv"),
        (STEP15, "Ligand_3D_Atoms.csv"),
        (STEP16, "MCS_Output/MCS_SDF"),
        (STEP16, "Results_Display.csv"),
        (APP, "MCS_Output\" / \"MCS_SDF"),
    ]
    downstream_failures = []
    for path, token in downstream_tokens:
        if token not in read(path):
            downstream_failures.append(f"{path.name} missing expected token {token}.")
    if downstream_failures:
        failures.extend(downstream_failures)
    else:
        print("PASS: downstream consumers still reference expected Step 11 artifacts.")

    if os.environ.get("WARHEAD_MCS_MAX_WORKERS") == "1":
        print("INFO: current environment already pins WARHEAD_MCS_MAX_WORKERS=1.")
    else:
        print("INFO: set WARHEAD_MCS_MAX_WORKERS=1 to match the Heroku-safe default.")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("PASS: Step 11 MCS memory contract checks completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

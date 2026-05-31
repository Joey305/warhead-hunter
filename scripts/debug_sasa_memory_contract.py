#!/usr/bin/env python3
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STEP6 = ROOT / "pipeline_assets" / "6_SASA.py"
STEP7 = ROOT / "pipeline_assets" / "7_metadata.py"
STEP12 = ROOT / "pipeline_assets" / "12_Results.py"
STEP15 = ROOT / "pipeline_assets" / "15_ResultsMerged.py"
API_SASA = ROOT / "api" / "sasa_api.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def main() -> int:
    failures: list[str] = []
    code = read(STEP6)
    tree = ast.parse(code, filename=str(STEP6))

    if "ProcessPoolExecutor" in code or "multiprocessing.cpu_count()-1" in code:
        failures.append("Step 6 still appears to use process-pool fanout.")
    else:
        print("PASS: Step 6 no longer uses process-pool fanout by default.")

    if "ThreadPoolExecutor" not in code:
        failures.append("Step 6 does not expose thread-based bounded concurrency.")
    else:
        print("PASS: Step 6 uses bounded thread-based concurrency.")

    if "WARHEAD_SASA_MAX_WORKERS" not in code:
        failures.append("Step 6 missing WARHEAD_SASA_MAX_WORKERS env control.")
    else:
        print("PASS: Step 6 exposes WARHEAD_SASA_MAX_WORKERS.")

    if "WARHEAD_SASA_STREAM_OUTPUT" not in code or "append_atom_rows" not in code:
        failures.append("Step 6 does not appear to stream atom-row output.")
    else:
        print("PASS: Step 6 streams atom-row output through append_atom_rows.")

    if "WARHEAD_SASA_DEBUG_MEMORY" not in code or "[sasa-mem]" not in code:
        failures.append("Step 6 missing gated memory checkpoint logging.")
    else:
        print("PASS: Step 6 has gated debug memory checkpoints.")

    required_headers = [
        "Target", "pdb_id", "Warhead", "Residue_ID", "Variant",
        "Chain", "atom_id", "exact_atom", "x", "y", "z", "Exposure_A2",
    ]
    for header in required_headers:
        if header not in code:
            failures.append(f"Step 6 atom output header missing expected column: {header}")
    if not any("summary_csv_header" in getattr(node, "name", "") for node in tree.body if isinstance(node, ast.FunctionDef)):
        failures.append("Step 6 summary header helper not found.")
    else:
        print("PASS: Step 6 defines explicit atom and summary output headers.")

    downstream_checks = [
        (STEP7, "Resolved_SASA_Summary.csv"),
        (STEP12, "Warhead_SASA_atoms.csv"),
        (STEP15, "Exposure_A2"),
        (API_SASA, "Warhead_SASA_atoms.csv"),
    ]
    for path, token in downstream_checks:
        if token not in read(path):
            failures.append(f"Downstream consumer {path.name} missing expected token {token}.")
    if not failures:
        print("PASS: downstream consumers still reference the expected SASA artifacts/columns.")

    if os.environ.get("WARHEAD_SASA_MAX_WORKERS") == "1":
        print("INFO: current environment already pins WARHEAD_SASA_MAX_WORKERS=1.")
    else:
        print("INFO: set WARHEAD_SASA_MAX_WORKERS=1 to match the intended Heroku-safe mode.")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("PASS: Step 6 SASA memory contract checks completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

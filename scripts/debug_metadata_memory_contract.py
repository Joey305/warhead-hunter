#!/usr/bin/env python3
from __future__ import annotations

import ast
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STEP7 = ROOT / "pipeline_assets" / "7_metadata.py"
STEP8 = ROOT / "pipeline_assets" / "8_scaffold.py"
STEP9 = ROOT / "pipeline_assets" / "9_2Dmapping.py"
STEP12 = ROOT / "pipeline_assets" / "12_Results.py"
STEP16 = ROOT / "pipeline_assets" / "16_ResultsDisplay.py"
APP = ROOT / "app.py"
ROUTES = ROOT / "routes.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def find_function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def main() -> int:
    failures: list[str] = []
    code = read(STEP7)
    tree = ast.parse(code, filename=str(STEP7))

    if "Pool(" in code or "multiprocessing" in code or "ProcessPoolExecutor" in code:
        failures.append("Step 7 still appears to use process-based worker fanout.")
    else:
        print("PASS: Step 7 no longer uses process-based worker fanout by default.")

    if "WARHEAD_METADATA_MAX_WORKERS" not in code:
        failures.append("Step 7 missing WARHEAD_METADATA_MAX_WORKERS env control.")
    else:
        print("PASS: Step 7 exposes WARHEAD_METADATA_MAX_WORKERS.")

    if "WARHEAD_METADATA_USE_THREADS" not in code:
        failures.append("Step 7 missing WARHEAD_METADATA_USE_THREADS env control.")
    else:
        print("PASS: Step 7 exposes WARHEAD_METADATA_USE_THREADS.")

    if "WARHEAD_METADATA_STREAM_OUTPUT" not in code or "DictWriter" not in code:
        failures.append("Step 7 does not appear to support bounded metadata output writing.")
    else:
        print("PASS: Step 7 supports bounded CSV output writing.")

    if "WARHEAD_METADATA_DEBUG_MEMORY" not in code or "[metadata-mem]" not in code:
        failures.append("Step 7 missing gated metadata memory checkpoint logging.")
    else:
        print("PASS: Step 7 has gated metadata memory checkpoints.")

    if "@lru_cache" not in code or "def fetch_rcsb" not in code:
        failures.append("RCSB metadata lookups are not cached.")
    else:
        print("PASS: RCSB metadata lookups are cached.")

    for helper in ("get_pains_catalog", "get_brenk_catalog"):
        if helper not in code:
            failures.append(f"Missing cached FilterCatalog helper: {helper}.")
    if "FilterCatalogParams()" in code:
        pains_fn = find_function(tree, "check_pains")
        brenk_fn = find_function(tree, "check_brenk")
        for fn, label in ((pains_fn, "check_pains"), (brenk_fn, "check_brenk")):
            if fn is None:
                failures.append(f"Could not find {label}.")
                continue
            segment = ast.get_source_segment(code, fn) or ""
            if "FilterCatalogParams" in segment or "FilterCatalog(" in segment:
                failures.append(f"{label} still appears to recreate FilterCatalog objects per ligand.")
        if not failures:
            print("PASS: PAINS/BRENK catalogs are cached outside per-ligand checks.")

    required_headers = [
        "Ligand", "Name", "Formula", "Type", "SMILES", "Parent_SMILES",
        "Canonical_SMILES", "InChI", "InChIKey", "MW", "LogP", "TPSA",
        "QED", "PAINS_Hits", "Brenk_Hits",
    ]
    for header in required_headers:
        if header not in code:
            failures.append(f"Step 7 metadata header missing expected column: {header}")
    if "Ligand_Metadata.csv" not in code or "Ligand_Metadata_Failures.csv" not in code:
        failures.append("Step 7 missing expected metadata output filenames.")
    else:
        print("PASS: Step 7 preserves expected metadata output filenames.")

    downstream_tokens = [
        (STEP8, "SMILES"),
        (STEP9, "Ligand_SMILES_Map.csv"),
        (STEP12, "Resolved_SASA_Summary.csv"),
        (STEP16, "Ligand_Metadata.csv"),
        (STEP16, "Canonical_SMILES"),
        (APP, "Ligand_Metadata.csv"),
        (ROUTES, "Ligand_Resolved"),
    ]
    downstream_failures = []
    for path, token in downstream_tokens:
        if token not in read(path):
            downstream_failures.append(f"{path.name} missing expected token {token}.")
    if downstream_failures:
        failures.extend(downstream_failures)
    else:
        print("PASS: downstream consumers still reference expected metadata artifacts/columns.")

    if os.environ.get("WARHEAD_METADATA_MAX_WORKERS") == "1":
        print("INFO: current environment already pins WARHEAD_METADATA_MAX_WORKERS=1.")
    else:
        print("INFO: set WARHEAD_METADATA_MAX_WORKERS=1 to match the Heroku-safe default.")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("PASS: Step 7 metadata memory contract checks completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

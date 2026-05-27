#!/usr/bin/env python3
from __future__ import annotations

import csv
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DISPLAY_COLUMNS = [
    "Target",
    "pdb_id",
    "Chain",
    "Warhead",
    "%Exposed",
    "%Buried",
    "Total_atoms",
    "Exposed_atoms",
    "SASA_in_complex_A2",
    "SMILES",
]


def csv_info(path: Path) -> tuple[bool, int, list[str]]:
    if not path.exists():
        return False, 0, []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return True, len(rows), list(reader.fieldnames or [])


def find_csv(job_dir: Path, filename: str) -> Path:
    candidates = [
        job_dir / filename,
        job_dir / "TARGET_RESULTS" / filename,
        job_dir / "TARGET_RESULTS" / "RESULTS" / filename,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    found = list(job_dir.rglob(filename))
    return found[0] if found else candidates[0]


def report_csv(label: str, path: Path) -> tuple[bool, int, list[str]]:
    exists, count, columns = csv_info(path)
    status = "exists" if exists else "missing"
    print(f"{label}: {status} ({path.relative_to(ROOT) if path.exists() else path})")
    print(f"{label} row count: {count}")
    return exists, count, columns


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python scripts/debug_result_gallery_contract.py <job_id>")
        return 2

    job_id = argv[1]
    job_dir = ROOT / "jobs" / job_id
    if not job_dir.exists():
        print(f"FAIL: job directory not found: {job_dir}")
        return 1

    results_path = find_csv(job_dir, "Results_Display.csv")
    summary_path = find_csv(job_dir, "Resolved_SASA_Summary.csv")
    sasa_atoms_path = find_csv(job_dir, "Warhead_SASA_atoms.csv")
    ligand_atoms_path = find_csv(job_dir, "Ligand_3D_Atoms_with_SASA.csv")

    results_exists, results_rows, results_columns = report_csv("Results_Display.csv", results_path)
    missing_columns = [c for c in REQUIRED_DISPLAY_COLUMNS if c not in results_columns]
    print("required display columns:")
    for column in REQUIRED_DISPLAY_COLUMNS:
        print(f"  {'PASS' if column in results_columns else 'FAIL'} {column}")

    summary_exists, summary_rows, _ = report_csv("Resolved_SASA_Summary.csv", summary_path)
    sasa_atoms_exists, sasa_atoms_rows, _ = report_csv("Warhead_SASA_atoms.csv", sasa_atoms_path)
    ligand_atoms_exists, ligand_atoms_rows, _ = report_csv("Ligand_3D_Atoms_with_SASA.csv", ligand_atoms_path)

    template = (ROOT / "templates/results_gallery.html").read_text(encoding="utf-8", errors="ignore")
    js = (ROOT / "static/js/protacable.js").read_text(encoding="utf-8", errors="ignore")

    template_checks = {
        "Warhead": "Warhead" in template,
        "%Exposed": "%Exposed" in template or "exposed" in template.lower(),
        "%Buried": "%Buried" in template or "buried" in template.lower(),
        "SASA_in_complex_A2": "SASA_in_complex_A2" in template or "SASA" in template,
        "SMILES": "SMILES" in template or "smiles" in template.lower(),
    }
    print("template display field usage:")
    for name, ok in template_checks.items():
        print(f"  {'PASS' if ok else 'FAIL'} {name}")

    js_checks = {
        "DOMContentLoaded boot": "DOMContentLoaded" in js,
        "syncView": "window.syncView" in js,
        "2D map load": "load2DMap" in js,
        "PROTAC Builder handoff": "openProtacBuilderWithSmiles" in js,
    }
    print("protacable.js boot/syncView behavior:")
    for name, ok in js_checks.items():
        print(f"  {'PASS' if ok else 'FAIL'} {name}")

    ok = (
        results_exists
        and results_rows > 0
        and not missing_columns
        and summary_exists
        and summary_rows >= 0
        and sasa_atoms_exists
        and sasa_atoms_rows >= 0
        and ligand_atoms_exists
        and ligand_atoms_rows >= 0
        and all(template_checks.values())
        and all(js_checks.values())
    )

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

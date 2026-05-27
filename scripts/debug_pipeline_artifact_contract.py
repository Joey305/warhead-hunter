#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import job_state
from debug_cif_handoff_contract import analyze_job


def count_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            rows = list(csv.reader(handle))
        return max(0, len(rows) - 1)
    except Exception:
        return None


def header(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.reader(handle)
            return next(reader, [])
    except Exception:
        return []


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    args = parser.parse_args()

    job_dir = job_state.job_dir_for(args.job_id)
    if not job_dir.exists():
        print("FAIL: job folder does not exist")
        return 1

    cif_result = analyze_job(args.job_id)

    cifdata = job_dir / "CIFdata.csv"
    step3_root = None
    if cifdata.exists():
        rows = list(csv.DictReader(cifdata.open("r", encoding="utf-8", errors="ignore", newline="")))
        if rows:
            outdir = str(rows[0].get("outdir") or "").rstrip("/")
            if outdir:
                step3_root = job_dir / f"{outdir}_PDB"

    war_root = first_existing([
        step3_root if step3_root else job_dir / "__missing__",
        job_dir / "WAR_PDB",
        job_dir / "TARGET_RESULTS" / "WAR_PDB",
    ])
    raw_war_root = step3_root or (job_dir / "WAR_PDB")
    target_war_root = job_dir / "TARGET_RESULTS" / "WAR_PDB"

    raw_pdbs = sorted((raw_war_root if raw_war_root.exists() else job_dir / "__missing__").rglob("*.pdb"))
    target_pdbs = sorted((target_war_root if target_war_root.exists() else job_dir / "__missing__").rglob("*.pdb"))

    summary_path = first_existing([
        job_dir / "Resolved_SASA_Summary.csv",
        job_dir / "TARGET_RESULTS" / "Resolved_SASA_Summary.csv",
    ])
    sasa_summary = job_dir / "Warhead_SASA_summary.csv"
    atoms_csv = job_dir / "Warhead_SASA_atoms.csv"
    results_display = first_existing([
        job_dir / "Results_Display.csv",
        job_dir / "TARGET_RESULTS" / "Results_Display.csv",
    ])

    print(f"Job: {args.job_id}")
    print(
        "CIF handoff summary: "
        f"manifest_rows={cif_result['manifest_rows']} "
        f"verified_manifest={cif_result['verified_manifest_rows']} "
        f"step3_candidates={cif_result['candidate_rows']} "
        f"resolvable_candidates={cif_result['resolvable_candidates']}"
    )
    print(f"Step 3 expected WAR_PDB root: {raw_war_root}")
    print(f"Step 5/6 input WAR_PDB root exists: {raw_war_root.exists()}")
    print(f"Step 3 raw PDB count: {len(raw_pdbs)}")
    print(f"TARGET_RESULTS/WAR_PDB count: {len(target_pdbs)}")
    if raw_pdbs:
        print(f"Sample raw PDBs: {[str(p.relative_to(job_dir)) for p in raw_pdbs[:10]]}")

    print(f"Warhead_SASA_summary.csv rows: {count_rows(sasa_summary)}")
    print(f"Warhead_SASA_atoms.csv rows: {count_rows(atoms_csv)}")
    print(f"Resolved_SASA_Summary.csv path: {summary_path}")
    print(f"Resolved_SASA_Summary.csv rows: {count_rows(summary_path) if summary_path else None}")
    print(f"Resolved_SASA_Summary.csv columns: {header(summary_path) if summary_path else []}")
    print(f"Results_Display.csv rows: {count_rows(results_display) if results_display else None}")

    failures: list[str] = []
    failures.extend(cif_result["failures"])
    if step3_root and not raw_pdbs:
        failures.append("FAIL: Step 3 reported PDB build but no PDB files are present under WAR_PDB.")
    if raw_pdbs and not target_pdbs:
        failures.append("FAIL: Raw WAR_PDB files exist but TARGET_RESULTS/WAR_PDB is empty or missing.")
    summary_headers = header(summary_path) if summary_path else []
    if summary_path is None:
        failures.append("FAIL: Resolved_SASA_Summary.csv is missing.")
    elif count_rows(summary_path) == 0:
        failures.append("FAIL: Step 7 input table is empty/missing Ligand_Resolved.")
    elif "Ligand_Resolved" not in summary_headers:
        failures.append("FAIL: Step 7 input table is missing Ligand_Resolved.")
    if raw_pdbs and count_rows(sasa_summary) == 0:
        failures.append("FAIL: PDB files exist but Warhead_SASA_summary.csv has zero data rows.")
    if results_display is None and job_state.results_ready_from_disk(args.job_id):
        failures.append("FAIL: results_ready inferred true but Results_Display.csv is missing.")

    if failures:
        for item in failures:
            print(item)
        print("Likely upstream artifact path or PDB generation/copy mismatch.")
        return 1

    print("PASS: pipeline artifact contract is internally consistent for this job.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

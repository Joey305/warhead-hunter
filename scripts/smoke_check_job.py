#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd


def count_files(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.rglob(pattern))


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/smoke_check_job.py <job_id>")
        return 2

    job_id = sys.argv[1].strip()
    repo_root = Path(__file__).resolve().parents[1]
    job_dir = repo_root / "jobs" / job_id

    print(f"Job ID: {job_id}")
    print(f"Job dir: {job_dir}")
    print(f"Exists: {'YES' if job_dir.exists() else 'NO'}")

    if not job_dir.exists():
        print("FAIL: job directory missing")
        return 1

    target_results = job_dir / "TARGET_RESULTS"
    war_pdb_dir = first_existing([target_results / "WAR_PDB", job_dir / "WAR_PDB"])
    ligand_pdb_dir = first_existing([target_results / "LIGAND_PDB", job_dir / "LIGAND_PDB"])
    ligand_sdf_dir = first_existing([target_results / "LIGAND_SDF", job_dir / "LIGAND_SDF"])
    results_display = first_existing([target_results / "Results_Display.csv", job_dir / "Results_Display.csv"])
    resolved_summary = first_existing([target_results / "Resolved_SASA_Summary.csv", job_dir / "Resolved_SASA_Summary.csv"])
    metadata_csv = first_existing([target_results / "Ligand_Metadata.csv", job_dir / "Ligand_Metadata.csv"])
    mcs_sdf_dir = first_existing([target_results / "MCS_Output" / "MCS_SDF", job_dir / "MCS_Output" / "MCS_SDF"])

    checks = [
        ("TARGET_RESULTS", target_results),
        ("Results_Display.csv", results_display),
        ("Resolved_SASA_Summary.csv", resolved_summary),
        ("Ligand_Metadata.csv", metadata_csv),
        ("WAR_PDB", war_pdb_dir),
        ("LIGAND_PDB", ligand_pdb_dir),
        ("LIGAND_SDF", ligand_sdf_dir),
        ("MCS_SDF", mcs_sdf_dir),
    ]

    print("\nArtifacts:")
    for label, path in checks:
        status = "present" if path and path.exists() else "missing"
        print(f"- {label}: {status}{f' -> {path}' if path else ''}")

    pdb_count = count_files(war_pdb_dir, "*.pdb") if war_pdb_dir else 0
    ligand_pdb_count = count_files(ligand_pdb_dir, "*.pdb") if ligand_pdb_dir else 0
    sdf_count = count_files(ligand_sdf_dir, "*.sdf") if ligand_sdf_dir else 0
    mcs_sdf_count = count_files(mcs_sdf_dir, "*.sdf") if mcs_sdf_dir else 0

    print("\nCounts:")
    print(f"- WAR_PDB files: {pdb_count}")
    print(f"- Ligand PDB files: {ligand_pdb_count}")
    print(f"- Ligand SDF files: {sdf_count}")
    print(f"- MCS SDF files: {mcs_sdf_count}")

    if results_display and results_display.exists():
        try:
            df = pd.read_csv(results_display)
            pdb_col = "pdb_id" if "pdb_id" in df.columns else ("pdb" if "pdb" in df.columns else None)
            chain_col = "Chain" if "Chain" in df.columns else ("chain" if "chain" in df.columns else None)
            lig_col = "Warhead" if "Warhead" in df.columns else ("Ligand" if "Ligand" in df.columns else None)

            print("\nSample /api/sdf URLs:")
            if pdb_col and chain_col and lig_col:
                shown = 0
                for _, row in df.iterrows():
                    pdb = str(row.get(pdb_col, "")).strip().lower()
                    chain = str(row.get(chain_col, "")).strip().upper()
                    lig = str(row.get(lig_col, "")).strip().upper()
                    if not (pdb and chain and lig):
                        continue
                    print(f"- /api/sdf/{job_id}/{pdb}/{chain}/{lig}")
                    shown += 1
                    if shown >= 5:
                        break
                if shown == 0:
                    print("- none derivable from Results_Display.csv")
            else:
                print("- Results_Display.csv missing pdb/chain/ligand columns")
        except Exception as e:
            print(f"\nSample /api/sdf URLs:\n- failed to read Results_Display.csv: {e}")

    failures = []
    if not results_display or not results_display.exists():
        failures.append("Results_Display.csv missing")
    if not resolved_summary or not resolved_summary.exists():
        failures.append("Resolved_SASA_Summary.csv missing")
    if ligand_pdb_count == 0:
        failures.append("No ligand PDB files found")
    if sdf_count == 0 and mcs_sdf_count == 0:
        failures.append("No SDF files found")

    print("\nSummary:")
    if failures:
        print("FAIL")
        for item in failures:
            print(f"- {item}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.sdf_resolver import normalize_sdf_key, resolve_sdf_path, row_sdf_key


def find_csv(job_dir: Path, filename: str) -> Path | None:
    for candidate in [job_dir / filename, job_dir / "TARGET_RESULTS" / filename]:
        if candidate.exists():
            return candidate
    return None


def read_rows(path: Path | None) -> list[dict[str, str]]:
    if not path:
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def summary_residue_lookup(job_dir: Path) -> dict[tuple[str, str, str], str]:
    lookup: dict[tuple[str, str, str], str] = {}
    for filename in ["Ligand_3D_Atoms.csv", "Resolved_SASA_Summary.csv"]:
        for row in read_rows(find_csv(job_dir, filename)):
            pdb, chain, ligand, resid = normalize_sdf_key(
                row.get("pdb_id") or row.get("pdb"),
                row.get("Chain") or row.get("chain") or "A",
                row.get("Ligand_Resolved") or row.get("Warhead") or row.get("ligand") or row.get("Ligand"),
                row.get("Residue_ID") or row.get("residue_id") or row.get("resid"),
            )
            if pdb and chain and ligand and resid:
                lookup.setdefault((pdb, chain, ligand), resid)
    return lookup


def display_key(row: dict[str, str], residue_lookup: dict[tuple[str, str, str], str]) -> tuple[str, str, str, str]:
    pdb, chain, ligand, resid = row_sdf_key(row)
    if not resid:
        resid = residue_lookup.get((pdb, chain, ligand), "")
    return pdb, chain, ligand, resid


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python scripts/debug_sdf_contract.py <job_id>")
        return 2

    job_id = argv[1]
    job_dir = ROOT / "jobs" / job_id
    if not job_dir.exists():
        print(f"FAIL: job directory not found: {job_dir}")
        return 1

    results_path = find_csv(job_dir, "Results_Display.csv")
    if not results_path:
        print("FAIL: Results_Display.csv missing")
        return 1

    rows = read_rows(results_path)
    residue_lookup = summary_residue_lookup(job_dir)
    sdf_dirs = [
        job_dir / "MCS_Output" / "MCS_SDF",
        job_dir / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF",
    ]
    sdf_files = []
    for sdf_dir in sdf_dirs:
        if sdf_dir.exists():
            sdf_files.extend(sorted(sdf_dir.glob("*.sdf")))

    matched = []
    missing = []
    for row in rows:
        pdb, chain, ligand, resid = display_key(row, residue_lookup)
        resolved, _diag = resolve_sdf_path(job_dir, pdb, chain, ligand, resid)
        if resolved:
            matched.append(((pdb, chain, ligand, resid), resolved))
        else:
            missing.append((pdb, chain, ligand, resid))

    print(f"job_id: {job_id}")
    print(f"results_display: {results_path.relative_to(ROOT)}")
    print(f"result row count: {len(rows)}")
    for sdf_dir in sdf_dirs:
        count = len(list(sdf_dir.glob("*.sdf"))) if sdf_dir.exists() else 0
        print(f"sdf_dir: {sdf_dir.relative_to(ROOT)} exists={sdf_dir.exists()} count={count}")
    print(f"SDF count total: {len(sdf_files)}")
    print(f"matched rows: {len(matched)}")
    print(f"missing rows: {len(missing)}")

    if missing:
        print("first 20 missing expected keys:")
        for pdb, chain, ligand, resid in missing[:20]:
            expected = f"{pdb}_{chain}_{ligand}_{resid}.sdf" if resid else f"{pdb}_{chain}_{ligand}_<residue_id>.sdf"
            print(f"  {expected}")

    print("sample actual SDF files:")
    for fp in sdf_files[:20]:
        print(f"  {fp.relative_to(job_dir)}")

    if rows and not missing:
        print("PASS")
        return 0

    print("FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = REPO_ROOT / "jobs"


def norm_resid(value: object) -> str:
    raw = str(value or "").strip()
    if not raw or raw.lower() == "nan":
        return ""
    try:
        f = float(raw)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    return raw


def read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def row_count(path: Path) -> int | None:
    if not path.exists():
        return None
    return len(read_csv_rows(path))


def first_existing(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def ligand_from_row(row: dict) -> str:
    for key in ("Ligand_Resolved", "Warhead", "ligand", "Ligand"):
        value = str(row.get(key) or "").strip().upper()
        if value and value.lower() != "nan":
            return value
    return ""


def residue_from_row(row: dict) -> str:
    for key in ("Residue_ID", "residue_id", "Resid"):
        value = norm_resid(row.get(key))
        if value:
            return value
    return ""


def find_protein(job_dir: Path, row: dict, pdb: str, chain: str, ligand: str) -> Path | None:
    pdb_path = Path(str(row.get("pdb_path") or ""))
    if pdb_path.exists():
        return pdb_path

    patterns = [
        f"{pdb}_{chain}_{ligand}.pdb",
        f"{pdb}_{chain}_*.pdb",
        f"{pdb}_*.pdb",
    ]
    for pattern in patterns:
        match = next(job_dir.glob(f"WAR_PDB/**/{pattern}"), None)
        if match:
            return match
    return None


def find_sdf(job_dir: Path, pdb: str, chain: str, ligand: str) -> Path | None:
    patterns = [
        f"TARGET_RESULTS/LIGAND_SDF/{pdb}_{chain}_{ligand}.sdf",
        f"LIGAND_SDF/{pdb}_{chain}_{ligand}.sdf",
    ]
    for pattern in patterns:
        path = job_dir / pattern
        if path.exists():
            return path

    for pattern in (f"{pdb}_{chain}_{ligand}.sdf", f"{pdb}_{chain}_*.sdf", f"{pdb}_*.sdf"):
        match = next(job_dir.glob(f"**/LIGAND_SDF/{pattern}"), None)
        if match:
            return match
    return None


def sasa_source_candidates(job_dir: Path) -> list[Path]:
    return [
        job_dir / "MCS_OUTPUT" / "Ligand_MCS_SASA_ALL_ATOMS.csv",
        job_dir / "MCS_Output" / "Ligand_MCS_SASA_ALL_ATOMS.csv",
        job_dir / "TARGET_RESULTS" / "MCS_OUTPUT" / "Ligand_MCS_SASA_ALL_ATOMS.csv",
        job_dir / "TARGET_RESULTS" / "MCS_Output" / "Ligand_MCS_SASA_ALL_ATOMS.csv",
        job_dir / "TARGET_RESULTS" / "Warhead_SASA_atoms.csv",
        job_dir / "Warhead_SASA_atoms.csv",
    ]


def sasa_keys_from_rows(rows: list[dict]) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for row in rows:
        pdb = str(row.get("pdb_id") or row.get("PDB_ID") or "").strip().lower()
        chain = str(row.get("Chain") or row.get("chain") or "").strip().upper()
        resid = norm_resid(row.get("Residue_ID") or row.get("residue_id"))
        if pdb and chain and resid:
            keys.add((pdb, chain, resid))
    return keys


def build_residue_lookup(rows: list[dict]) -> dict[tuple[str, str, str], str]:
    lookup: dict[tuple[str, str, str], str] = {}
    for row in rows:
        pdb = str(row.get("pdb_id") or row.get("pdb") or "").strip().lower()
        chain = str(row.get("Chain") or row.get("chain") or "A").strip().upper() or "A"
        ligand = ligand_from_row(row)
        resid = residue_from_row(row)
        if pdb and chain and ligand and resid:
            lookup.setdefault((pdb, chain, ligand), resid)
    return lookup


def print_file_status(label: str, path: Path) -> None:
    count = row_count(path)
    if count is None:
        print(f"{label}: missing ({path})")
    else:
        print(f"{label}: exists, rows={count} ({path})")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python scripts/debug_ligand_display.py <job_id>", file=sys.stderr)
        return 2

    job_id = argv[1]
    job_dir = JOBS_DIR / job_id

    print(f"Job: {job_id}")
    print(f"Job folder exists: {'yes' if job_dir.exists() else 'no'} ({job_dir})")
    if not job_dir.exists():
        return 1

    results_path = job_dir / "Results_Display.csv"
    resolved_path = job_dir / "Resolved_SASA_Summary.csv"
    mcs_atoms_path = job_dir / "MCS_Output" / "Ligand_MCS_SASA_ALL_ATOMS.csv"
    ligand_sdf_dir = job_dir / "TARGET_RESULTS" / "LIGAND_SDF"
    war_pdb_dir = job_dir / "WAR_PDB"

    print_file_status("Results_Display.csv", results_path)
    print_file_status("Resolved_SASA_Summary.csv", resolved_path)
    print_file_status("MCS_Output/Ligand_MCS_SASA_ALL_ATOMS.csv", mcs_atoms_path)
    print(
        f"TARGET_RESULTS/LIGAND_SDF: {'exists' if ligand_sdf_dir.exists() else 'missing'}, "
        f"SDF count={len(list(ligand_sdf_dir.glob('*.sdf'))) if ligand_sdf_dir.exists() else 0}"
    )
    print(
        f"WAR_PDB: {'exists' if war_pdb_dir.exists() else 'missing'}, "
        f"PDB count={len(list(war_pdb_dir.glob('**/*.pdb'))) if war_pdb_dir.exists() else 0}"
    )

    print("\nSASA atom source candidates:")
    for path in sasa_source_candidates(job_dir):
        count = row_count(path)
        print(f"- {'exists' if path.exists() else 'missing'} rows={count if count is not None else '-'} {path}")

    api_sasa_path = first_existing(sasa_source_candidates(job_dir))
    api_sasa_rows = read_csv_rows(api_sasa_path) if api_sasa_path else []
    api_sasa_keys = sasa_keys_from_rows(api_sasa_rows)
    print(f"\nSASA source selected by API priority: {api_sasa_path or 'none'}")
    print(f"SASA keys available from selected source: {len(api_sasa_keys)}")

    results = read_csv_rows(results_path)
    residue_lookup = build_residue_lookup(read_csv_rows(resolved_path))
    displayable = 0
    sasa_available = 0
    missing_sasa = 0

    print("\nFirst 25 Results_Display rows:")
    for idx, row in enumerate(results[:25], start=1):
        pdb = str(row.get("pdb_id") or row.get("pdb") or "").strip().lower()
        chain = str(row.get("Chain") or row.get("chain") or "A").strip().upper() or "A"
        ligand = ligand_from_row(row)
        resid = residue_from_row(row) or residue_lookup.get((pdb, chain, ligand), "")

        protein = find_protein(job_dir, row, pdb, chain, ligand)
        sdf = find_sdf(job_dir, pdb, chain, ligand)
        sasa_key_found = bool(resid and (pdb, chain, resid) in api_sasa_keys)

        if protein and sdf:
            displayable += 1
        if sasa_key_found:
            sasa_available += 1
        else:
            missing_sasa += 1

        print(
            f"{idx:02d}. pdb_id={pdb or '-'} Chain={chain or '-'} "
            f"Ligand_Resolved/Warhead={ligand or '-'} Residue_ID={resid or '-'} "
            f"protein={'yes' if protein else 'no'} "
            f"sdf={'yes' if sdf else 'no'} "
            f"sasa_key={'yes' if sasa_key_found else 'no'}"
        )

    all_displayable = 0
    all_sasa_available = 0
    all_missing_sasa = 0
    for row in results:
        pdb = str(row.get("pdb_id") or row.get("pdb") or "").strip().lower()
        chain = str(row.get("Chain") or row.get("chain") or "A").strip().upper() or "A"
        ligand = ligand_from_row(row)
        resid = residue_from_row(row) or residue_lookup.get((pdb, chain, ligand), "")
        if find_protein(job_dir, row, pdb, chain, ligand) and find_sdf(job_dir, pdb, chain, ligand):
            all_displayable += 1
        if resid and (pdb, chain, resid) in api_sasa_keys:
            all_sasa_available += 1
        else:
            all_missing_sasa += 1

    print("\nSummary:")
    print(f"cards displayable by protein+SDF: {all_displayable}")
    print(f"cards with SASA overlay available: {all_sasa_available}")
    print(f"cards missing SASA overlay: {all_missing_sasa}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

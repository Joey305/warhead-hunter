#!/usr/bin/env python3
from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.sdf_resolver import expected_mcs_sdf_filename, normalize_sdf_key, resolve_sdf_path, row_sdf_key

try:
    from rdkit import Chem  # type: ignore
    from rdkit import RDLogger  # type: ignore
    RDLogger.DisableLog("rdApp.*")
except Exception:  # pragma: no cover - optional diagnostic
    Chem = None


ROOT_CAUSES = {
    "INPUT_MISSING_REQUIRED_COLUMNS",
    "INPUT_HAS_ROWS_BUT_11_GENERATED_ZERO_SDFS",
    "PARTIAL_11_GENERATION_FAILURE",
    "12_COPY_FAILURE",
    "RESULTS_DISPLAY_REFERENCES_NONEXISTENT_SDFS",
    "API_RESOLVER_MISMATCH",
    "FRONTEND_RESID_NOT_SENT",
    "VALIDATION_FALSE_POSITIVE",
    "UNKNOWN_REQUIRES_MANUAL_REVIEW",
}


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except Exception:
        return str(path)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def csv_report(path: Path) -> tuple[bool, int, list[str]]:
    if not path.exists():
        return False, 0, []
    try:
        rows = read_rows(path)
    except Exception as exc:
        print(f"  {rel(path)}: READ_ERROR {exc}")
        return True, -1, []
    columns = list(rows[0].keys()) if rows else []
    if not columns:
        try:
            with path.open(newline="", encoding="utf-8", errors="replace") as handle:
                reader = csv.reader(handle)
                columns = next(reader, [])
        except Exception:
            columns = []
    return True, len(rows), columns


def key_from_ligand_row(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return normalize_sdf_key(
        row.get("pdb_id") or row.get("pdb"),
        row.get("Chain") or row.get("chain") or "A",
        row.get("Warhead") or row.get("Ligand_Resolved") or row.get("ligand") or row.get("Ligand"),
        row.get("Residue_ID") or row.get("residue_id") or row.get("resid"),
    )


def residue_lookup_from_ligand_rows(rows: list[dict[str, str]]) -> dict[tuple[str, str, str], str]:
    lookup: dict[tuple[str, str, str], str] = {}
    for row in rows:
        pdb, chain, ligand, resid = key_from_ligand_row(row)
        if pdb and chain and ligand and resid:
            lookup.setdefault((pdb, chain, ligand), resid)
    return lookup


def display_key(row: dict[str, Any], residue_lookup: dict[tuple[str, str, str], str]) -> tuple[str, str, str, str]:
    pdb, chain, ligand, resid = row_sdf_key(row)
    if not resid:
        resid = residue_lookup.get((pdb, chain, ligand), "")
    return pdb, chain, ligand, resid


def filename_key(path: Path) -> tuple[str, str, str, str] | None:
    parts = path.stem.split("_")
    if len(parts) < 4:
        return None
    return normalize_sdf_key(parts[0], parts[1], parts[2], "_".join(parts[3:]))


def expected_name_for_key(key: tuple[str, str, str, str]) -> str:
    return expected_mcs_sdf_filename(*key)


def collect_sdfs(sdf_dir: Path) -> list[Path]:
    return sorted(sdf_dir.glob("*.sdf")) if sdf_dir.exists() else []


def validate_sdf_file(path: Path) -> tuple[bool, list[str]]:
    issues: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return False, [f"read_error={exc}"]

    if path.stat().st_size <= 0:
        issues.append("empty_file")
    if "$$$$" not in text:
        issues.append("missing_sdf_terminator")
    atom_line_re = re.compile(r"^\s*-?\d+\.\d+\s+-?\d+\.\d+\s+-?\d+\.\d+\s+[A-Z][a-z]?\b", re.M)
    has_atom_block_line = bool(atom_line_re.search(text))
    if not has_atom_block_line:
        issues.append("no_atom_block_line_detected")

    rdkit_parse_ok = None
    if Chem is not None:
        try:
            supplier = Chem.SDMolSupplier(str(path), removeHs=False)
            mol = supplier[0] if supplier and len(supplier) else None
            if mol is None:
                rdkit_parse_ok = False
            else:
                rdkit_parse_ok = True
        except Exception as exc:
            rdkit_parse_ok = False
            if not has_atom_block_line:
                issues.append(f"rdkit_exception={exc}")

    if Chem is not None and rdkit_parse_ok is False and not has_atom_block_line:
        issues.append("rdkit_parse_failed")

    return not issues, issues


def print_core_file_report(job_dir: Path) -> None:
    print("\nA. Core files")
    for rel_path in [
        "Ligand_3D_Atoms.csv",
        "MCS_Output/MCS_SDF",
        "TARGET_RESULTS/MCS_Output/MCS_SDF",
        "Results_Display.csv",
        "Target_Table/Ligand_SMILES_Map.csv",
        "Target_Table/SMILES_Ligand_Map.csv",
        "MCS_Output/Ligand_MCS_Failures.csv",
        "MCS_Output/Ligand_MCS_Map.csv",
        "MCS_Output/Ligand_MCS_SASA_ALL_ATOMS.csv",
    ]:
        path = job_dir / rel_path
        if path.is_dir():
            print(f"  {rel_path}: DIR exists=True files={len(list(path.glob('*.sdf')))}")
        elif rel_path.endswith(".csv"):
            exists, rows, cols = csv_report(path)
            print(f"  {rel_path}: exists={exists} rows={rows} cols={cols}")
        else:
            print(f"  {rel_path}: exists={path.exists()}")


def summarize_failure_reasons(job_dir: Path) -> list[str]:
    evidence: list[str] = []
    failures = job_dir / "MCS_Output" / "Ligand_MCS_Failures.csv"
    if failures.exists():
        try:
            rows = read_rows(failures)
            counter = Counter()
            for row in rows:
                err = (row.get("Error") or "").replace("\n", " ").strip()
                if "Element 'Hn' not found" in err:
                    counter["Element 'Hn' not found"] += 1
                elif "3D mol build failed" in err:
                    counter["3D mol build failed"] += 1
                elif "Size mismatch" in err:
                    counter["Size mismatch"] += 1
                elif err:
                    counter[err[:80]] += 1
            if counter:
                evidence.append("failure_csv_reasons=" + ", ".join(f"{k}:{v}" for k, v in counter.most_common(8)))
        except Exception as exc:
            evidence.append(f"failure_csv_read_error={exc}")
    return evidence


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python scripts/debug_sdf_failure_forensics.py <job_id>")
        return 2

    job_id = argv[1]
    job_dir = ROOT / "jobs" / job_id
    if not job_dir.exists():
        print(f"FAIL: job directory not found: {job_dir}")
        print("ROOT_CAUSE = UNKNOWN_REQUIRES_MANUAL_REVIEW")
        return 1

    print(f"job_id: {job_id}")
    print(f"job_dir: {rel(job_dir)}")
    print_core_file_report(job_dir)

    ligand_path = job_dir / "Ligand_3D_Atoms.csv"
    results_path = job_dir / "Results_Display.csv"
    mcs_sdf_dir = job_dir / "MCS_Output" / "MCS_SDF"
    copied_sdf_dir = job_dir / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF"

    required_cols = {"pdb_id", "Chain", "Warhead", "Residue_ID"}
    ligand_rows: list[dict[str, str]] = []
    missing_required_cols: set[str] = set()
    if ligand_path.exists():
        ligand_rows = read_rows(ligand_path)
        actual_cols = set(ligand_rows[0].keys()) if ligand_rows else set(csv_report(ligand_path)[2])
        missing_required_cols = required_cols - actual_cols
    else:
        missing_required_cols = set(required_cols)

    expected_keys = {key_from_ligand_row(row) for row in ligand_rows}
    expected_keys = {key for key in expected_keys if all(key)}
    residue_lookup = residue_lookup_from_ligand_rows(ligand_rows)

    mcs_files = collect_sdfs(mcs_sdf_dir)
    copied_files = collect_sdfs(copied_sdf_dir)
    mcs_file_keys: dict[tuple[str, str, str, str], Path] = {}
    malformed: list[Path] = []
    for fp in mcs_files:
        key = filename_key(fp)
        if key is None or not all(key):
            malformed.append(fp)
        else:
            mcs_file_keys[key] = fp

    copied_file_keys: dict[tuple[str, str, str, str], Path] = {}
    copied_malformed: list[Path] = []
    for fp in copied_files:
        key = filename_key(fp)
        if key is None or not all(key):
            copied_malformed.append(fp)
        else:
            copied_file_keys[key] = fp

    missing_after_11 = sorted(expected_keys - set(mcs_file_keys))
    extra_after_11 = sorted(set(mcs_file_keys) - expected_keys)
    missing_after_12 = sorted(expected_keys - set(copied_file_keys))

    print("\nB. Input-to-SDF mapping")
    print(f"  expected SDF count from Ligand_3D_Atoms.csv: {len(expected_keys)}")
    print(f"  actual SDF count after 11_mcsMatcher.py: {len(mcs_files)}")
    print(f"  parsed SDF filename keys after 11: {len(mcs_file_keys)}")
    print(f"  missing expected SDFs after 11: {len(missing_after_11)}")
    for key in missing_after_11[:30]:
        print(f"    missing_after_11: {expected_name_for_key(key)}")
    print(f"  extra SDFs after 11: {len(extra_after_11)}")
    for key in extra_after_11[:20]:
        print(f"    extra_after_11: {expected_name_for_key(key)}")
    print(f"  malformed SDF filenames after 11: {len(malformed)}")
    for fp in malformed[:20]:
        print(f"    malformed_after_11: {rel(fp)}")

    print("\nC. Display-to-SDF mapping")
    display_rows = read_rows(results_path) if results_path.exists() else []
    display_missing: list[tuple[tuple[str, str, str, str], bool, bool, bool]] = []
    display_matched = 0
    api_matched = 0
    resolver_mismatch: list[tuple[tuple[str, str, str, str], str]] = []
    for row in display_rows:
        key = display_key(row, residue_lookup)
        expected_name = expected_name_for_key(key) if all(key) else ""
        in_ligand_input = key in expected_keys
        in_mcs = key in mcs_file_keys
        in_copied = key in copied_file_keys
        if in_mcs or in_copied:
            display_matched += 1
        else:
            display_missing.append((key, in_ligand_input, in_mcs, in_copied))

        resolved, diag = resolve_sdf_path(job_dir, key[0], key[1], key[2], key[3])
        if resolved:
            api_matched += 1
        elif in_mcs or in_copied:
            resolver_mismatch.append((key, str(diag.get("searched_paths", []))))

        if not expected_name:
            display_missing.append((key, in_ligand_input, in_mcs, in_copied))

    print(f"  display row count: {len(display_rows)}")
    print(f"  matched display rows by direct files: {display_matched}")
    print(f"  missing display rows: {len(display_missing)}")
    print(f"  API resolver matched rows: {api_matched}")
    for key, in_input, in_mcs, in_copied in display_missing[:30]:
        name = expected_name_for_key(key) if all(key) else str(key)
        print(
            "    missing_display: "
            f"{name} in_Ligand_3D_Atoms={in_input} in_MCS_Output={in_mcs} in_TARGET_RESULTS={in_copied}"
        )
    print(f"  API resolver mismatches where direct file exists: {len(resolver_mismatch)}")
    for key, searched in resolver_mismatch[:20]:
        print(f"    resolver_mismatch: {expected_name_for_key(key)} searched={searched}")

    print("\nD. File validity checks")
    sample_files = sorted({*mcs_files, *copied_files})[:20]
    invalid_sample: list[tuple[Path, list[str]]] = []
    print(f"  sampled SDF files: {len(sample_files)}")
    for fp in sample_files:
        ok, issues = validate_sdf_file(fp)
        status = "OK" if ok else "INVALID"
        print(f"    {status}: {rel(fp)} size={fp.stat().st_size if fp.exists() else 0} issues={issues}")
        if not ok:
            invalid_sample.append((fp, issues))

    print("\nE. API resolver parity")
    print(f"  resolver matched display rows: {api_matched}/{len(display_rows)}")
    print(f"  resolver mismatches: {len(resolver_mismatch)}")

    frontend_resid_ok = True
    for js_path in [ROOT / "static/js/3Drender.js", ROOT / "static/js/protacable.js"]:
        try:
            text = js_path.read_text(encoding="utf-8", errors="replace")
            if "resid" not in text or "/api/sdf" not in text:
                frontend_resid_ok = False
        except Exception:
            frontend_resid_ok = False
    print(f"  frontend appears to send resid: {frontend_resid_ok}")

    evidence: list[str] = []
    broken = False
    root_cause = "UNKNOWN_REQUIRES_MANUAL_REVIEW"

    if missing_required_cols:
        root_cause = "INPUT_MISSING_REQUIRED_COLUMNS"
        evidence.append(f"missing_required_columns={sorted(missing_required_cols)}")
        broken = True
    elif expected_keys and not mcs_files:
        root_cause = "INPUT_HAS_ROWS_BUT_11_GENERATED_ZERO_SDFS"
        evidence.append(f"expected_groups={len(expected_keys)} actual_sdfs=0")
        evidence.extend(summarize_failure_reasons(job_dir))
        broken = True
    elif missing_after_11:
        root_cause = "PARTIAL_11_GENERATION_FAILURE"
        evidence.append(f"expected_groups={len(expected_keys)} actual_sdfs={len(mcs_files)} missing_after_11={len(missing_after_11)}")
        evidence.append("first_missing=" + ", ".join(expected_name_for_key(k) for k in missing_after_11[:10]))
        evidence.extend(summarize_failure_reasons(job_dir))
        broken = True
    elif expected_keys and mcs_files and len(copied_files) < len(mcs_files):
        root_cause = "12_COPY_FAILURE"
        evidence.append(f"mcs_sdfs={len(mcs_files)} copied_sdfs={len(copied_files)} missing_after_12={len(missing_after_12)}")
        broken = True
    elif display_missing:
        root_cause = "RESULTS_DISPLAY_REFERENCES_NONEXISTENT_SDFS"
        evidence.append(f"display_rows={len(display_rows)} missing_display_rows={len(display_missing)}")
        broken = True
    elif resolver_mismatch:
        root_cause = "API_RESOLVER_MISMATCH"
        evidence.append(f"resolver_mismatches={len(resolver_mismatch)}")
        broken = True
    elif not frontend_resid_ok:
        root_cause = "FRONTEND_RESID_NOT_SENT"
        evidence.append("frontend JS did not contain expected resid query construction")
        broken = True
    elif invalid_sample:
        root_cause = "UNKNOWN_REQUIRES_MANUAL_REVIEW"
        evidence.append(f"invalid_sdf_sample_count={len(invalid_sample)}")
        broken = True
    else:
        root_cause = "VALIDATION_FALSE_POSITIVE"
        evidence.append("SDF contract is intact for this job; no missing files or resolver mismatch found")

    if root_cause not in ROOT_CAUSES:
        root_cause = "UNKNOWN_REQUIRES_MANUAL_REVIEW"

    print("\nF. Error summary")
    print(f"ROOT_CAUSE = {root_cause}")
    for item in evidence:
        print(f"  evidence: {item}")
    print("PASS" if not broken else "FAIL")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

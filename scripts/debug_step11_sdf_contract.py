#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.sdf_resolver import (
    expected_mcs_sdf_filename,
    mcs_sdf_roots,
    normalize_sdf_key,
    parse_mcs_sdf_filename,
    resolve_sdf_path,
    row_sdf_key,
)

try:
    from api.randy_archive_client import find_asset as randy_find_asset
except Exception:
    randy_find_asset = None


def find_first(job_dir: Path, names: Iterable[str]) -> Path | None:
    for name in names:
        for candidate in [job_dir / name, job_dir / "TARGET_RESULTS" / name]:
            if candidate.exists():
                return candidate
    return None


def read_csv_rows(path: Path | None) -> List[Dict[str, str]]:
    if not path:
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def nonempty_sdfs(job_dir: Path) -> List[Path]:
    files: List[Path] = []
    for root in mcs_sdf_roots(job_dir):
        if not root.exists():
            continue
        for fp in sorted(root.glob("*.sdf")):
            try:
                if fp.is_file() and fp.stat().st_size > 0:
                    files.append(fp)
            except Exception:
                continue
    return files


def parse_job_log(job_dir: Path) -> Dict[str, bool]:
    job_log = job_dir / "job.log"
    text = job_log.read_text(encoding="utf-8", errors="replace") if job_log.exists() else ""
    return {
        "exists": job_log.exists(),
        "step11_started": "Running 11_mcsMatcher.py" in text,
        "step11_after_logged": "after 11_mcsMatcher.py" in text,
        "step11_validation_passed": "SDF validation PASS after 11_mcsMatcher.py" in text,
        "step12_validation_passed": "SDF validation PASS after 12_Results.py" in text,
        "step11_summary_logged": "Step 11 summary:" in text,
        "step11_no_output_killed": "11_mcsMatcher.py produced no output for >" in text,
        "step12_started": "Running 12_Results.py" in text,
        "step16_completed": "16_ResultsDisplay.py" in text and "Results_Display.csv" in text,
        "pipeline_finished": "PIPELINE FINISHED SUCCESSFULLY" in text,
        "critical_error": "CRITICAL ERROR" in text,
    }


def artifact_index_rows(job_dir: Path) -> List[Tuple[str, str, str, str]]:
    rows = read_csv_rows(find_first(job_dir, ["Results_Display.csv"]))
    out: List[Tuple[str, str, str, str]] = []
    for row in rows:
        out.append(row_sdf_key(row))
    return out


def similar_sdfs(job_dir: Path, pdb: str, chain: str, ligand: str) -> List[str]:
    out: List[str] = []
    for fp in nonempty_sdfs(job_dir):
        parsed = parse_mcs_sdf_filename(fp.name)
        if not parsed:
            continue
        fpdb, fchain, fligand, fresid = parsed
        if fpdb == pdb and fchain == chain and fligand == ligand:
            out.append(fp.name)
    return out[:20]


def results_rows_missing_sdf(job_dir: Path) -> List[Tuple[str, str, str, str]]:
    missing: List[Tuple[str, str, str, str]] = []
    for row in read_csv_rows(find_first(job_dir, ["Results_Display.csv"])):
        key = row_sdf_key(row)
        resolved, _diag = resolve_sdf_path(job_dir, *key)
        if not resolved:
            missing.append(key)
    return missing


def step11_failure_rows(job_dir: Path) -> List[Dict[str, str]]:
    return read_csv_rows(find_first(job_dir, ["MCS_Output/Ligand_MCS_SDF_Failures.csv", "Ligand_MCS_SDF_Failures.csv"]))


def print_check(label: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    suffix = f" {detail}" if detail else ""
    print(f"{status}: {label}{suffix}")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Diagnose Step 11 SDF contract issues for a job.")
    parser.add_argument("job_id")
    parser.add_argument("--pdb", default="")
    parser.add_argument("--chain", default="")
    parser.add_argument("--ligand", default="")
    parser.add_argument("--resid", default="")
    args = parser.parse_args(argv[1:])

    job_id = args.job_id.strip()
    job_dir = ROOT / "jobs" / job_id
    target_key = normalize_sdf_key(args.pdb, args.chain, args.ligand, args.resid) if args.pdb and args.chain and args.ligand else None

    print(f"job_id: {job_id}")
    print(f"job_dir: {'present' if job_dir.exists() else 'missing'}")
    if not job_dir.exists():
        print_check("job directory", False, str(job_dir))
        return 1

    log_state = parse_job_log(job_dir)
    print_check("job.log exists", log_state["exists"])
    print_check("Step 11 started", log_state["step11_started"])
    print_check("job_runner logged after Step 11", log_state["step11_after_logged"] or log_state["step11_validation_passed"])
    print_check("Step 11 validation passed", log_state["step11_validation_passed"])
    print_check("Step 12 validation passed", log_state["step12_validation_passed"])
    print_check("Step 11 summary logged", log_state["step11_summary_logged"])
    print_check("Step 11 not killed by no-output watchdog", not log_state["step11_no_output_killed"])
    print_check("Step 12 started", log_state["step12_started"])
    print_check("pipeline finished", log_state["pipeline_finished"])
    print_check("no critical error in log", not log_state["critical_error"])

    sdf_files = nonempty_sdfs(job_dir)
    print_check("MCS_Output/MCS_SDF exists", any(root.exists() for root in mcs_sdf_roots(job_dir)))
    print(f"nonempty_sdf_count: {len(sdf_files)}")

    results_path = find_first(job_dir, ["Results_Display.csv"])
    print_check("Results_Display.csv exists", results_path is not None, str(results_path.relative_to(ROOT)) if results_path else "")
    missing_rows = results_rows_missing_sdf(job_dir) if results_path else []
    print_check("Results_Display rows all resolve to SDF", not missing_rows, f"missing_rows={len(missing_rows)}")
    if missing_rows:
        print(f"missing_display_rows: {missing_rows[:10]}")

    failure_rows = step11_failure_rows(job_dir)
    print(f"step11_sdf_failure_rows: {len(failure_rows)}")

    if target_key:
        pdb, chain, ligand, resid = target_key
        expected = expected_mcs_sdf_filename(pdb, chain, ligand, resid)
        resolved, diag = resolve_sdf_path(job_dir, pdb, chain, ligand, resid)
        print(f"target_expected_sdf: {expected}")
        print_check("target SDF resolves locally", bool(resolved), str(resolved.relative_to(job_dir)) if resolved else "")
        matches = similar_sdfs(job_dir, pdb, chain, ligand)
        print(f"similar_sdfs: {matches[:10]}")
        in_results = target_key in artifact_index_rows(job_dir)
        print_check("target row present in Results_Display.csv", in_results)
        if randy_find_asset is not None:
            archived = randy_find_asset(job_id, pdb=pdb, chain=chain, ligand=ligand, resid=resid, kind="sdf")
            print_check("target SDF available in RANDY fallback", bool(archived), str((archived or {}).get("relative_path") or ""))
        if not resolved and diag:
            print(f"resolver_sample_candidates: {diag.get('matching_candidates') or []}")

    return 0 if not missing_rows else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

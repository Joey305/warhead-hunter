#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import job_state

MANIFEST_FILE = "CIF_Download_Manifest.csv"


def parse_bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def normalize_pdb_id(value: str) -> str:
    return str(value or "").strip().lower()


def count_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            rows = list(csv.reader(handle))
        return max(0, len(rows) - 1)
    except Exception:
        return None


def read_csv_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return pd.read_csv(path, dtype=str).fillna("").to_dict("records")
    except Exception:
        return []


def verify_file(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 0
    except Exception:
        return False


def safe_resolve(path: Path, job_dir: Path) -> Path | None:
    try:
        resolved = path.resolve()
    except Exception:
        return None
    try:
        resolved.relative_to(job_dir)
    except ValueError:
        return None
    return resolved


def resolve_recorded_path(recorded: str, job_dir: Path) -> Path | None:
    raw = str(recorded or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    candidates = [candidate] if candidate.is_absolute() else [job_dir / candidate, Path.cwd() / candidate]
    for item in candidates:
        safe = safe_resolve(item, job_dir)
        if safe and verify_file(safe):
            return safe
    return None


def actual_cif_files(job_dir: Path, cif_base: str) -> list[Path]:
    root = job_dir / cif_base
    if not root.exists():
        return []
    files: list[Path] = []
    for pattern in ("*.cif", "*.CIF", "*.cif.gz", "*.CIF.GZ"):
        files.extend(sorted(root.rglob(pattern)))
    return sorted(set(files))


def legacy_candidates(job_dir: Path, cif_base: str, protein: str, pdb_id: str) -> list[Path]:
    root = job_dir / cif_base / protein
    names = []
    for stem in (pdb_id, pdb_id.lower(), pdb_id.upper()):
        for suffix in (".cif", ".CIF", ".cif.gz", ".CIF.GZ"):
            name = f"{stem}{suffix}"
            if name not in names:
                names.append(name)
    return [root / name for name in names]


def build_candidate_rows(job_dir: Path) -> tuple[pd.DataFrame | None, int]:
    filtered = job_dir / "filtered_data.csv"
    sim = job_dir / "chain_similarity.csv"
    queries = job_dir / "queries.csv"
    if not filtered.exists() or not sim.exists() or not queries.exists():
        return None, 0

    filtered_df = pd.read_csv(filtered)
    sim_df = pd.read_csv(sim)
    query = pd.read_csv(queries).iloc[0]
    min_id = float(query["MinID"])
    merged = filtered_df.merge(sim_df, on=["protein", "pdb", "chain"], how="inner")
    final_set = merged[merged["Similarity"] >= min_id].drop_duplicates()
    if final_set.empty and not merged.empty:
        final_set = merged[merged["Similarity"] >= 30.0].drop_duplicates()
    return final_set, len(merged)


def analyze_job(job_id: str) -> dict:
    job_dir = job_state.job_dir_for(job_id)
    result = {
        "job_dir": job_dir,
        "failures": [],
        "notes": [],
        "manifest_rows": 0,
        "verified_manifest_rows": 0,
        "candidate_rows": 0,
        "merged_rows": 0,
        "resolvable_candidates": 0,
        "raw_pdb_count": 0,
        "cif_base": "WAR",
    }

    if not job_dir.exists():
        result["failures"].append("FAIL: job folder does not exist")
        return result

    queries_path = job_dir / "queries.csv"
    cifdata_path = job_dir / "CIFdata.csv"
    manifest_path = job_dir / MANIFEST_FILE
    input_path = job_dir / "input.csv"

    if not input_path.exists():
        result["failures"].append("FAIL: input.csv is missing")
    if not queries_path.exists():
        result["failures"].append("FAIL: queries.csv is missing")
    if not cifdata_path.exists():
        result["failures"].append("FAIL: CIFdata.csv is missing")

    cifdata_rows = read_csv_records(cifdata_path)
    if cifdata_rows:
        result["cif_base"] = str(cifdata_rows[0].get("outdir") or "WAR")

    manifest_rows = read_csv_records(manifest_path)
    result["manifest_rows"] = len(manifest_rows)
    result["verified_manifest_rows"] = sum(1 for row in manifest_rows if parse_bool(row.get("exists")))

    cif_files = actual_cif_files(job_dir, result["cif_base"])
    cif_index: dict[tuple[str, str], list[Path]] = {}
    for path in cif_files:
        protein = path.parent.name.lower()
        stem = path.name
        for suffix in (".cif.gz", ".CIF.GZ", ".cif", ".CIF"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        cif_index.setdefault((protein, normalize_pdb_id(stem)), []).append(path)

    final_set, merged_rows = build_candidate_rows(job_dir)
    result["merged_rows"] = merged_rows
    if final_set is not None:
        result["candidate_rows"] = len(final_set)

    manifest_index: dict[tuple[str, str], list[dict]] = {}
    for row in manifest_rows:
        key = (str(row.get("target") or "").strip().lower(), normalize_pdb_id(row.get("pdb_id", "")))
        manifest_index.setdefault(key, []).append(row)

    resolvable = 0
    sample_missing = []
    if final_set is not None:
        for row in final_set.itertuples(index=False):
            key = (str(row.protein).strip().lower(), normalize_pdb_id(row.pdb))
            resolved = None
            for manifest_row in manifest_index.get(key, []):
                for field in ("actual_path", "intended_path"):
                    resolved = resolve_recorded_path(manifest_row.get(field, ""), job_dir)
                    if resolved:
                        break
                if resolved:
                    break
            if resolved is None:
                for candidate in legacy_candidates(job_dir, result["cif_base"], str(row.protein), normalize_pdb_id(row.pdb)):
                    if verify_file(candidate):
                        resolved = candidate
                        break
            if resolved is None:
                files = cif_index.get(key, [])
                resolved = files[0] if files else None

            if resolved is not None:
                resolvable += 1
            elif len(sample_missing) < 10:
                sample_missing.append(f"{row.protein}/{normalize_pdb_id(row.pdb)}")

    result["resolvable_candidates"] = resolvable

    raw_pdb_root = job_dir / f"{result['cif_base']}_PDB"
    result["raw_pdb_count"] = len(sorted(raw_pdb_root.rglob("*.pdb"))) if raw_pdb_root.exists() else 0

    if result["manifest_rows"] and result["verified_manifest_rows"] == 0:
        result["failures"].append("FAIL: Step 1 log may have claimed downloads, but manifest has 0 verified CIF files.")
    if result["candidate_rows"] and result["resolvable_candidates"] == 0:
        result["failures"].append(
            "FAIL: Step 3 build candidates exist, but 0 have resolvable CIF paths. "
            f"Sample missing keys: {sample_missing}"
        )
    if result["candidate_rows"] and result["resolvable_candidates"] < result["candidate_rows"]:
        result["failures"].append(
            f"FAIL: Only {result['resolvable_candidates']} of {result['candidate_rows']} Step 3 candidates have resolvable CIF paths."
        )
    if result["candidate_rows"] and result["resolvable_candidates"] and result["raw_pdb_count"] == 0:
        result["failures"].append(
            "FAIL: Step 3 candidates are resolvable, but WAR_PDB contains zero PDB files. "
            "This suggests a later parse/build failure."
        )
    if not manifest_path.exists():
        result["notes"].append("NOTE: CIF_Download_Manifest.csv is missing; using legacy path checks only.")
    if cif_files:
        result["notes"].append(
            f"NOTE: actual CIF files under {result['cif_base']}/ = {len(cif_files)}; "
            f"sample={[str(p.relative_to(job_dir)) for p in cif_files[:10]]}"
        )

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    args = parser.parse_args()

    result = analyze_job(args.job_id)
    job_dir = result["job_dir"]

    print(f"Job: {args.job_id}")
    print(f"Job dir exists: {job_dir.exists()}")
    print(f"input.csv exists: {(job_dir / 'input.csv').exists()}")
    print(f"queries.csv exists: {(job_dir / 'queries.csv').exists()}")
    print(f"CIFdata.csv exists: {(job_dir / 'CIFdata.csv').exists()}")
    print(f"{MANIFEST_FILE} exists: {(job_dir / MANIFEST_FILE).exists()}")
    print(f"Manifest rows: {result['manifest_rows']}")
    print(f"Verified manifest rows: {result['verified_manifest_rows']}")
    print(f"Step 2 merged rows: {result['merged_rows']}")
    print(f"Step 3 candidate rows: {result['candidate_rows']}")
    print(f"Resolvable Step 3 candidates: {result['resolvable_candidates']}")
    print(f"Expected Step 3 output root: {job_dir / (result['cif_base'] + '_PDB')}")
    print(f"Actual Step 3 PDB output count: {result['raw_pdb_count']}")
    print(f"WAR root: {job_dir / result['cif_base']}")
    print(f"WAR file count: {len(actual_cif_files(job_dir, result['cif_base']))}")

    for note in result["notes"]:
        print(note)

    if result["failures"]:
        for item in result["failures"]:
            print(item)
        return 1

    if result["candidate_rows"] and result["candidate_rows"] == result["resolvable_candidates"] == result["raw_pdb_count"]:
        print(
            f"PASS: {result['candidate_rows']} Step 3 build candidates, "
            f"{result['resolvable_candidates']} resolvable CIF files, "
            f"{result['raw_pdb_count']} PDB outputs."
        )
    else:
        print("PASS: CIF handoff contract is internally consistent for this job.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import argparse
import pandas as pd
import requests
import urllib3

# Disable SSL Warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =============================================================================
# TOP-LEVEL DEFAULTS
# =============================================================================
min_mw = 250.0
max_mw = 700.0
min_id_default = 90.0
default_outdir = "WAR"
default_limit = None

# CONFIG
MAX_DOWNLOAD_WORKERS = 12
MANIFEST_FILE = "CIF_Download_Manifest.csv"
MANIFEST_COLUMNS = [
    "pdb_id",
    "requested_pdb_id",
    "target",
    "source",
    "query",
    "download_url",
    "status",
    "intended_path",
    "actual_path",
    "exists",
    "size_bytes",
    "error",
    "downloaded_at",
]

# HEADERS (Mimic a Browser to bypass Firewall/404s)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_pdb_id(pdb_id: str) -> str:
    return str(pdb_id or "").strip().lower()


def bool_to_csv(value: bool) -> str:
    return "true" if value else "false"


def path_for_csv(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def verify_cif_file(path: Path) -> tuple[bool, int, str]:
    try:
        if not path.exists():
            return False, 0, "missing"
        size = path.stat().st_size
        if size <= 0:
            return False, size, "empty"
        with path.open("rb") as handle:
            sample = handle.read(256)
        if not sample:
            return False, size, "unreadable"
        return True, size, ""
    except Exception as exc:
        return False, 0, f"verify_error: {exc}"


def existing_case_variant(outdir: Path, pdb_norm: str) -> Path | None:
    for suffix in (".cif", ".CIF", ".cif.gz"):
        for candidate_name in (f"{pdb_norm}{suffix}", f"{pdb_norm.upper()}{suffix}"):
            candidate = outdir / candidate_name
            ok, _size, _err = verify_cif_file(candidate)
            if ok:
                return candidate
    return None


###############################################################################
# SEARCH LOGIC
###############################################################################

def rcsb_text_search(term, rows=500):
    term = (term or "").strip()
    if not term:
        return []

    url = "https://search.rcsb.org/rcsbsearch/v2/query"
    query = {
        "query": {"type": "terminal", "service": "full_text", "parameters": {"value": term}},
        "return_type": "entry",
        "request_options": {"paginate": {"start": 0, "rows": rows}},
    }
    try:
        r = requests.post(url, json=query, headers=HEADERS, timeout=30, verify=False)
        if r.status_code == 200:
            return sorted({x["identifier"] for x in r.json().get("result_set", [])})
    except Exception:
        pass

    print(f"    🔄 Switching to EBI Backup Search for '{term}'...")
    ebi_url = f"https://www.ebi.ac.uk/pdbe/search/pdb/select?q={term}&wt=json&rows={rows}"
    try:
        r = requests.get(ebi_url, headers=HEADERS, timeout=30, verify=False)
        if r.status_code == 200:
            docs = r.json().get("response", {}).get("docs", [])
            return sorted({d["pdb_id"] for d in docs})
    except Exception:
        pass

    return []


###############################################################################
# DOWNLOADER
###############################################################################

def download_single_cif(pdb_id: str, outdir: Path, protein: str, query: str) -> dict:
    requested_pdb = str(pdb_id or "").strip()
    pdb_norm = normalize_pdb_id(requested_pdb)
    pdb_code = pdb_norm.upper()
    intended_path = outdir / f"{pdb_norm}.cif"

    result = {
        "pdb_id": pdb_norm,
        "requested_pdb_id": requested_pdb,
        "target": protein,
        "source": "",
        "query": query,
        "download_url": "",
        "status": "failed",
        "intended_path": path_for_csv(intended_path),
        "actual_path": "",
        "exists": "false",
        "size_bytes": 0,
        "error": "",
        "downloaded_at": utc_now_iso(),
    }

    intended_ok, intended_size, intended_err = verify_cif_file(intended_path)
    if intended_ok:
        result.update({
            "source": "cache",
            "status": "cached",
            "actual_path": path_for_csv(intended_path),
            "exists": "true",
            "size_bytes": intended_size,
        })
        return result

    cached_variant = existing_case_variant(outdir, pdb_norm)
    if cached_variant is not None:
        cached_ok, cached_size, cached_err = verify_cif_file(cached_variant)
        if cached_ok:
            result.update({
                "source": "cache",
                "status": "cached_legacy_case",
                "actual_path": path_for_csv(cached_variant),
                "exists": "true",
                "size_bytes": cached_size,
            })
            return result
        result["error"] = cached_err or "cached_variant_invalid"

    sources = [
        ("rcsb", f"https://files.rcsb.org/download/{pdb_code}.cif"),
        ("pdbe", f"https://www.ebi.ac.uk/pdbe/entry-files/download/{pdb_code}.cif"),
    ]

    for source_name, url in sources:
        result["source"] = source_name
        result["download_url"] = url
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, verify=False)
            if r.status_code != 200:
                result["error"] = f"http_{r.status_code}"
                continue

            outdir.mkdir(parents=True, exist_ok=True)
            with intended_path.open("wb") as handle:
                handle.write(r.content)

            ok, size_bytes, verify_error = verify_cif_file(intended_path)
            result["exists"] = bool_to_csv(ok)
            result["size_bytes"] = size_bytes
            result["actual_path"] = path_for_csv(intended_path) if intended_path.exists() else ""
            if ok:
                result["status"] = "verified"
                result["error"] = ""
                return result

            result["status"] = "failed_verification"
            result["error"] = verify_error or "verification_failed"
        except Exception as exc:
            result["error"] = repr(exc)

    if not result["error"]:
        result["error"] = intended_err or "failed_all_sources"
    return result


###############################################################################
# PROCESSOR
###############################################################################

def process_protein_row(row, root_outdir: Path, limit=None):
    protein = str(row["protein"]).strip()
    query = str(row["search_query"]).strip()

    print(f"\n=== Processing {protein} ===")
    protein_dir = root_outdir / protein
    protein_dir.mkdir(parents=True, exist_ok=True)

    ids = rcsb_text_search(query, rows=500)
    if not ids and protein != query:
        ids = rcsb_text_search(protein, rows=500)

    print(f"  → Found {len(ids)} candidates")
    if limit is not None:
        ids = ids[:limit]

    attempted = 0
    verified_ids = []
    failed_rows = []
    manifest_rows = []

    if ids:
        with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as exe:
            futures = {exe.submit(download_single_cif, pdb, protein_dir, protein, query): pdb for pdb in ids}
            for fut in as_completed(futures):
                attempted += 1
                row_result = fut.result()
                manifest_rows.append(row_result)
                if row_result["exists"] == "true" and row_result["actual_path"]:
                    verified_ids.append(row_result["pdb_id"])
                else:
                    failed_rows.append(row_result)
                    print(
                        "    ✖ "
                        f"{row_result['requested_pdb_id'] or row_result['pdb_id']} "
                        f"source={row_result['source'] or 'unknown'} "
                        f"url={row_result['download_url'] or 'n/a'} "
                        f"path={row_result['intended_path']} "
                        f"error={row_result['error'] or 'unknown'} "
                        f"exists={row_result['exists']} size={row_result['size_bytes']}"
                    )

    sample_paths = [row["actual_path"] for row in manifest_rows if row["exists"] == "true" and row["actual_path"]][:5]
    print(
        f"  → Candidates={len(ids)} attempted={attempted} "
        f"verified={len(verified_ids)} failed={len(failed_rows)}"
    )
    print(f"  → CIF output directory: {protein_dir.resolve()}")
    if sample_paths:
        print(f"  → Sample verified CIFs: {sample_paths}")

    return {
        "protein": protein,
        "query": query,
        "n_candidates": len(ids),
        "n_attempted": attempted,
        "n_verified": len(verified_ids),
        "verified_ids": sorted(set(verified_ids)),
        "manifest_rows": manifest_rows,
        "protein_dir": path_for_csv(protein_dir),
    }


###############################################################################
# MAIN
###############################################################################

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", type=str)
    parser.add_argument("--outdir", type=str, default=default_outdir)
    parser.add_argument("--limit", type=int, default=default_limit)
    parser.add_argument("--mw_min", type=float, default=min_mw)
    parser.add_argument("--mw_max", type=float, default=max_mw)
    parser.add_argument("--min_id", type=float, default=min_id_default)

    if len(sys.argv) > 1:
        args = parser.parse_args()
        print(f"🤖 AUTOMATED MODE: Processing {args.auto}")
    else:
        csvs = list(Path.cwd().glob("*.csv"))
        args = argparse.Namespace(
            auto=str(csvs[0]) if csvs else None,
            outdir=default_outdir,
            limit=default_limit,
            mw_min=min_mw,
            mw_max=max_mw,
            min_id=min_id_default,
        )

    if not args.auto or not os.path.exists(args.auto):
        print("❌ Input CSV not found.")
        sys.exit(1)

    df = pd.read_csv(args.auto)
    root_outdir = Path(args.outdir)
    root_outdir.mkdir(parents=True, exist_ok=True)

    summary = {}
    cifdata_rows = []
    manifest_rows = []

    for _, row in df.iterrows():
        result = process_protein_row(row, root_outdir, args.limit)
        summary[result["protein"]] = result["verified_ids"]
        manifest_rows.extend(result["manifest_rows"])
        cifdata_rows.append({
            "protein": result["protein"],
            "n_candidates": result["n_candidates"],
            "n_attempted": result["n_attempted"],
            "n_downloaded": result["n_verified"],
            "n_verified": result["n_verified"],
            "outdir": args.outdir,
            "protein_dir": result["protein_dir"],
            "manifest_path": MANIFEST_FILE,
        })

    with open("summary.json", "w", encoding="utf-8") as handle:
        json.dump(summary, handle)

    pd.DataFrame(cifdata_rows).to_csv("CIFdata.csv", index=False)
    pd.DataFrame(manifest_rows, columns=MANIFEST_COLUMNS).to_csv(MANIFEST_FILE, index=False)

    pd.DataFrame([{
        "MW_min": args.mw_min,
        "MW_max": args.mw_max,
        "MinID": args.min_id,
    }]).to_csv("queries.csv", index=False)

    verified_count = sum(1 for row in manifest_rows if row["exists"] == "true")
    failed_count = len(manifest_rows) - verified_count
    sample_verified = [row["actual_path"] for row in manifest_rows if row["exists"] == "true" and row["actual_path"]][:10]

    print("\n=== DONE ===")
    print(
        f"✅ Wrote queries.csv with MW_min={args.mw_min}, "
        f"MW_max={args.mw_max}, MinID={args.min_id}"
    )
    print(
        f"✅ CIF manifest rows={len(manifest_rows)} verified={verified_count} "
        f"failed={failed_count} root={root_outdir.resolve()}"
    )
    if sample_verified:
        print(f"✅ Sample manifest CIF paths: {sample_verified}")


if __name__ == "__main__":
    main()

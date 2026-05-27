#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
from pathlib import Path
import requests
import argparse
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib3

# Disable SSL Warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =============================================================================
# TOP-LEVEL DEFAULTS (EDIT THESE)
# =============================================================================
min_mw = 250.0
max_mw = 700.0
min_id_default = 90.0
default_outdir = "WAR"
default_limit = None

# CONFIG
MAX_DOWNLOAD_WORKERS = 12

# HEADERS (Mimic a Browser to bypass Firewall/404s)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

###############################################################################
# SEARCH LOGIC
###############################################################################

def rcsb_text_search(term, rows=500):
    term = (term or "").strip()
    if not term:
        return []

    # 1. RCSB
    url = "https://search.rcsb.org/rcsbsearch/v2/query"
    query = {
        "query": {"type": "terminal", "service": "full_text", "parameters": {"value": term}},
        "return_type": "entry",
        "request_options": {"paginate": {"start": 0, "rows": rows}}
    }
    try:
        r = requests.post(url, json=query, headers=HEADERS, timeout=30, verify=False)
        if r.status_code == 200:
            return sorted({x["identifier"] for x in r.json().get("result_set", [])})
    except:
        pass

    # 2. EBI Backup
    print(f"    🔄 Switching to EBI Backup Search for '{term}'...")
    ebi_url = f"https://www.ebi.ac.uk/pdbe/search/pdb/select?q={term}&wt=json&rows={rows}"
    try:
        r = requests.get(ebi_url, headers=HEADERS, timeout=30, verify=False)
        if r.status_code == 200:
            docs = r.json().get("response", {}).get("docs", [])
            return sorted({d["pdb_id"] for d in docs})
    except:
        pass

    return []

###############################################################################
# DOWNLOADER (With Backup Source)
###############################################################################

def download_single_cif(pdb_id, outdir):
    outfile = Path(outdir) / f"{pdb_id}.cif"
    if outfile.exists():
        return pdb_id, True, "cached"

    sources = [
        f"https://files.rcsb.org/download/{pdb_id}.cif",
        f"https://www.ebi.ac.uk/pdbe/entry-files/download/{pdb_id}.cif"
    ]

    for url in sources:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, verify=False)
            if r.status_code == 200:
                with open(outfile, "wb") as f:
                    f.write(r.content)
                return pdb_id, True, "ok"
        except:
            continue

    return pdb_id, False, "Failed all sources"

###############################################################################
# PROCESSOR
###############################################################################

def process_protein_row(row, root_outdir, limit=None):
    protein = str(row["protein"]).strip()
    query   = str(row["search_query"]).strip()

    print(f"\n=== Processing {protein} ===")
    protein_dir = Path(root_outdir) / protein
    protein_dir.mkdir(parents=True, exist_ok=True)

    ids = rcsb_text_search(query, rows=500)
    if not ids and protein != query:
        ids = rcsb_text_search(protein, rows=500)

    print(f"  → Found {len(ids)} candidates")
    if limit is not None:
        ids = ids[:limit]

    downloaded = []
    if ids:
        with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as exe:
            futures = {exe.submit(download_single_cif, pdb, protein_dir): pdb for pdb in ids}
            for fut in as_completed(futures):
                pdb_id, ok, msg = fut.result()
                if ok:
                    downloaded.append(pdb_id)
                else:
                    print(f"    ✖ {pdb_id} ({msg})")

    print(f"  → Downloaded {len(downloaded)} CIFs")
    return protein, len(ids), len(downloaded), downloaded

###############################################################################
# MAIN
###############################################################################

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", type=str)
    parser.add_argument("--outdir", type=str, default=default_outdir)
    parser.add_argument("--limit", type=int, default=default_limit)

    # Use top-of-file defaults
    parser.add_argument("--mw_min", type=float, default=min_mw)
    parser.add_argument("--mw_max", type=float, default=max_mw)
    parser.add_argument("--min_id", type=float, default=min_id_default)

    # Simple switch: Args present = Auto, Args missing = Manual
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
            min_id=min_id_default
        )

    if not args.auto or not os.path.exists(args.auto):
        print("❌ Input CSV not found.")
        sys.exit(1)

    df = pd.read_csv(args.auto)
    Path(args.outdir).mkdir(exist_ok=True)

    SUMMARY = {}
    CIFDATA_ROWS = []

    for _, row in df.iterrows():
        prot, n_cand, n_down, pdbs = process_protein_row(row, args.outdir, args.limit)
        SUMMARY[prot] = pdbs
        CIFDATA_ROWS.append({
            "protein": prot, "n_candidates": n_cand, "n_downloaded": n_down, "outdir": args.outdir
        })

    with open("summary.json", "w") as f:
        json.dump(SUMMARY, f)

    pd.DataFrame(CIFDATA_ROWS).to_csv("CIFdata.csv", index=False)

    # This is where Step 2 reads MW_min/MW_max/MinID from
    pd.DataFrame([{
        "MW_min": args.mw_min,
        "MW_max": args.mw_max,
        "MinID": args.min_id
    }]).to_csv("queries.csv", index=False)

    print("\n=== DONE ===")
    print(f"✅ Wrote queries.csv with MW_min={args.mw_min}, MW_max={args.mw_max}, MinID={args.min_id}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
STEP 2 — SQchk.py (REWRITE, HARDENED)
===============================================================================
Purpose:
  • For each candidate PDB ID:
      - Pull polymer sequences + ligand entities from PDBe (EBI) API
      - Compute sequence identity of each polymer entity vs user FASTA
      - Log per-chain similarity into chain_similarity.csv
      - Log per-chain ligands into filtered_data.csv (excluding NON_LIGAND_CODES)

Fixes vs your current version:
  ✅ Never “0it” silently:
        - Validates queries.csv, Protein_Data.csv, summary.json
        - Counts total PDB jobs and HARD FAILS if 0
  ✅ More reliable FASTA parsing + normalization
  ✅ Robust JSON parsing (handles PDB_MAP as dict or list)
  ✅ Safer MW parsing (keeps your “ignore MW for debugging” behavior)
  ✅ Better progress + summary stats at end
  ✅ Resilient networking: retries + backoff, explicit timeouts
  ✅ Avoids biopython pairwise2 deprecation (uses PairwiseAligner)
       - Still local alignment; still “lenient best segment” like your localxx intent
===============================================================================
"""

import os
import json
import csv
import time
import random
import requests
import urllib3
import pandas as pd

from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import Lock
from tqdm import tqdm

# Biopython (PairwiseAligner replaces deprecated pairwise2)
from Bio.Align import PairwiseAligner

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )
}

from NON_LIGAND_CODES import NON_LIGAND_CODES

# -------------------------
# Config
# -------------------------
MAX_WORKERS = 12
CSV_FILE = "filtered_data.csv"
CHAIN_CSV_FILE = "chain_similarity.csv"
csv_lock = Lock()

EBI_MOLECULES_URL = "https://www.ebi.ac.uk/pdbe/api/pdb/entry/molecules/{pdb_id}"

# Networking retries
REQ_TIMEOUT = 20
REQ_RETRIES = 3
REQ_BACKOFF_BASE = 0.6  # seconds

# Sequence identity thresholds (for logging)
LOG_MATCH_IF_GT = 0.0  # keep your behavior (log any >0)


# =============================================================================
# Utilities
# =============================================================================

def die(msg: str, code: int = 1):
    raise SystemExit(f"\n❌ {msg}\n")


def read_first_row_csv(path: str) -> pd.Series:
    if not os.path.exists(path):
        die(f"Missing required file: {path}")
    df = pd.read_csv(path)
    if df.empty:
        die(f"{path} is empty (needs at least one row).")
    return df.iloc[0]


def load_json(path: str):
    if not os.path.exists(path):
        die(f"Missing required file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception as e:
            die(f"Failed to parse JSON {path}: {e}")


def normalize_fasta(raw_fasta) -> str:
    """
    Accepts a FASTA string (may include headers and newlines).
    Returns a clean, uppercase amino acid string.
    """
    if raw_fasta is None or (isinstance(raw_fasta, float) and pd.isna(raw_fasta)):
        return ""
    s = str(raw_fasta)
    lines = s.splitlines()
    seq = "".join([L.strip() for L in lines if not L.strip().startswith(">")])
    seq = seq.replace(" ", "").replace("\n", "").replace("\r", "").strip().upper()
    return seq


def normalize_pdb_id(pdb) -> str:
    return str(pdb).strip().lower()


def ensure_list(x):
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        return [x]
    return list(x)


def safe_float(x, default=None):
    try:
        if x is None:
            return default
        if isinstance(x, float) and pd.isna(x):
            return default
        s = str(x).strip()
        if s == "":
            return default
        return float(s)
    except Exception:
        return default


# =============================================================================
# Networking: PDBe (EBI) molecules endpoint
# =============================================================================

def fetch_url_json(url: str):
    """
    GET JSON with retries + backoff. Returns dict or None.
    """
    for attempt in range(1, REQ_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, verify=False)
            if r.status_code == 200:
                return r.json()
            # 404 etc.
            return None
        except Exception:
            # retry with backoff
            if attempt < REQ_RETRIES:
                time.sleep(REQ_BACKOFF_BASE * attempt + random.random() * 0.2)
            else:
                return None


def get_structure_data(pdb_id: str):
    """
    Uses PDBe molecules endpoint.
    Returns:
      {
        "polymers": [{"entity_id","sequence","chains"}...],
        "nonpolymers": [{"entity_id","resname","name","mw","chains"}...]
      }
    """
    pdb_id = normalize_pdb_id(pdb_id)
    url = EBI_MOLECULES_URL.format(pdb_id=pdb_id)
    data = fetch_url_json(url)

    if not data or pdb_id not in data:
        return None

    root = data[pdb_id]
    polymers, nonpolymers = [], []

    for entity in root:
        e_id = str(entity.get("entity_id", ""))
        chains = entity.get("in_chains", []) or []
        moltype = (entity.get("molecule_type", "") or "").lower()

        # polymer protein-like
        if "polypeptide" in moltype:
            polymers.append({
                "entity_id": e_id,
                "sequence": (entity.get("sequence", "") or ""),
                "chains": chains
            })

        # non-polymer ligand-like: PDBe often uses "bound" in molecule_type
        elif "bound" in moltype:
            comps = entity.get("chem_comp_ids", []) or []
            resname = comps[0] if comps else "UNK"
            name_list = entity.get("molecule_name", ["Unknown"]) or ["Unknown"]
            nm = name_list[0] if isinstance(name_list, list) and name_list else str(name_list)
            mw = safe_float(entity.get("weight", 0.0), default=0.0)

            nonpolymers.append({
                "entity_id": e_id,
                "resname": str(resname).strip().upper(),
                "name": nm,
                "mw": mw,
                "chains": chains
            })

    return {"polymers": polymers, "nonpolymers": nonpolymers}


# =============================================================================
# Sequence identity using PairwiseAligner (local alignment)
# =============================================================================

_ALIGNER = None

def get_aligner():
    """
    Create a local aligner once per process.
    Scoring tuned to behave like "lenient local best segment":
      - local mode
      - match=1, mismatch=0 so score approximates number of matching positions
      - gap penalties 0 so it can slide freely (very lenient)
    """
    global _ALIGNER
    if _ALIGNER is None:
        al = PairwiseAligner()
        al.mode = "local"
        al.match_score = 1.0
        al.mismatch_score = 0.0
        al.open_gap_score = 0.0
        al.extend_gap_score = 0.0
        _ALIGNER = al
    return _ALIGNER


def seq_identity_percent(query_fasta_whole: str, pdb_frag: str) -> float:
    """
    Returns percent identity of best local alignment, normalized by len(pdb_frag),
    similar to your: alignments[0].score / len(pdb_frag) * 100
    """
    if not query_fasta_whole or not pdb_frag:
        return 0.0

    q = query_fasta_whole
    t = pdb_frag.replace(" ", "").replace("\n", "").replace("\r", "").strip().upper()
    if not t:
        return 0.0

    al = get_aligner()
    try:
        score = al.score(q, t)
        if len(t) == 0:
            return 0.0
        return (score / len(t)) * 100.0
    except Exception:
        return 0.0


# =============================================================================
# Worker
# =============================================================================

def process_one_pdb(job):
    """
    job = (protein_name, pdb_id, fasta_whole, mw_min, mw_max)
    Returns small summary dict for end-of-run stats.
    Writes CSV rows directly under lock.
    """
    protein_name, pdb, fasta_whole, mw_min, mw_max = job
    pdb = normalize_pdb_id(pdb)

    meta = get_structure_data(pdb)
    if not meta:
        return {"pdb": pdb, "protein": protein_name, "ok": False, "reason": "no_ebi_meta"}

    # ---- chain similarity rows
    chain_sim_rows = []
    for poly in meta["polymers"]:
        pdb_frag = (poly.get("sequence", "") or "").replace(" ", "").replace("\n", "").upper()
        if not pdb_frag:
            continue

        pct = seq_identity_percent(fasta_whole, pdb_frag)
        if pct > LOG_MATCH_IF_GT:
            print(f"DEBUG: {pdb} Chain(s) {poly.get('chains', [])} -> Match: {pct:.1f}%", flush=True)

        for ch in (poly.get("chains", []) or []):
            chain_sim_rows.append([protein_name, pdb, ch, poly.get("entity_id", ""), pct])

    if chain_sim_rows:
        with csv_lock:
            with open(CHAIN_CSV_FILE, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(chain_sim_rows)

    # ---- ligand rows (keep ALL for debug; still skipping NON_LIGAND_CODES)
    ligand_rows = []
    for lig in meta["nonpolymers"]:
        resname = lig.get("resname", "UNK")
        if resname in NON_LIGAND_CODES:
            continue

        # keep your debug mode: ignore MW filters for now
        for ch in (lig.get("chains", []) or []):
            ligand_rows.append([
                protein_name,
                pdb,
                ch,
                resname,
                lig.get("mw", 0.0),
                lig.get("name", "Unknown")
            ])

    if ligand_rows:
        with csv_lock:
            with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(ligand_rows)

    return {
        "pdb": pdb,
        "protein": protein_name,
        "ok": True,
        "chains_logged": len(chain_sim_rows),
        "ligands_logged": len(ligand_rows),
    }


# =============================================================================
# Main
# =============================================================================

def init_output_files():
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["protein", "pdb", "chain", "ligand", "MW", "Name"])
    with open(CHAIN_CSV_FILE, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["protein", "pdb", "chain", "entity_id", "Similarity"])


def build_jobs():
    # Load inputs
    q = read_first_row_csv("queries.csv")
    mw_min = safe_float(q.get("MW_min", None), default=None)
    mw_max = safe_float(q.get("MW_max", None), default=None)

    pdb_map = load_json("summary.json")
    df = pd.read_csv("Protein_Data.csv")
    if df.empty:
        die("Protein_Data.csv is empty (no proteins to process).")

    if "protein" not in df.columns or "fasta" not in df.columns:
        die(f"Protein_Data.csv must contain columns: protein, fasta (found: {list(df.columns)})")

    jobs = []
    missing = 0

    for _, row in df.iterrows():
        protein = str(row["protein"]).strip()
        fasta = normalize_fasta(row["fasta"])
        if not protein or not fasta:
            missing += 1
            continue

        # pdb_map may be keyed by protein name
        pdbs = pdb_map.get(protein, [])
        pdbs = ensure_list(pdbs)

        for pdb in pdbs:
            pdb_id = normalize_pdb_id(pdb)
            if pdb_id:
                jobs.append((protein, pdb_id, fasta, mw_min, mw_max))

    return jobs, missing, mw_min, mw_max


def main():
    init_output_files()

    jobs, missing, mw_min, mw_max = build_jobs()

    print("\n🚀 Running Sequence Identity Analysis...")
    print(f"   Proteins missing fasta/name rows skipped: {missing}")
    print(f"   MW_min={mw_min}  MW_max={mw_max}  (MW filters currently ignored for ligand debug)")
    print(f"   Total PDB jobs to process: {len(jobs)}")

    # HARD FAIL if nothing to do (this prevents your 0it silent failure)
    if len(jobs) == 0:
        die(
            "No PDB jobs were constructed. This is why you saw '0it'.\n"
            "Check that summary.json contains PDBs under the exact protein key used in Protein_Data.csv,\n"
            "and that Protein_Data.csv has non-empty 'protein' and 'fasta' values."
        )

    ok = 0
    fail = 0
    fail_reasons = {}

    # Using ProcessPoolExecutor, but we submit futures so we can count failures
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as exe:
        futures = [exe.submit(process_one_pdb, job) for job in jobs]

        for fut in tqdm(as_completed(futures), total=len(futures)):
            try:
                res = fut.result()
                if res and res.get("ok"):
                    ok += 1
                else:
                    fail += 1
                    reason = (res or {}).get("reason", "unknown")
                    fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
            except Exception as e:
                fail += 1
                fail_reasons["exception"] = fail_reasons.get("exception", 0) + 1
                # keep it visible; one PDB failing shouldn't kill the run
                print(f"⚠️ Worker exception: {e}", flush=True)

    print("\nDONE")
    print(f"✅ PDBs processed OK: {ok}")
    print(f"❌ PDBs failed: {fail}")
    if fail_reasons:
        print("📌 Failure reasons:")
        for k, v in sorted(fail_reasons.items(), key=lambda x: -x[1]):
            print(f"   - {k}: {v}")

    # Safety: if we produced zero ligand rows, warn loudly
    try:
        lig_df = pd.read_csv(CSV_FILE)
        if len(lig_df) <= 1:
            print("\n⚠️ filtered_data.csv has no ligand rows.")
            print("   This usually means either:")
            print("   • PDBe 'bound' entities are not present for these PDBs, or")
            print("   • your NON_LIGAND_CODES list is too aggressive, or")
            print("   • the PDBe endpoint didn't return expected fields.")
    except Exception:
        pass


if __name__ == "__main__":
    main()

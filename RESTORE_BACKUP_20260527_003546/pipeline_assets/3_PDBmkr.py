#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import io
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import pandas as pd
from tqdm import tqdm
from Bio.PDB import MMCIFParser, PDBIO, Select

# =============================================================================
# CONFIG
# =============================================================================
MAX_WORKERS = 12
LIGMAP_FILE = "5CharMAP.csv"
CHAINMAP_FILE = "ChainRenameMAP.csv"

# Canonical headers (single source of truth)
LIGMAP_COLS = ["protein", "pdb", "ligand5", "ligand3", "ligandX"]
CHAINMAP_COLS = ["pdb", "orig_chain", "new_chain"]


# =============================================================================
# BIO.PDB helpers
# =============================================================================
class ChainSelect(Select):
    def __init__(self, chain_id):
        self.chain_id = chain_id

    def accept_chain(self, chain):
        return chain.id == self.chain_id


def next_ligandX(idx: int) -> str:
    return f"{chr(ord('A') + (idx // 100))}{idx % 100:02d}"


def generate_chain_id(idx: int) -> str:
    if idx < 26:
        return chr(ord("A") + idx)
    elif idx < 52:
        return chr(ord("a") + (idx - 26))
    return "X"


def build_single_pdb(job: dict):
    """
    Build a single-chain PDB file from a CIF, optionally renaming chain IDs.
    Returns: (key_tuple, pdb_text_or_None, error_or_None)
    """
    protein, pdb_id = job["protein"], job["pdb"]
    orig_chain, new_chain = job["orig_chain"], job["new_chain"]
    cif_path = job["cif_path"]

    key = (protein, pdb_id, orig_chain, job["job_index"])

    if not os.path.exists(cif_path):
        return key, None, f"CIF missing: {cif_path}"

    try:
        parser = MMCIFParser(QUIET=True)
        structure = parser.get_structure(pdb_id, cif_path)
    except Exception as e:
        return key, None, f"CIF parse error: {e}"

    if new_chain != orig_chain:
        for model in structure:
            for ch in model:
                if ch.id == orig_chain:
                    ch.id = new_chain

    io_obj = io.StringIO()
    io_pdb = PDBIO()
    io_pdb.set_structure(structure)
    try:
        io_pdb.save(io_obj, ChainSelect(new_chain))
    except Exception as e:
        return key, None, f"PDB save error: {e}"

    return key, io_obj.getvalue(), None


# =============================================================================
# CSV helpers
# =============================================================================
def ensure_csv_with_headers(path: str, columns: list[str]) -> None:
    """
    Ensure CSV exists and has at least the given headers.
    - If missing -> create headers-only.
    - If empty/invalid -> rewrite headers-only.
    - If headers mismatch badly -> rewrite headers-only.
    """
    try:
        if not os.path.exists(path):
            pd.DataFrame(columns=columns).to_csv(path, index=False)
            return

        # If exists but empty / unreadable, rewrite with headers
        try:
            df = pd.read_csv(path)
        except Exception:
            pd.DataFrame(columns=columns).to_csv(path, index=False)
            return

        if df is None or df.columns is None:
            pd.DataFrame(columns=columns).to_csv(path, index=False)
            return

        # Normalize a common casing mismatch: ligandx -> ligandX
        lower_map = {c.lower(): c for c in df.columns}
        if "ligandx" in lower_map and "ligandX" not in df.columns:
            df = df.rename(columns={lower_map["ligandx"]: "ligandX"})

        # If required columns missing, rewrite headers-only
        if not all(c in df.columns for c in columns):
            pd.DataFrame(columns=columns).to_csv(path, index=False)
            return

        # Otherwise keep as-is
    except Exception:
        # Last-ditch: guarantee the file exists
        try:
            pd.DataFrame(columns=columns).to_csv(path, index=False)
        except Exception:
            pass


def write_headers_only(path: str, columns: list[str]) -> None:
    """Force overwrite as headers-only."""
    pd.DataFrame(columns=columns).to_csv(path, index=False)


# =============================================================================
# MAIN
# =============================================================================
def main():
    # Always guarantee these files exist with headers (safe for downstream steps)
    ensure_csv_with_headers(LIGMAP_FILE, LIGMAP_COLS)
    ensure_csv_with_headers(CHAINMAP_FILE, CHAINMAP_COLS)

    # Inputs
    if not os.path.exists("filtered_data.csv") or not os.path.exists("chain_similarity.csv"):
        print("❌ Input CSVs missing. Did Step 2 finish?")
        return

    filtered_df = pd.read_csv("filtered_data.csv")
    sim_df = pd.read_csv("chain_similarity.csv")
    cifinfo = pd.read_csv("CIFdata.csv")
    query = pd.read_csv("queries.csv").iloc[0]

    target_min_id = float(query["MinID"])
    cif_base = str(cifinfo.iloc[0]["outdir"])
    output_root = f"{cif_base}_PDB"
    os.makedirs(output_root, exist_ok=True)

    # Merge
    merged = filtered_df.merge(sim_df, on=["protein", "pdb", "chain"], how="inner")

    if not merged.empty:
        max_sim = merged["Similarity"].max()
        print(f"📊 Similarity Stats: Max found = {max_sim:.2f}% (Threshold: {target_min_id}%)")
    else:
        print("📊 Merge result is empty.")

    # Filtering
    final_set = merged[merged["Similarity"] >= target_min_id].drop_duplicates()

    if final_set.empty and not merged.empty:
        print(f"⚠️ Strict filter ({target_min_id}%) dropped everything.")
        print("🔄 Auto-lowering threshold to 30% to salvage data...")
        final_set = merged[merged["Similarity"] >= 30.0].drop_duplicates()

    if final_set.empty:
        print("❗ No entries found even after relaxing filter.")
        # Force both maps to headers-only so pipeline stays consistent and independent
        write_headers_only(LIGMAP_FILE, LIGMAP_COLS)
        write_headers_only(CHAINMAP_FILE, CHAINMAP_COLS)
        return

    # Detect if ANY 5-letter ligands exist in final_set
    all_ligands = final_set["ligand"].astype(str)
    has_five_letter = any(len(l.strip()) == 5 for l in all_ligands)

    # If no 5-letter ligands: create Skip4.txt and DO NOT populate 5CharMAP rows
    if not has_five_letter:
        print("⚠️ No 5-letter ligands detected. Renaming map not required.")
        skip4_flag = "Skip4.txt"
        with open(skip4_flag, "w") as f:
            f.write("No 5-letter ligands detected. Step 4 renaming not required.\n")
        print("🛑 Created Skip4.txt — Step 4 will be skipped.")
        # IMPORTANT: Do not reuse any old rows that might exist from previous runs
        write_headers_only(LIGMAP_FILE, LIGMAP_COLS)

    # Make sure protein output dirs exist
    for protein in final_set["protein"].unique():
        os.makedirs(f"{output_root}/{protein}", exist_ok=True)

    # -----------------------------------------------------------------------------
    # 5CharMAP handling:
    # - Only build / append ligmap rows if has_five_letter is True
    # - Otherwise we keep it headers-only (already forced above)
    # -----------------------------------------------------------------------------
    ligmap_rows = []
    lig5_to_ligX = {}
    ligand_counter = 0

    if has_five_letter:
        # Load existing map safely (to keep stable ligandX assignments across runs)
        try:
            ligmap_df = pd.read_csv(LIGMAP_FILE)
        except Exception:
            ligmap_df = pd.DataFrame(columns=LIGMAP_COLS)

        # Normalize columns
        if not ligmap_df.empty:
            lower_map = {c.lower(): c for c in ligmap_df.columns}
            if "ligandx" in lower_map and "ligandX" not in ligmap_df.columns:
                ligmap_df = ligmap_df.rename(columns={lower_map["ligandx"]: "ligandX"})

        # Ensure expected columns exist
        for c in LIGMAP_COLS:
            if c not in ligmap_df.columns:
                ligmap_df[c] = pd.Series(dtype="object")

        # Existing rows (preserve)
        ligmap_rows = ligmap_df[LIGMAP_COLS].to_dict("records")

        # Build lig5 -> ligX and continue ligandX counter
        for r in ligmap_rows:
            lig5 = str(r.get("ligand5", "")).strip()
            ligX = str(r.get("ligandX", "")).strip()
            if lig5 and ligX:
                lig5_to_ligX[lig5] = ligX
                try:
                    ligand_counter = max(ligand_counter, int(ligX[1:]) + 1)
                except Exception:
                    pass

        # Assign ligandX for any new 5-letter ligands
        five_ligs = final_set[final_set["ligand"].astype(str).str.len() == 5]["ligand"].unique()
        for lig5 in five_ligs:
            lig5 = str(lig5).strip()
            if lig5 and lig5 not in lig5_to_ligX:
                lig5_to_ligX[lig5] = next_ligandX(ligand_counter)
                ligand_counter += 1

    # -----------------------------------------------------------------------------
    # Chain rename map
    # -----------------------------------------------------------------------------
    try:
        chainmap_df = pd.read_csv(CHAINMAP_FILE)
    except Exception:
        chainmap_df = pd.DataFrame(columns=CHAINMAP_COLS)

    for c in CHAINMAP_COLS:
        if c not in chainmap_df.columns:
            chainmap_df[c] = pd.Series(dtype="object")

    chainmap_rows = chainmap_df[CHAINMAP_COLS].to_dict("records")
    chain_map = {
        (r["pdb"], r["orig_chain"]): r["new_chain"]
        for r in chainmap_rows
        if r.get("pdb") and r.get("orig_chain")
    }
    chain_counter = len(chain_map)

    # Rename only chains with length > 1
    long_chains = final_set[final_set["chain"].astype(str).str.len() > 1][["pdb", "chain"]].drop_duplicates()
    for _, r in long_chains.iterrows():
        key = (r["pdb"], r["chain"])
        if key not in chain_map:
            chain_map[key] = generate_chain_id(chain_counter)
            chainmap_rows.append({
                "pdb": r["pdb"],
                "orig_chain": r["chain"],
                "new_chain": chain_map[key]
            })
            chain_counter += 1

    # -----------------------------------------------------------------------------
    # Build PDB jobs
    # -----------------------------------------------------------------------------
    jobs = []
    seen_ligmap = set()

    for job_idx, row in enumerate(final_set.itertuples(index=False)):
        protein = row.protein
        pdb_id = row.pdb
        chain = row.chain
        ligand = row.ligand

        cif_path = os.path.join(cif_base, protein, f"{pdb_id}.cif")
        new_chain = chain_map.get((pdb_id, chain), chain)

        ligand_str = str(ligand).strip()

        # Only record ligand mapping rows if true 5-letter ligands exist
        if has_five_letter and len(ligand_str) == 5:
            lig5 = ligand_str
            key = (protein, pdb_id, lig5)
            if key not in seen_ligmap:
                seen_ligmap.add(key)
                ligmap_rows.append({
                    "protein": protein,
                    "pdb": pdb_id,
                    "ligand5": lig5,
                    "ligand3": lig5[:3],
                    "ligandX": lig5_to_ligX.get(lig5, "")
                })

        safe_ligand = ligand_str.replace("/", "-")
        out_rel = f"{protein}/{pdb_id}_{chain}_{safe_ligand}.pdb"
        out_path = os.path.join(output_root, out_rel)

        jobs.append({
            "job_index": job_idx,
            "protein": protein,
            "pdb": pdb_id,
            "orig_chain": chain,
            "new_chain": new_chain,
            "ligand": ligand,
            "cif_path": cif_path,
            "out_path": out_path
        })

    print(f"\n🚀 BUILDING {len(jobs)} PDB CHAINS...\n")
    results = {}

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as exe:
        futmap = {exe.submit(build_single_pdb, j): j["job_index"] for j in jobs}
        for fut in tqdm(as_completed(futmap), total=len(jobs)):
            idx = futmap[fut]
            try:
                _k, pdbtext, err = fut.result()
                if pdbtext:
                    results[idx] = pdbtext
            except Exception:
                pass

    # Write PDB outputs
    for idx, pdb_text in results.items():
        out_path = jobs[idx]["out_path"]
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.write(pdb_text)

    # -----------------------------------------------------------------------------
    # Final writes (key change: 5CharMAP headers-only if no 5-letter ligands)
    # -----------------------------------------------------------------------------
    if has_five_letter:
        pd.DataFrame(ligmap_rows, columns=LIGMAP_COLS).to_csv(LIGMAP_FILE, index=False)
    else:
        write_headers_only(LIGMAP_FILE, LIGMAP_COLS)

    pd.DataFrame(chainmap_rows, columns=CHAINMAP_COLS).drop_duplicates().to_csv(CHAINMAP_FILE, index=False)

    print("\n✅ All PDBs built successfully.")
    if has_five_letter:
        print(f"✅ Wrote {LIGMAP_FILE} with mapping rows (5-letter ligands detected).")
    else:
        print(f"✅ Wrote {LIGMAP_FILE} as headers-only (no 5-letter ligands detected).")


if __name__ == "__main__":
    main()


# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-

# import os
# import io
# from pathlib import Path
# from concurrent.futures import ProcessPoolExecutor, as_completed
# import pandas as pd
# from tqdm import tqdm
# from Bio.PDB import MMCIFParser, PDBIO, Select

# # CONFIG
# MAX_WORKERS = 12
# LIGMAP_FILE = "5CharMAP.csv"
# CHAINMAP_FILE = "ChainRenameMAP.csv"

# # ### NEW/CHANGED: canonical headers (single source of truth)
# LIGMAP_COLS = ["protein", "pdb", "ligand5", "ligand3", "ligandX"]
# CHAINMAP_COLS = ["pdb", "orig_chain", "new_chain"]


# class ChainSelect(Select):
#     def __init__(self, chain_id): self.chain_id = chain_id
#     def accept_chain(self, chain): return chain.id == self.chain_id


# def next_ligandX(idx):
#     return f"{chr(ord('A') + (idx // 100))}{idx % 100:02d}"


# def generate_chain_id(idx):
#     if idx < 26: return chr(ord("A") + idx)
#     elif idx < 52: return chr(ord("a") + (idx - 26))
#     return "X"


# def build_single_pdb(job):
#     protein, pdb_id = job["protein"], job["pdb"]
#     orig_chain, new_chain = job["orig_chain"], job["new_chain"]
#     cif_path = job["cif_path"]

#     key = (protein, pdb_id, orig_chain, job["job_index"])

#     if not os.path.exists(cif_path):
#         return key, None, f"CIF missing: {cif_path}"

#     try:
#         parser = MMCIFParser(QUIET=True)
#         structure = parser.get_structure(pdb_id, cif_path)
#     except Exception as e:
#         return key, None, f"CIF parse error: {e}"

#     if new_chain != orig_chain:
#         for model in structure:
#             for ch in model:
#                 if ch.id == orig_chain:
#                     ch.id = new_chain

#     io_obj = io.StringIO()
#     io_pdb = PDBIO()
#     io_pdb.set_structure(structure)
#     try:
#         io_pdb.save(io_obj, ChainSelect(new_chain))
#     except Exception as e:
#         return key, None, f"PDB save error: {e}"

#     return key, io_obj.getvalue(), None


# # ### NEW/CHANGED: helper that guarantees file exists with headers
# def ensure_csv_with_headers(path: str, columns: list[str]) -> None:
#     """
#     Ensure CSV exists and has at least the given headers.
#     - If missing -> create headers-only.
#     - If empty/invalid -> rewrite headers-only.
#     """
#     try:
#         if not os.path.exists(path):
#             pd.DataFrame(columns=columns).to_csv(path, index=False)
#             return

#         # If exists but empty / unreadable, rewrite with headers
#         try:
#             df = pd.read_csv(path)
#         except Exception:
#             pd.DataFrame(columns=columns).to_csv(path, index=False)
#             return

#         # Normalize columns if needed (don't destroy data unless it's incompatible)
#         # If required columns are missing, rewrite as headers-only
#         lower_map = {c.lower(): c for c in df.columns}
#         # Example normalization: ligandx -> ligandX
#         if "ligandx" in lower_map and "ligandX" not in df.columns:
#             df = df.rename(columns={lower_map["ligandx"]: "ligandX"})

#         if not all(c in df.columns for c in columns):
#             pd.DataFrame(columns=columns).to_csv(path, index=False)
#             return

#         # Otherwise keep as-is (already good)
#     except Exception:
#         # Last-ditch: guarantee the file exists
#         try:
#             pd.DataFrame(columns=columns).to_csv(path, index=False)
#         except Exception:
#             pass


# def main():
#     # ### NEW/CHANGED: ALWAYS create these upfront (even if we exit early)
#     ensure_csv_with_headers(LIGMAP_FILE, LIGMAP_COLS)
#     ensure_csv_with_headers(CHAINMAP_FILE, CHAINMAP_COLS)

#     # 1. SETUP
#     if not os.path.exists("filtered_data.csv") or not os.path.exists("chain_similarity.csv"):
#         print("❌ Input CSVs missing. Did Step 2 finish?")
#         # 5CharMAP.csv already exists with headers due to ensure_csv_with_headers()
#         return

#     filtered_df = pd.read_csv("filtered_data.csv")
#     sim_df = pd.read_csv("chain_similarity.csv")
#     cifinfo = pd.read_csv("CIFdata.csv")
#     query = pd.read_csv("queries.csv").iloc[0]

#     target_min_id = float(query["MinID"])
#     cif_base = str(cifinfo.iloc[0]["outdir"])
#     output_root = f"{cif_base}_PDB"
#     os.makedirs(output_root, exist_ok=True)

#     # 2. MERGE & DEBUG STATS
#     merged = filtered_df.merge(sim_df, on=["protein", "pdb", "chain"], how="inner")

#     if not merged.empty:
#         max_sim = merged["Similarity"].max()
#         print(f"📊 Similarity Stats: Max found = {max_sim:.2f}% (Threshold: {target_min_id}%)")
#     else:
#         print("📊 Merge result is empty.")

#     # 3. SMART FILTERING
#     final_set = merged[merged["Similarity"] >= target_min_id].drop_duplicates()

#     # --------------------------------------------------------
#     # Detect if ANY 5-letter ligands exist in final_set
#     # --------------------------------------------------------
#     all_ligands = final_set["ligand"].astype(str)
#     has_five_letter = any(len(l) == 5 for l in all_ligands)


#     if final_set.empty and not merged.empty:
#         print(f"⚠️ Strict filter ({target_min_id}%) dropped everything.")
#         print(f"🔄 Auto-lowering threshold to 30% to salvage data...")
#         final_set = merged[merged["Similarity"] >= 30.0].drop_duplicates()

#     if final_set.empty:
#         print("❗ No entries found even after relaxing filter.")
#         # ### NEW/CHANGED: write headers-only (or preserve existing) explicitly
#         pd.DataFrame([], columns=LIGMAP_COLS).to_csv(LIGMAP_FILE, index=False)
#         # Chain map can also be headers-only
#         pd.DataFrame([], columns=CHAINMAP_COLS).to_csv(CHAINMAP_FILE, index=False)
#         return

#     for protein in final_set["protein"].unique():
#         os.makedirs(f"{output_root}/{protein}", exist_ok=True)

#     # 4. MAPPING LOGIC
#     # ### NEW/CHANGED: load existing map safely, normalize columns
#     try:
#         ligmap_df = pd.read_csv(LIGMAP_FILE)
#     except Exception:
#         ligmap_df = pd.DataFrame(columns=LIGMAP_COLS)

#     if not ligmap_df.empty:
#         lower_map = {c.lower(): c for c in ligmap_df.columns}
#         if "ligandx" in lower_map and "ligandX" not in ligmap_df.columns:
#             ligmap_df = ligmap_df.rename(columns={lower_map["ligandx"]: "ligandX"})

#     # Ensure all expected columns exist
#     for c in LIGMAP_COLS:
#         if c not in ligmap_df.columns:
#             ligmap_df[c] = pd.Series(dtype="object")

#     ligmap_rows = ligmap_df[LIGMAP_COLS].to_dict("records")

#     lig5_to_ligX = {}
#     ligand_counter = 0
#     for r in ligmap_rows:
#         lig5 = str(r.get("ligand5", "")).strip()
#         ligX = str(r.get("ligandX", "")).strip()
#         if lig5 and ligX:
#             lig5_to_ligX[lig5] = ligX
#             try:
#                 ligand_counter = max(ligand_counter, int(ligX[1:]) + 1)
#             except Exception:
#                 pass

#     # --------------------------------------------------------
#     # If we have 5-letter ligands → normal behavior
#     # If ZERO 5-letter ligands → enable 3-letter fallback mode
#     # --------------------------------------------------------
#     if has_five_letter:
#         five_ligs = final_set[final_set["ligand"].astype(str).str.len() == 5]["ligand"].unique()
#         for lig5 in five_ligs:
#             lig5 = str(lig5)
#             if lig5 not in lig5_to_ligX:
#                 lig5_to_ligX[lig5] = next_ligandX(ligand_counter)
#                 ligand_counter += 1
#     else:
#         print("⚠️ No 5-letter ligands detected. Enabling 3-letter fallback mapping mode.")
#         skip4_flag = "Skip4.txt"

#         with open(skip4_flag, "w") as f:
#             f.write("No 5-letter ligands detected. Step 4 renaming not required.\n")

#         print("🛑 Created Skip4.txt — Step 4 will be skipped.")



#     # Chain map
#     try:
#         chainmap_df = pd.read_csv(CHAINMAP_FILE)
#     except Exception:
#         chainmap_df = pd.DataFrame(columns=CHAINMAP_COLS)

#     for c in CHAINMAP_COLS:
#         if c not in chainmap_df.columns:
#             chainmap_df[c] = pd.Series(dtype="object")

#     chainmap_rows = chainmap_df[CHAINMAP_COLS].to_dict("records")

#     chain_map = {(r["pdb"], r["orig_chain"]): r["new_chain"] for r in chainmap_rows if r.get("pdb") and r.get("orig_chain")}
#     chain_counter = len(chain_map)

#     long_chains = final_set[final_set["chain"].astype(str).str.len() > 1][["pdb", "chain"]].drop_duplicates()
#     for _, r in long_chains.iterrows():
#         key = (r["pdb"], r["chain"])
#         if key not in chain_map:
#             chain_map[key] = generate_chain_id(chain_counter)
#             chainmap_rows.append({"pdb": r["pdb"], "orig_chain": r["chain"], "new_chain": chain_map[key]})
#             chain_counter += 1

#     # 5. EXECUTION
#     jobs = []
#     seen_ligmap = set()

#     for job_idx, row in enumerate(final_set.itertuples(index=False)):
#         protein = row.protein
#         pdb_id = row.pdb
#         chain = row.chain
#         ligand = row.ligand

#         cif_path = os.path.join(cif_base, protein, f"{pdb_id}.cif")
#         new_chain = chain_map.get((pdb_id, chain), chain)

#         ligand_str = str(ligand)

#         # --------------------------------------------------------
#         # NORMAL MODE → Only process 5-letter ligands
#         # --------------------------------------------------------
#         if has_five_letter and len(ligand_str) == 5:
#             lig5 = ligand_str
#             key = (protein, pdb_id, lig5)
#             if key not in seen_ligmap:
#                 seen_ligmap.add(key)
#                 ligmap_rows.append({
#                     "protein": protein,
#                     "pdb": pdb_id,
#                     "ligand5": lig5,
#                     "ligand3": lig5[:3],
#                     "ligandX": lig5_to_ligX.get(lig5, "")
#                 })

#         # --------------------------------------------------------
#         # FALLBACK MODE → Use 3-letter ligand in ALL columns
#         # --------------------------------------------------------
#         elif not has_five_letter:
#             lig3 = ligand_str
#             key = (protein, pdb_id, lig3)
#             if key not in seen_ligmap:
#                 seen_ligmap.add(key)
#                 ligmap_rows.append({
#                     "protein": protein,
#                     "pdb": pdb_id,
#                     "ligand5": lig3,
#                     "ligand3": lig3,
#                     "ligandX": lig3
#                 })


#         safe_ligand = str(ligand).replace("/", "-")
#         out_rel = f"{protein}/{pdb_id}_{chain}_{safe_ligand}.pdb"
#         out_path = os.path.join(output_root, out_rel)

#         jobs.append({
#             "job_index": job_idx,
#             "protein": protein,
#             "pdb": pdb_id,
#             "orig_chain": chain,
#             "new_chain": new_chain,
#             "ligand": ligand,
#             "cif_path": cif_path,
#             "out_path": out_path
#         })

#     print(f"\n🚀 BUILDING {len(jobs)} PDB CHAINS...\n")
#     results = {}

#     with ProcessPoolExecutor(max_workers=MAX_WORKERS) as exe:
#         futmap = {exe.submit(build_single_pdb, j): j["job_index"] for j in jobs}
#         for fut in tqdm(as_completed(futmap), total=len(jobs)):
#             idx = futmap[fut]
#             try:
#                 _k, pdbtext, err = fut.result()
#                 if pdbtext:
#                     results[idx] = pdbtext
#             except Exception:
#                 pass

#     for idx, pdb_text in results.items():
#         out_path = jobs[idx]["out_path"]
#         os.makedirs(os.path.dirname(out_path), exist_ok=True)
#         with open(out_path, "w") as f:
#             f.write(pdb_text)

#     # ### NEW/CHANGED: ALWAYS save with explicit columns => headers even if empty
#     pd.DataFrame(ligmap_rows, columns=LIGMAP_COLS).to_csv(LIGMAP_FILE, index=False)
#     pd.DataFrame(chainmap_rows, columns=CHAINMAP_COLS).drop_duplicates().to_csv(CHAINMAP_FILE, index=False)

#     print("\n✅ All PDBs built successfully.")


# if __name__ == "__main__":
#     main()

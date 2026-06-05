#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, shutil, pandas as pd
from pathlib import Path

RESULT_DIR = Path("TARGET_RESULTS")
RESULT_DIR.mkdir(exist_ok=True)

def collect_results(src, dst_folder):
    src, dst = Path(src), Path(dst_folder) / Path(src).name
    if not src.exists(): return
    if src.is_dir():
        if dst.exists(): shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else: shutil.copy2(src, dst)

def main():
    # 1. Collect everything
    for item in ["MCS_Output", "Target_Table", "WAR_PDB"]: collect_results(item, RESULT_DIR)
    for csv_f in Path(".").glob("*.csv"):
        if "input" not in csv_f.name and "Protein_Data" not in csv_f.name:
            collect_results(csv_f, RESULT_DIR)

    # 2. Load the three pillars of data
    mcs_f = RESULT_DIR / "MCS_Output" / "Ligand_MCS_Map.csv"
    sasa_f = RESULT_DIR / "Warhead_SASA_atoms.csv"
    summary_f = RESULT_DIR / "Resolved_SASA_Summary.csv"

    if not all(f.exists() for f in [mcs_f, sasa_f, summary_f]):
        print("❌ Critical files missing for merge.")
        raise RuntimeError(
            f"Step 12 missing critical files: "
            f"MCS={mcs_f.exists()} SASA={sasa_f.exists()} SUMMARY={summary_f.exists()}"
        )

    mcs = pd.read_csv(mcs_f)
    sasa = pd.read_csv(sasa_f)
    summ = pd.read_csv(summary_f)

    # Standardize column names across all files for the merge
    # Force "Ligand" to be "Warhead" everywhere
    for df in [mcs, summ]:
        if 'Ligand' in df.columns: df.rename(columns={'Ligand': 'Warhead'}, inplace=True)
    
    # Standardize keys to strings
    keys = ["Target", "pdb_id", "Residue_ID", "Chain", "atom_id"]
    for df in [mcs, sasa]:
        for c in keys:
            if c in df.columns: df[c] = df[c].astype(str)

    # Merge Step 1: Geometry (SASA) + Mapping (MCS)
    # We keep AtomIndex and AtomSymbol
    merged = pd.merge(sasa, mcs[keys + ["AtomIndex", "AtomSymbol"]], on=keys, how="left")

    # Merge Step 2: Add SMILES from Summary
    smi_lookup = summ[['pdb_id', 'Warhead', 'SMILES']].drop_duplicates()
    final = pd.merge(merged, smi_lookup, on=['pdb_id', 'Warhead'], how='left')

    # Force specific column names to satisfy SVGmkr.py
    final.to_csv(RESULT_DIR / "3DSASAmapped.csv", index=False)
    print("✅ Integrated 3DSASAmapped.csv created with standard headers.")

if __name__ == "__main__":
    main()

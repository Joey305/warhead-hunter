#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import pandas as pd

# -----------------------------------------------------------
# Utils
# -----------------------------------------------------------

def get_ligand_from_filename(fname: str) -> str:
    """
    Extract ligand from file name: PDB_CHAIN_LIG.pdb
    Example: 6XL4_B_NQ1.pdb → NQ1
    """
    core = fname.replace(".pdb", "")
    parts = core.split("_")
    if len(parts) < 3:
        raise ValueError(f"Unexpected filename format: {fname}")
    return parts[-1]


def get_resname(line: str) -> str:
    """Extract 3-letter residue name from PDB ATOM/HETATM line."""
    return line[17:20].strip()


# -----------------------------------------------------------
# Cleaner for one file
# -----------------------------------------------------------

def clean_pdb(fullpath: str):
    fname = os.path.basename(fullpath)
    ligand = get_ligand_from_filename(fname)

    print(f"🧪 Cleaning {fname}  | Keeping ligand: {ligand}")

    with open(fullpath, "r") as f:
        lines = f.readlines()

    out = []
    for line in lines:

        if line.startswith("ATOM"):
            out.append(line)
            continue

        if line.startswith("TER") or line.startswith("END"):
            out.append(line)
            continue

        if line.startswith("HETATM"):
            res = get_resname(line)
            # keep only matching ligand; remove all others (EDO, HOH, CIT, etc.)
            if res == ligand:
                out.append(line)
            continue

        # Other lines (REMARK, HEADER)
        out.append(line)

    with open(fullpath, "w") as f:
        f.writelines(out)

    print(f"✔ Cleaned: {fname}\n")


# -----------------------------------------------------------
# MAIN
# -----------------------------------------------------------

def main():

    # Load CIFdata.csv
    df = pd.read_csv("CIFdata.csv")

    # Use first row's outdir (they are all WAR anyway)
    base_outdir = str(df.iloc[0]["outdir"]).rstrip("/")
    pdb_root = base_outdir + "_PDB"

    if not os.path.isdir(pdb_root):
        raise FileNotFoundError(f"❌ PDB directory not found: {pdb_root}")

    print(f"\n📁 ROOT DIRECTORY: {pdb_root}\n")

    # Iterate through each protein listed in CIFdata.csv
    for protein in df["protein"].unique():

        protein_dir = os.path.join(pdb_root, protein)

        if not os.path.isdir(protein_dir):
            print(f"⚠️ Skipping missing protein folder: {protein_dir}")
            continue

        print(f"\n=============================")
        print(f"🔬 Processing Protein: {protein}")
        print(f"📂 Folder: {protein_dir}")
        print(f"=============================\n")

        # Iterate through PDB files inside this protein directory
        for fname in os.listdir(protein_dir):
            if fname.lower().endswith(".pdb"):
                fullpath = os.path.join(protein_dir, fname)
                clean_pdb(fullpath)

    print("\n🎉 DONE — All PDB files cleaned successfully!\n")


if __name__ == "__main__":
    main()

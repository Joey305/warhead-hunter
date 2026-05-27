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
    return parts[-1].upper()


def get_resname(line: str) -> str:
    """Extract residue name from PDB ATOM/HETATM line."""
    return line[17:20].strip().upper()


# -----------------------------------------------------------
# Ion filters
# -----------------------------------------------------------

# Common PDB residue names for ions / inorganics / counterions you probably don't want as "ligands"
ION_RESNAMES = {
    # alkali / alkaline earth / common metals
    "LI", "NA", "K", "RB", "CS",
    "MG", "CA", "SR", "BA",
    "ZN", "MN", "FE", "CU", "CO", "NI", "CD", "HG",

    # halides and small ions
    "F", "CL", "BR", "I",

    # noble gases / your specific case
    "XE", "KR", "AR",

    # frequent inorganics that show up as HETATM residues
    "PO4", "SO4", "NO3", "CO3", "IUM"
}

WATER_RESNAMES = {"HOH", "WAT", "DOD"}


def pdb_is_ion_only(lines) -> bool:
    """
    True if the file has no protein ATOM records AND all HETATM residues are ions/waters (or none).
    """
    has_protein = any(l.startswith("ATOM") for l in lines)
    if has_protein:
        return False

    het_res = {get_resname(l) for l in lines if l.startswith("HETATM")}
    if not het_res:
        return True  # nothing but headers/remarks/etc.

    allowed = ION_RESNAMES | WATER_RESNAMES
    return het_res.issubset(allowed)


def should_drop_pdb(lines, ligand_resname: str, drop_ion_ligands_even_with_protein: bool = True) -> bool:
    """
    Drop rules:
      A) if file is ion-only (no protein, only ions/waters)
      B) if the *intended ligand* (from filename) is an ion (e.g., XE), optionally even if protein exists
    """
    if pdb_is_ion_only(lines):
        return True

    if drop_ion_ligands_even_with_protein and ligand_resname in ION_RESNAMES:
        return True

    return False


# -----------------------------------------------------------
# Cleaner for one file
# -----------------------------------------------------------

def clean_pdb(fullpath: str, delete_dropped: bool = True):
    fname = os.path.basename(fullpath)
    ligand = get_ligand_from_filename(fname)

    with open(fullpath, "r") as f:
        lines = f.readlines()

    # --- DROP GATE (pre-clean) ---
    if should_drop_pdb(lines, ligand_resname=ligand, drop_ion_ligands_even_with_protein=True):
        msg = f"🧂 DROPPING {fname}  | Ion/ion-only detected (ligand={ligand})"
        if delete_dropped:
            os.remove(fullpath)
            print(msg + " → deleted")
        else:
            print(msg + " → skipped (not deleted)")
        return

    print(f"🧪 Cleaning {fname}  | Keeping ligand: {ligand}")

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
            # keep only matching ligand; remove all others (EDO, HOH, CIT, ions, etc.)
            if res == ligand:
                out.append(line)
            continue

        # Other lines (REMARK, HEADER, etc.)
        out.append(line)

    with open(fullpath, "w") as f:
        f.writelines(out)

    print(f"✔ Cleaned: {fname}\n")


# -----------------------------------------------------------
# MAIN
# -----------------------------------------------------------

def main():
    df = pd.read_csv("CIFdata.csv")

    base_outdir = str(df.iloc[0]["outdir"]).rstrip("/")
    pdb_root = base_outdir + "_PDB"

    if not os.path.isdir(pdb_root):
        raise FileNotFoundError(f"❌ PDB directory not found: {pdb_root}")

    print(f"\n📁 ROOT DIRECTORY: {pdb_root}\n")

    dropped = 0
    cleaned = 0

    for protein in df["protein"].unique():
        protein_dir = os.path.join(pdb_root, protein)

        if not os.path.isdir(protein_dir):
            print(f"⚠️ Skipping missing protein folder: {protein_dir}")
            continue

        print(f"\n=============================")
        print(f"🔬 Processing Protein: {protein}")
        print(f"📂 Folder: {protein_dir}")
        print(f"=============================\n")

        for fname in os.listdir(protein_dir):
            if fname.lower().endswith(".pdb"):
                fullpath = os.path.join(protein_dir, fname)
                before_exists = os.path.exists(fullpath)
                clean_pdb(fullpath, delete_dropped=True)
                after_exists = os.path.exists(fullpath)

                if before_exists and not after_exists:
                    dropped += 1
                else:
                    cleaned += 1

    print(f"\n🎉 DONE — cleaned={cleaned}, dropped={dropped}\n")


if __name__ == "__main__":
    main()

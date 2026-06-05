#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
extract_ligand_atoms.py
----------------------------------------
Scans WAR_PDB/ directory and extracts all ligand atoms 
from each PDB file into a unified CSV.

Output CSV columns:
    Target, pdb_id, Warhead, Residue_ID, Variant, Chain,
    atom_id, atom_name, x, y, z
"""

import os
import csv
from pathlib import Path

# Root folder containing WAR_PDB/<Target>/
ROOT = Path("WAR_PDB")

# Output file
OUTFILE = "Ligand_3D_Atoms.csv"

rows = []

# ---------------------------------------------------------
# Parse PDB file name into metadata
# ---------------------------------------------------------
def parse_filename(filename):
    # Example filename:   4EIN_A_NOH.pdb
    base = Path(filename).stem
    parts = base.split("_")

    if len(parts) < 3:
        raise ValueError(f"Bad filename format: {filename}")

    pdb_id = parts[0]
    chain  = parts[1]
    warhead = parts[2]

    return pdb_id, chain, warhead

# ---------------------------------------------------------
# Extract ligand atoms from PDB file
# ---------------------------------------------------------
def extract_atoms_from_pdb(pdb_file, target):
    pdb_id, chain_code, warhead = parse_filename(pdb_file.name)

    with open(pdb_file, "r") as f:
        for line in f:
            if not line.startswith("HETATM"):
                continue

            resname = line[17:20].strip()
            chain = line[21].strip()
            res_id = line[22:26].strip()
            atom_id = line[6:11].strip()
            atom_name = line[12:16].strip()
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])

            # Only keep ligand atoms belonging to the warhead
            if resname != warhead:
                continue

            rows.append({
                "Target": target,
                "pdb_id": pdb_id,
                "Warhead": warhead,
                "Residue_ID": res_id,
                "Variant": 1,  
                "Chain": chain,
                "atom_id": atom_id,
                "atom_name": atom_name,
                "x": x,
                "y": y,
                "z": z
            })

# ---------------------------------------------------------
# Walk through WAR_PDB/<Target>/ and process all PDBs
# ---------------------------------------------------------
for target_dir in ROOT.iterdir():
    if not target_dir.is_dir():
        continue

    target = target_dir.name

    for pdb_file in target_dir.glob("*.pdb"):
        extract_atoms_from_pdb(pdb_file, target)

# ---------------------------------------------------------
# Save CSV
# ---------------------------------------------------------
with open(OUTFILE, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "Target","pdb_id","Warhead","Residue_ID","Variant","Chain",
        "atom_id","atom_name","x","y","z"
    ])
    writer.writeheader()
    writer.writerows(rows)

print(f"✅ Extracted ligand atom coordinates for {len(rows)} atoms")
print(f"💾 Saved → {OUTFILE}")

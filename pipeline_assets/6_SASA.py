#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import csv
import argparse
import pandas as pd
from pathlib import Path
from Bio.PDB import PDBParser, ShrakeRupley
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

# Output files
ATOM_CSV = "Warhead_SASA_atoms.csv"
SUMMARY_CSV = "Warhead_SASA_summary.csv"
LOG_FILE = "Warhead_SASA.log"
FAILURE_CSV = "SASA_Failure.csv"

DEFAULT_PROBE = 1.4
THRESHOLD = 0.1


# ---------------------------------------------
# HEADER WRITER
# ---------------------------------------------
def write_headers():
    if not os.path.exists(ATOM_CSV):
        with open(ATOM_CSV, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Target","pdb_id","Warhead","Residue_ID","Variant",
                "Chain","atom_id","exact_atom","x","y","z","Exposure_A2"
            ])

    if not os.path.exists(SUMMARY_CSV):
        with open(SUMMARY_CSV, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Target","pdb_id","Warhead","Residue_ID","Variant",
                "Total_atoms","Exposed_atoms","SASA_in_complex_A2",
                "%Exposed","%Buried"
            ])


# ---------------------------------------------
# SASA ANALYSIS
# ---------------------------------------------
def analyze_sasa(pdb_path, probe_radius):

    ligase = pdb_path.parent.name  # e.g., HER2, NUDT5
    stem = pdb_path.stem.split("_")

    pdb_id = stem[0]
    warhead = stem[2] if len(stem) >= 3 else "UNK"

    variant = 1
    if stem[-1].isdigit():
        variant = stem[-1]

    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("s", pdb_path)

    # Remove waters
    for model in structure:
        for chain in model:
            waters = [r for r in chain if r.resname == "HOH"]
            for w in waters:
                chain.detach_child(w.id)

    sr = ShrakeRupley(probe_radius=probe_radius)
    sr.compute(structure, level="A")

    atom_rows = []
    total_atoms = 0
    exposed_atoms = 0
    sasa_total = 0.0
    residue_id = None

    for model in structure:
        for chain in model:
            for residue in chain:
                if residue.resname.strip() == warhead:
                    residue_id = residue.id[1]
                    for atom in residue.get_atoms():
                        sasa = getattr(atom, "sasa", 0.0)
                        total_atoms += 1

                        if sasa > THRESHOLD:
                            exposed_atoms += 1
                            sasa_total += sasa

                            atom_rows.append([
                                ligase, pdb_id, warhead, residue_id, variant,
                                chain.id, atom.serial_number, atom.name,
                                round(atom.coord[0],3),
                                round(atom.coord[1],3),
                                round(atom.coord[2],3),
                                round(sasa,3)
                            ])

    percent_exposed = (exposed_atoms / total_atoms) if total_atoms > 0 else 0.0

    summary_row = [
        ligase, pdb_id, warhead, residue_id if residue_id else "NA",
        variant, total_atoms, exposed_atoms,
        round(sasa_total,3), round(percent_exposed,3),
        round(1 - percent_exposed,3)
    ]

    return atom_rows, summary_row


# ---------------------------------------------
# Worker
# ---------------------------------------------
def process_file(pdb_path, probe_radius):
    try:
        atoms, summary = analyze_sasa(pdb_path, probe_radius)

        with open(ATOM_CSV, "a", newline="") as f:
            csv.writer(f).writerows(atoms)

        with open(SUMMARY_CSV, "a", newline="") as f:
            csv.writer(f).writerow(summary)

        print(f"✅ {pdb_path.name}: {summary[6]}/{summary[5]} exposed ({summary[8]:.2f})")

    except Exception as e:
        print(f"❌ Error in {pdb_path.name}: {e}")


# ---------------------------------------------
# MAIN LOGIC — FIXED DIRECTORY CRAWLING
# ---------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=float, default=DEFAULT_PROBE)
    args = parser.parse_args()

    # Read CIFdata.csv to locate WAR_PDB
    df = pd.read_csv("CIFdata.csv")

    base_outdir = str(df.iloc[0]["outdir"]).rstrip("/")
    pdb_root = base_outdir + "_PDB"       # => WAR_PDB

    if not os.path.isdir(pdb_root):
        raise RuntimeError(f"ERROR: Directory not found: {pdb_root}")

    print(f"\n📁 Scanning WARHEAD PDB ROOT: {pdb_root}")
    print(f"📁 Step 6 input root resolved: {Path(pdb_root).resolve()}")

    # Collect ALL pdb files under WAR_PDB/*/*.pdb
    pdb_files = list(Path(pdb_root).rglob("*.pdb"))

    print(f"🔎 Found {len(pdb_files)} PDB files")
    if pdb_files:
        print(f"🧾 Step 6 sample PDBs: {[str(p.relative_to(pdb_root)) for p in pdb_files[:10]]}")
    print(f"🧠 Using {multiprocessing.cpu_count()-1} cores\n")

    write_headers()

    if not pdb_files:
        pd.DataFrame([{
            "reason": "No PDB files found for SASA analysis",
            "pdb_root": str(Path(pdb_root).resolve()),
            "discovered_pdb_count": 0,
        }]).to_csv(FAILURE_CSV, index=False)
        raise RuntimeError(
            f"Step 6 found 0 PDB files under {Path(pdb_root).resolve()}. "
            f"Wrote {FAILURE_CSV}."
        )

    with ProcessPoolExecutor(max_workers=multiprocessing.cpu_count()-1) as exe:
        futures = {exe.submit(process_file, p, args.probe): p for p in pdb_files}
        for i, f in enumerate(as_completed(futures), 1):
            if i % 25 == 0:
                print(f"📊 Progress: {i}/{len(pdb_files)}")

    print("\n🎉 WARHEAD SASA COMPLETE\n")


if __name__ == "__main__":
    main()

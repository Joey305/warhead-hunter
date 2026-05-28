#!/usr/bin/env python3
# 15_ResultsMerged.py
# Final atom-level merge: geometry + SASA exposure

from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "TARGET_RESULTS"

ligand_atoms_csv = RESULTS_DIR / "Ligand_3D_Atoms.csv"

# Locate SASA atoms file robustly
sasa_matches = list(RESULTS_DIR.glob("Warhead_SASA_*atoms*.csv"))
if not sasa_matches:
    raise FileNotFoundError(
        f"No Warhead_SASA atoms file found in {RESULTS_DIR}"
    )

sasa_csv = sasa_matches[0]
print(f"🧬 Using SASA file: {sasa_csv.name}")

# ------------------------------------------------------------------
# Load data
# ------------------------------------------------------------------

lig = pd.read_csv(ligand_atoms_csv)
sasa = pd.read_csv(sasa_csv)

# ------------------------------------------------------------------
# Select ONLY what we need from SASA
# (this avoids duplicate columns entirely)
# ------------------------------------------------------------------

sasa_keyed = sasa[
    ["Target", "pdb_id", "Warhead", "Chain", "atom_id", "Exposure_A2"]
].copy()

# Ensure numeric exposure
sasa_keyed["Exposure_A2"] = pd.to_numeric(
    sasa_keyed["Exposure_A2"], errors="coerce"
)

# ------------------------------------------------------------------
# LEFT MERGE: keep ALL ligand atoms
# ------------------------------------------------------------------

merged = lig.merge(
    sasa_keyed,
    on=["Target", "pdb_id", "Warhead", "Chain", "atom_id"],
    how="left"
)

# ------------------------------------------------------------------
# Fill atoms with no SASA measurement → buried
# ------------------------------------------------------------------

merged["Exposure_A2"] = merged["Exposure_A2"].fillna(0.0)

# ------------------------------------------------------------------
# Save final annotated atom table
# ------------------------------------------------------------------

out_csv = RESULTS_DIR / "Ligand_3D_Atoms_with_SASA.csv"
merged.to_csv(out_csv, index=False)

print(f"✅ Wrote merged atom table: {out_csv.name}")
print(f"📊 Total atoms: {len(merged)}")
print("🎉 RESULTS MERGE COMPLETE")

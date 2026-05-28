#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Target_Scaffolds.py
--------------------------------------------
Unified scaffold engine for PROTEIN TARGETS.

Input:
    Resolved_SASA_Summary.csv
        Required columns:
            Target
            Ligand_Resolved
            SMILES

Outputs (parallel to ligand-based scaffold system):
    Target_Table/Target_Recruiters_Scaffold.csv
    Target_Table/Target_Scaffold_Frequency.csv
    Target_Table/Target_Scaffold_Summary.csv
    Target_Table/Target_Scaffold_Data.csv
    Target_Table/Target_Scaffold.log

Features:
    ✔ Murcko scaffolds
    ✔ Scaffold hashing for invalid SMILES
    ✔ Scaffold frequency and diversity
    ✔ Shannon diversity index
    ✔ Murcko classification
    ✔ Scaffold 3D center-of-mass (RDKit embed)
    ✔ Connectivity score (target–scaffold bipartite degree)
"""

import pandas as pd
import numpy as np
from math import log
from pathlib import Path
from collections import Counter

import hashlib
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem.Scaffolds import MurckoScaffold
import networkx as nx


# ============================================================
# CONFIG
# ============================================================
INPUT = Path("Resolved_SASA_Summary.csv")

TABLE_DIR = Path("Target_Table")
TABLE_DIR.mkdir(exist_ok=True)

OUT1 = TABLE_DIR / "Target_Recruiters_Scaffold.csv"
OUT2 = TABLE_DIR / "Target_Scaffold_Frequency.csv"
OUT3 = TABLE_DIR / "Target_Scaffold_Summary.csv"
OUT4 = TABLE_DIR / "Target_Scaffold_Data.csv"
LOG  = TABLE_DIR / "Target_Scaffold.log"


# ============================================================
# HELPERS
# ============================================================
def safe_mol(smiles):
    try:
        return Chem.MolFromSmiles(smiles)
    except:
        return None


def murcko_scaffold(smi):
    """Return Murcko scaffold SMILES or None."""
    mol = safe_mol(smi)
    if mol is None:
        return None
    try:
        core = MurckoScaffold.GetScaffoldForMol(mol)
        if core is None or core.GetNumAtoms() == 0:
            return None
        return Chem.MolToSmiles(core, canonical=True)
    except:
        return None


def scaffold_hash(smi):
    """8-character deterministic hash for fallback scaffolds."""
    return hashlib.sha1(str(smi).encode()).hexdigest()[:8]


def shannon_diversity(counts):
    total = sum(counts.values())
    if total == 0:
        return 0.0
    H = -sum((n / total) * log(n / total) for n in counts.values() if n > 0)
    return H


def classify_scaffold(scaf_smi):
    if not scaf_smi:
        return "Unknown"
    mol = Chem.MolFromSmiles(scaf_smi)
    if mol is None:
        return "Unknown"
    core = MurckoScaffold.GetScaffoldForMol(mol)
    if not core:
        return "Unknown"

    ring_info = core.GetRingInfo()
    rings = ring_info.NumRings()
    hetero = sum(1 for a in core.GetAtoms() if a.GetAtomicNum() not in (1, 6))

    if rings == 0:
        return "Acyclic"
    elif rings == 1:
        return "Monocyclic_Hetero" if hetero else "Monocyclic"
    elif rings == 2:
        return "Bicyclic_Hetero" if hetero else "Bicyclic"
    else:
        return "Polycyclic_Hetero" if hetero else "Polycyclic"


def scaffold_center_of_mass(smi):
    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return np.nan, np.nan, np.nan
    try:
        mol = Chem.AddHs(mol)
        AllChem.EmbedMolecule(mol, randomSeed=42)
        conf = mol.GetConformer()
        coords = np.array([[conf.GetAtomPosition(i).x,
                            conf.GetAtomPosition(i).y,
                            conf.GetAtomPosition(i).z]
                           for i in range(mol.GetNumAtoms())])
        return coords.mean(axis=0)
    except:
        return np.nan, np.nan, np.nan


# ============================================================
# MAIN
# ============================================================
def main():

    df = pd.read_csv(INPUT)
    print(f"📥 Loaded {len(df)} rows from {INPUT.name}")

    # rename to match internal structure
    df = df.rename(columns={
        "Target": "Protein",
        "Ligand_Resolved": "Recruiter",
    })

    df = df.dropna(subset=["Protein", "Recruiter", "SMILES"])
    df = df.drop_duplicates(subset=["Protein", "Recruiter"])

    targets = sorted(df["Protein"].unique())
    print(f"🔬 Found {len(targets)} protein targets.")

    out_rows = []
    frequency_rows = []
    summary_rows = []

    # ------------------------------------------------------------
    # STAGE 1 — ASSIGN SCAFFOLDS PER TARGET
    # ------------------------------------------------------------
    for protein, sub in df.groupby("Protein"):

        sub = sub.copy()

        # Murcko
        sub["Scaffold_SMILES"] = sub["SMILES"].apply(murcko_scaffold)

        # hash fallback for invalid Murckos
        sub["Scaffold_Hash"] = [
            scaffold_hash(smi if scaf is None else scaf)
            for smi, scaf in zip(sub["SMILES"], sub["Scaffold_SMILES"])
        ]

        # canonical scaffold IDs
        unique_scaffolds = sub["Scaffold_Hash"].unique()
        scaffold_map = {sc: f"{protein}_SCAF_{i+1}" for i, sc in enumerate(unique_scaffolds)}

        sub["Scaffold_ID"] = sub["Scaffold_Hash"].map(scaffold_map)

        # OUTPUT ROWS
        for _, row in sub.iterrows():
            out_rows.append({
                "Protein": protein,
                "Recruiter": row["Recruiter"],
                "SMILES": row["SMILES"],
                "Scaffold_ID": row["Scaffold_ID"],
                "Scaffold_SMILES": row["Scaffold_SMILES"],
                "Scaffold_Hash": row["Scaffold_Hash"],
            })

        # FREQUENCY TABLE
        freq = (
            sub.groupby("Scaffold_ID")["Recruiter"]
            .nunique()
            .reset_index(name="Recruiter_Count")
        )
        freq["Protein"] = protein
        frequency_rows += freq.to_dict("records")

        # DIVERSITY SUMMARY
        counts = dict(zip(freq["Scaffold_ID"], freq["Recruiter_Count"]))
        total = sum(counts.values())
        unique = len(counts)
        diversity = unique / total if total > 0 else 0
        H = shannon_diversity(counts)

        summary_rows.append({
            "Protein": protein,
            "Unique_Scaffolds": unique,
            "Total_Recruiters": total,
            "Diversity_Score": round(diversity, 3),
            "Shannon_Index": round(H, 3),
        })

    # Save Stage 1 outputs
    pd.DataFrame(out_rows).to_csv(OUT1, index=False)
    pd.DataFrame(frequency_rows).to_csv(OUT2, index=False)
    pd.DataFrame(summary_rows).to_csv(OUT3, index=False)

    print("✅ Stage 1 complete — base scaffold mapping and diversity scores saved.")

    # ------------------------------------------------------------
    # STAGE 2 — ADD ADVANCED METRICS
    # ------------------------------------------------------------
    df_freq = pd.DataFrame(frequency_rows)
    df_scaf = pd.DataFrame(out_rows)

    # merge scaffold SMILES for computation
    scaffold_smiles_map = dict(zip(df_scaf["Scaffold_ID"], df_scaf["Scaffold_SMILES"]))

    adv_rows = []
    G = nx.Graph()

    for scaf_id, group in df_scaf.groupby("Scaffold_ID"):

        protein = group["Protein"].iloc[0]
        smi = scaffold_smiles_map.get(scaf_id, "")

        # classification
        murcko_smi = murcko_scaffold(smi) if smi else ""
        cls = classify_scaffold(murcko_smi)

        # center of mass
        x, y, z = scaffold_center_of_mass(smi) if smi else (np.nan, np.nan, np.nan)

        # connectivity: how many times target connects to this scaffold
        G.add_edge(protein, scaf_id)

        adv_rows.append({
            "Protein": protein,
            "Scaffold_ID": scaf_id,
            "Original_Scaffold_SMILES": smi,
            "Murcko_SMILES": murcko_smi,
            "Scaffold_Class": cls,
            "CenterOfMass_X": x,
            "CenterOfMass_Y": y,
            "CenterOfMass_Z": z,
        })

    df_adv = pd.DataFrame(adv_rows)

    # add connectivity
    df_adv["Connectivity"] = df_adv["Scaffold_ID"].map(dict(G.degree()))

    # recruiter density
    totals = df_freq.groupby("Protein")["Recruiter_Count"].sum()
    df_freq["Recruiter_Density"] = df_freq["Recruiter_Count"] / df_freq["Protein"].map(totals)

    df_adv = df_adv.merge(df_freq, on=["Protein", "Scaffold_ID"], how="left")

    # save advanced table
    df_adv.to_csv(OUT4, index=False)

    # log output
    with open(LOG, "w") as f:
        f.write(f"Scaffolds processed: {len(df_adv)}\n")
        f.write(f"Proteins analyzed: {len(targets)}\n")
        f.write("Pipeline complete.\n")

    print("🎉 Stage 2 complete — advanced scaffold data saved.")
    print(f"📦 All tables written to {TABLE_DIR}/")

if __name__ == "__main__":
    main()

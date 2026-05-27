#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
UNIFIED LIGAND METADATA PIPELINE — PARALLEL EDITION (FULL VERSION)
===============================================================================
Author: Joseph-Michael Schulz (UM BMB)

Fixes in this version:
  ✅ 5CharMAP is ALWAYS applied if present + non-empty
  ✅ Accepts 5CharMAP columns ligandX / ligandx (case-insensitive)
  ✅ NEW: supports ligand3 (3-letter) → ligand5 mapping when Warhead is 3-letter
  ✅ Mapping precedence (SMILES key resolution):
        (pdb, ligandX) -> ligand5  (highest; placeholder A00/A01...)
        (pdb, ligand3) -> ligand5  (NEW; if Warhead is 3-letter)
        ligandX -> ligand5         (fallback)
        ligand3 -> ligand5         (NEW fallback)
        Warhead itself             (last resort)
  ✅ Prevents placeholder ligands (A00/A01/...) from hijacking SMILES lookup
  ✅ NEW OUTPUT COLUMN:
        Warhead_5 (blank if no 5-letter mapping was used)
===============================================================================
"""

import os
import sys
import math
import argparse
import pandas as pd
import requests
from tqdm import tqdm
from multiprocessing import Pool, cpu_count
from collections import defaultdict

# ---------------- RDKit ----------------
from rdkit import Chem
from rdkit import RDLogger
from rdkit.Chem import Descriptors, Crippen, rdMolDescriptors, QED
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams
from rdkit.Chem.MolStandardize import rdMolStandardize

RDLogger.DisableLog("rdApp.*")

# SA Score
try:
    import sascorer
    HAS_SA = True
except ImportError:
    HAS_SA = False


# ============================================================================ #
# HELPERS
# ============================================================================ #

def normalize_chain(val, default="A"):
    """Normalize chain labels."""
    if pd.isna(val):
        return default
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return default
    if len(s) == 1 and s.isalpha():
        return s.upper()
    if s.isdigit():
        i = int(s)
        if 1 <= i <= 26:
            return chr(ord("A") + i - 1)
        return default
    for ch in s:
        if ch.isalpha():
            return ch.upper()
    return default



from rdkit import Chem

def safe_mol(smi):
    """Safely load SMILES → Mol, ensuring RingInfo is initialized."""
    if not smi:
        return None
    try:
        mol = Chem.MolFromSmiles(smi, sanitize=True)
        if mol is not None:
            return mol
    except Exception:
        pass

    # Fallback: build unsanitized, then try minimal sanitize + ring init
    try:
        mol = Chem.MolFromSmiles(smi, sanitize=False)
        if mol is None:
            return None

        # Try to sanitize. If full sanitize fails, try partial steps.
        try:
            Chem.SanitizeMol(mol)
        except Exception:
            # Partial sanitize: properties + aromaticity are often enough
            try:
                Chem.SanitizeMol(
                    mol,
                    sanitizeOps=(
                        Chem.SanitizeFlags.SANITIZE_PROPERTIES |
                        Chem.SanitizeFlags.SANITIZE_SYMMRINGS |
                        Chem.SanitizeFlags.SANITIZE_SETAROMATICITY
                    )
                )
            except Exception:
                return None  # too broken, skip

        # Ensure ring info exists (belt-and-suspenders)
        try:
            mol.GetRingInfo()
        except Exception:
            return None

        return mol
    except Exception:
        return None



def safe_bertz(mol):
    try:
        return rdMolDescriptors.CalcBertzCT(mol)
    except Exception:
        try:
            return Descriptors.BertzCT(mol)
        except Exception:
            return math.nan


def safe_chiral_atoms(mol):
    try:
        Chem.AssignStereochemistry(mol, force=True, cleanIt=True)
    except Exception:
        pass

    try:
        return int(rdMolDescriptors.CalcNumAtomStereoCenters(mol))
    except Exception:
        try:
            centers = Chem.FindMolChiralCenters(
                mol,
                includeUnassigned=True,
                force=True,
                useLegacyImplementation=False,
            )
            return int(len(centers))
        except Exception:
            return math.nan


def druglikeness_rules(d):
    mw, logp, tpsa = d["MW"], d["LogP"], d["TPSA"]
    hbd, hba, rot = d["HBD"], d["HBA"], d["Rotatable_Bonds"]
    heavy = d["Heavy_Atom_Count"]

    lipinski = sum([mw > 500, logp > 5, hbd > 5, hba > 10]) <= 1
    veber = (tpsa <= 140 and rot <= 10)
    egan = (tpsa <= 131 and logp <= 5.88)
    ghose = (160 <= mw <= 480) and (-0.4 <= logp <= 5.6) and (20 <= heavy <= 70)
    muegge = (200 <= mw <= 600 and -2 <= logp <= 5 and hba <= 10
              and hbd <= 5 and tpsa <= 150)

    return {
        "Lipinski_Pass": lipinski,
        "Veber_Pass": veber,
        "Egan_Pass": egan,
        "Ghose_Pass": ghose,
        "Muegge_Pass": muegge
    }


def check_pains(mol):
    params = FilterCatalogParams()
    params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS_A)
    params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS_B)
    params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS_C)
    cat = FilterCatalog(params)
    matches = cat.GetMatches(mol)
    return "; ".join([m.GetDescription() for m in matches]) if matches else "None"


def check_brenk(mol):
    params = FilterCatalogParams()
    params.AddCatalog(FilterCatalogParams.FilterCatalogs.BRENK)
    cat = FilterCatalog(params)
    matches = cat.GetMatches(mol)
    return "; ".join([m.GetDescription() for m in matches]) if matches else "None"


def compute_descriptors(mol):
    d = {
        "MW": Descriptors.MolWt(mol),
        "LogP": Crippen.MolLogP(mol),
        "TPSA": rdMolDescriptors.CalcTPSA(mol),
        "HBA": rdMolDescriptors.CalcNumHBA(mol),
        "HBD": rdMolDescriptors.CalcNumHBD(mol),
        "Rotatable_Bonds": rdMolDescriptors.CalcNumRotatableBonds(mol),
        "Ring_Count": rdMolDescriptors.CalcNumRings(mol),
        "Aromatic_Rings": rdMolDescriptors.CalcNumAromaticRings(mol),
        "Fraction_CSP3": rdMolDescriptors.CalcFractionCSP3(mol),
        "Heavy_Atom_Count": mol.GetNumHeavyAtoms(),
        "Chiral_Atoms": safe_chiral_atoms(mol),
        "Formal_Charge": Chem.GetFormalCharge(mol),
        "QED": QED.qed(mol),
        "BertzCT": safe_bertz(mol),
        "HallKierAlpha": Descriptors.HallKierAlpha(mol),
        "Kappa1": Descriptors.Kappa1(mol),
        "Kappa2": Descriptors.Kappa2(mol),
        "Kappa3": Descriptors.Kappa3(mol),
        "NumSpiroAtoms": rdMolDescriptors.CalcNumSpiroAtoms(mol),
        "NumBridgeheadAtoms": rdMolDescriptors.CalcNumBridgeheadAtoms(mol),
        "NumAliphaticRings": rdMolDescriptors.CalcNumAliphaticRings(mol),
        "NumAromaticRings": rdMolDescriptors.CalcNumAromaticRings(mol),
        "NumSaturatedRings": rdMolDescriptors.CalcNumSaturatedRings(mol),
        "NumHeteroAtoms": rdMolDescriptors.CalcNumHeteroatoms(mol),
        "MolMR": Descriptors.MolMR(mol),
        "SA_Score": sascorer.calculateScore(mol) if HAS_SA else math.nan
    }
    d.update(druglikeness_rules(d))
    return d


def fetch_rcsb(lig5):
    lig5 = str(lig5).strip().upper()
    url = f"https://data.rcsb.org/rest/v1/core/chemcomp/{lig5}"
    try:
        r = requests.get(url, timeout=4)
        if r.status_code == 200:
            cc = r.json().get("chem_comp", {})
            return {
                "Name": cc.get("name", ""),
                "Formula": cc.get("formula", ""),
                "Type": cc.get("type", "")
            }
    except Exception:
        pass
    return {"Name": "", "Formula": "", "Type": ""}


# ============================================================================ #
# PARALLEL WORKER
# ============================================================================ #

# def process_single_ligand(lig_and_smi):
#     lig, smi = lig_and_smi
#     lig = str(lig).strip().upper()

#     if not smi:
#         return None

#     # --- NEW: strip salts + drop pure ions/inorganics ---
#     mol, parent_smi, was_stripped, drop_reason = clean_parent_from_smiles(
#         smi,
#         drop_pure_ions=True,
#         drop_metal_containing=False  # leave False unless you REALLY want it
#     )
#     if drop_reason is not None:
#         return None

#     # RCSB metadata keyed by ligand ID (still fine)
#     rc = fetch_rcsb(lig)

#     # compute descriptors on the cleaned parent
#     rd = compute_descriptors(mol)

#     return {
#         "Ligand": lig,
#         "Name": rc["Name"],
#         "Formula": rc["Formula"],
#         "Type": rc["Type"],
#         "SMILES": smi,                       # original from file
#         "Parent_SMILES": parent_smi,         # NEW: cleaned parent
#         "Salt_Stripped": bool(was_stripped), # NEW
#         "Canonical_SMILES": parent_smi,      # canonical of parent
#         "InChI": Chem.MolToInchi(mol),
#         "InChIKey": Chem.InchiToInchiKey(Chem.MolToInchi(mol)),
#         **rd,
#         "PAINS_Hits": check_pains(mol),
#         "Brenk_Hits": check_brenk(mol),
#     }

def process_single_ligand(lig_and_smi):
    lig, smi = lig_and_smi
    lig = str(lig).strip().upper()

    if not smi:
        return None

    try:
        mol, parent_smi, was_stripped, drop_reason = clean_parent_from_smiles(
            smi,
            drop_pure_ions=True,
            drop_metal_containing=False
        )
        if drop_reason is not None or mol is None:
            return None

        # Force ring init check here too (debug-friendly)
        mol.GetRingInfo()

        rc = fetch_rcsb(lig)
        rd = compute_descriptors(mol)

        return {
            "Ligand": lig,
            "Name": rc["Name"],
            "Formula": rc["Formula"],
            "Type": rc["Type"],
            "SMILES": smi,
            "Parent_SMILES": parent_smi,
            "Salt_Stripped": bool(was_stripped),
            "Canonical_SMILES": parent_smi,
            "InChI": Chem.MolToInchi(mol),
            "InChIKey": Chem.InchiToInchiKey(Chem.MolToInchi(mol)),
            **rd,
            "PAINS_Hits": check_pains(mol),
            "Brenk_Hits": check_brenk(mol),
        }

    except Exception as e:
        # Write a minimal failure row instead of crashing the pool
        return {"Ligand": lig, "SMILES": smi, "Error": repr(e)}



# ============================================================================ #
# INPUT HELPERS
# ============================================================================ #

def choose_csv():
    files = [f for f in os.listdir(".") if f.lower().endswith(".csv")]
    if not files:
        raise FileNotFoundError("❌ No CSV files found in directory.")

    print("\n📂 Available CSV files:\n")
    for i, f in enumerate(files, 1):
        print(f"{i}. {f}")

    while True:
        choice = input("\nSelect SASA CSV by number: ")
        if choice.isdigit() and 1 <= int(choice) <= len(files):
            return files[int(choice) - 1]
        print("❌ Invalid choice.")


def find_smiles_file():
    for f in os.listdir("."):
        if f.lower() == "components-smiles-stereo-oe.smi":
            return f
    raise FileNotFoundError("❌ components-smiles-stereo-oe.smi not found.")



# ---------------------------------------------------------------------------
# FINAL CLEAN: strip salts/counterions + optionally filter pure ions/inorganics
# ---------------------------------------------------------------------------

LF_CHOOSER = rdMolStandardize.LargestFragmentChooser(preferOrganic=True)

COMMON_ION_SMILES = {
    "[Li+]", "[Na+]", "[K+]", "[Rb+]", "[Cs+]",
    "[Mg+2]", "[Ca+2]", "[Zn+2]", "[Mn+2]", "[Fe+2]", "[Fe+3]",
    "[Cl-]", "[Br-]", "[I-]", "[F-]",
    "[PF6-]", "[BF4-]", "[ClO4-]", "[NO3-]", "[SO4-2]",
    "[Xe]",
}


def _num_carbons(mol):
    return sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 6)

def _has_any_metal(mol):
    # crude but effective: anything beyond common organics/halogens
    # (tune to your needs)
    organic_like = {1, 5, 6, 7, 8, 9, 15, 16, 17, 35, 53}  # H,B,C,N,O,F,P,S,Cl,Br,I
    return any(a.GetAtomicNum() not in organic_like for a in mol.GetAtoms())

def clean_parent_from_smiles(smi, drop_pure_ions=True, drop_metal_containing=False):
    """
    Returns: (mol_parent, parent_smiles, was_stripped, drop_reason_or_None)

    - Strips salts/counterions by taking the LargestFragment (prefer organic).
    - Optionally drops pure ions/inorganics (no carbon, tiny fragments).
    - Optionally drops metal-containing ligands (OFF by default; can be too aggressive).
    """
    if not smi:
        return None, "", False, "empty_smiles"

    smi_norm = smi.strip()
    up = smi_norm.upper().replace(" ", "")
    if up in COMMON_ION_SMILES:
        return None, "", False, "common_ion"

    mol = safe_mol(smi_norm)
    if not mol:
        return None, "", False, "rdkit_parse_failed"

    # Strip salts / pick largest fragment (prefer organic)
    was_stripped = ("." in smi_norm)
    try:
        parent = LF_CHOOSER.choose(mol)
    except Exception:
        parent = mol  # fallback

    if not parent:
        return None, "", was_stripped, "no_parent_after_strip"

    # Optional metal filter (can remove valid organometallic ligands!)
    if drop_metal_containing and _has_any_metal(parent):
        return None, "", was_stripped, "metal_containing"

    # Drop pure ions / inorganic tiny fragments
    if drop_pure_ions:
        c = _num_carbons(parent)
        heavy = parent.GetNumHeavyAtoms()
        atoms = parent.GetNumAtoms()
        # Typical "junk": no carbon AND very small (single ions like [Li+], [Cl-], etc.)
        if c == 0 and (heavy <= 3 or atoms <= 3):
            return None, "", was_stripped, "inorganic_small_no_carbon"

    parent_smi = Chem.MolToSmiles(parent, canonical=True)
    return parent, parent_smi, was_stripped, None


# ============================================================================ #
# 5CHARMAP LOADER (ALWAYS USE IF PRESENT)
# ============================================================================ #
def load_5charmap_maps(map_path="5CharMAP.csv"):
    """
    If 5CharMAP.csv exists and has usable rows, we use it.
    If it exists but is empty (or header-only), we disable alias mapping and continue.

    Required columns (case-insensitive):
      - pdb
      - ligand5
      - ligandX / ligandx

    Optional:
      - ligand3
    """

    if not os.path.exists(map_path):
        print("ℹ️ 5CharMAP.csv not found → alias mapping disabled.")
        return {}, {}, {}, {}

    # Read CSV safely (handles totally empty file)
    try:
        alias = pd.read_csv(map_path)
    except pd.errors.EmptyDataError:
        print("ℹ️ 5CharMAP.csv is empty → alias mapping disabled.")
        return {}, {}, {}, {}
    except Exception as e:
        raise RuntimeError(f"Failed to read 5CharMAP.csv: {e}")

    # Normalize columns + trim cells
    alias = alias.copy()
    alias.columns = [str(c).strip().lower() for c in alias.columns]
    alias = alias.fillna("")
    alias = alias.apply(lambda col: col.map(lambda x: str(x).strip() if x is not None else ""))

    # Drop rows that are entirely blank (header-only CSVs / whitespace rows)
    if len(alias.columns) == 0 or alias.empty:
        print("ℹ️ 5CharMAP.csv has no usable rows → alias mapping disabled.")
        return {}, {}, {}, {}

    alias = alias[~(alias == "").all(axis=1)]
    if alias.empty:
        print("ℹ️ 5CharMAP.csv has no usable rows → alias mapping disabled.")
        return {}, {}, {}, {}

    # Required columns
    if "pdb" not in alias.columns:
        raise RuntimeError(f"5CharMAP.csv missing required column: pdb (found: {list(alias.columns)})")
    if "ligand5" not in alias.columns:
        raise RuntimeError(f"5CharMAP.csv missing required column: ligand5 (found: {list(alias.columns)})")
    if "ligandx" not in alias.columns:
        raise RuntimeError(f"5CharMAP.csv missing required column: ligandX / ligandx (found: {list(alias.columns)})")

    has_lig3 = "ligand3" in alias.columns

    # Canonicalize
    alias["pdb"] = alias["pdb"].astype(str).str.lower().str.strip()
    alias["ligandx"] = alias["ligandx"].astype(str).str.upper().str.strip()
    alias["ligand5"] = alias["ligand5"].astype(str).str.upper().str.strip()
    if has_lig3:
        alias["ligand3"] = alias["ligand3"].astype(str).str.upper().str.strip()

    # Build maps
    map_pdb_x_to_5 = {
        (r["pdb"], r["ligandx"]): r["ligand5"]
        for _, r in alias.iterrows()
        if r["pdb"] and r["ligandx"] and r["ligand5"]
    }

    map_x_to_5 = (
        alias[alias["ligandx"] != ""]
        .drop_duplicates("ligandx")
        .set_index("ligandx")["ligand5"]
        .to_dict()
    )

    map_pdb_3_to_5 = {}
    map_3_to_5 = {}
    if has_lig3:
        map_pdb_3_to_5 = {
            (r["pdb"], r["ligand3"]): r["ligand5"]
            for _, r in alias.iterrows()
            if r["pdb"] and r["ligand3"] and r["ligand5"]
        }
        map_3_to_5 = (
            alias[alias["ligand3"] != ""]
            .drop_duplicates("ligand3")
            .set_index("ligand3")["ligand5"]
            .to_dict()
        )

    print(f"🧭 5CharMAP loaded: {len(map_pdb_x_to_5)} (pdb,ligX)->lig5 mappings")
    if has_lig3:
        print(f"🧭 5CharMAP loaded: {len(map_pdb_3_to_5)} (pdb,lig3)->lig5 mappings")
    else:
        print("ℹ️ 5CharMAP has no ligand3 column → 3-letter mapping disabled.")

    return map_pdb_x_to_5, map_x_to_5, map_pdb_3_to_5, map_3_to_5






def resolve_smiles_key_and_warhead5(pdb, warhead, map_pdb_x_to_5, map_x_to_5, map_pdb_3_to_5, map_3_to_5):
    """
    Decide what code we will use to look up SMILES, and whether Warhead_5 should be set.

    Precedence:
      1) exact (pdb, warhead as ligandX) -> ligand5
      2) exact (pdb, warhead as ligand3) -> ligand5 (only if warhead looks 3-letter)
      3) global ligandX -> ligand5
      4) global ligand3 -> ligand5 (only if warhead looks 3-letter)
      5) warhead itself

    Returns:
      (smiles_key, warhead_5)
        smiles_key: the identifier to use against smiles_map
        warhead_5:  5-letter ligand id if mapping used, else ""
    """
    pdb = str(pdb).strip().lower()
    w = str(warhead).strip().upper()

    # 1) (pdb, ligandX)
    lig5 = map_pdb_x_to_5.get((pdb, w))
    if lig5:
        return lig5, lig5

    # 2) (pdb, ligand3) if warhead is 3-letter
    if len(w) == 3 and w.isalnum():
        lig5 = map_pdb_3_to_5.get((pdb, w))
        if lig5:
            return lig5, lig5

    # 3) global ligandX
    lig5 = map_x_to_5.get(w)
    if lig5:
        return lig5, lig5

    # 4) global ligand3 if warhead is 3-letter
    if len(w) == 3 and w.isalnum():
        lig5 = map_3_to_5.get(w)
        if lig5:
            return lig5, lig5

    # 5) fallback to warhead itself
    return w, ""


# ============================================================================ #
# CHAIN FROM WAR_PDB FILENAMES
# ============================================================================ #

def build_chain_map_from_warpdb(war_pdb_root="WAR_PDB"):
    """
    Expected filenames: <pdb>_<chain>_<ligandX>.pdb
      e.g., 7jxh_B_VOY.pdb

    Returns:
      chain_map_multi[(pdb, ligandX)] = [chain1, chain2, ...] sorted by filename
    """
    chain_map_multi = defaultdict(list)

    if not os.path.isdir(war_pdb_root):
        print(f"⚠️ WAR_PDB root not found: {war_pdb_root} (will fallback to existing Chain/Variant/default)")
        return dict(chain_map_multi)

    pdb_files = []
    for root, _, files in os.walk(war_pdb_root):
        for fn in files:
            if fn.lower().endswith(".pdb"):
                pdb_files.append(os.path.join(root, fn))

    pdb_files = sorted(pdb_files, key=lambda p: os.path.basename(p).lower())

    for path in pdb_files:
        fn = os.path.basename(path)
        base = fn[:-4]
        parts = base.split("_", 2)
        if len(parts) < 3:
            continue

        pdb = parts[0].strip().lower()
        chain_token = parts[1].strip()
        ligX = parts[2].strip().upper()

        chain = normalize_chain(chain_token, default="A")
        chain_map_multi[(pdb, ligX)].append(chain)

    print(f"🔗 WAR_PDB chain map built: {len(chain_map_multi)} (pdb,ligX) groups")
    return dict(chain_map_multi)


def assign_chains_from_warpdb(df, PDB_COL, chain_map_multi):

    if "Chain" in df.columns:
        df["Chain"] = df["Chain"].apply(lambda v: normalize_chain(v, default="A"))
    elif "Variant" in df.columns:
        df["Chain"] = df["Variant"].apply(lambda v: normalize_chain(v, default="A"))
    else:
        df["Chain"] = "A"

    df["_row_i"] = range(len(df))

    for (pdb_val, ligX), g in df.groupby([PDB_COL, "Warhead"], sort=False):
        pdb = str(pdb_val).strip().lower()
        ligX = str(ligX).strip().upper()
        chains = chain_map_multi.get((pdb, ligX), [])
        if not chains:
            continue

        idx = g.sort_values("_row_i").index.tolist()

        if len(chains) == len(idx):
            for i, row_idx in enumerate(idx):
                df.at[row_idx, "Chain"] = chains[i]
        else:
            for i, row_idx in enumerate(idx):
                if i < len(chains):
                    df.at[row_idx, "Chain"] = chains[i]

    if "Variant" in df.columns:
        df.drop(columns=["Variant"], inplace=True)
    df.drop(columns=["_row_i"], inplace=True)

    return df


# ============================================================================ #
# MAIN
# ============================================================================ #

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", type=str, help="Skip prompt and use the specified SASA CSV.")
    parser.add_argument("--file", type=str, help="Explicitly specify the SASA CSV.")
    parser.add_argument("--war_pdb_root", type=str, default="WAR_PDB",
                        help="Path to WAR_PDB root (default: WAR_PDB)")
    args = parser.parse_args()

    # 1) SMILES file
    SMILES_FILE = find_smiles_file()
    print(f"📂 Using SMILES file: {SMILES_FILE}")

    smiles_map = {}
    with open(SMILES_FILE, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                smi = parts[0].strip()
                lig = parts[1].strip().upper()
                if lig and smi:
                    smiles_map[lig] = smi

    print(f"\n🧬 Loaded {len(smiles_map)} SMILES entries.")
    print(f"🔎 Sample SMILES keys: {list(smiles_map.keys())[:15]}\n")

    # 2) Select SASA CSV
    if args.file:
        sasa_file = args.file
        print(f"📄 Using SASA file (--file): {sasa_file}")
    elif args.auto:
        sasa_file = args.auto
        print(f"🤖 AUTO MODE: {sasa_file}")
    elif os.path.exists("Resolved_SASA_Summary.csv"):
        sasa_file = "Resolved_SASA_Summary.csv"
        print("📄 Using existing Resolved_SASA_Summary.csv (default).")
    else:
        sasa_file = choose_csv()
        print(f"📄 Using SASA file: {sasa_file}")

    # 3) Load table
    df = pd.read_csv(sasa_file)
    print("📥 Loaded SASA table.")

    if "Warhead" not in df.columns:
        raise KeyError("SASA table must contain 'Warhead' column (ligand code).")

    # 4) Detect PDB column
    if "pdb_id" in df.columns:
        PDB_COL = "pdb_id"
    elif "pdb" in df.columns:
        PDB_COL = "pdb"
    else:
        raise KeyError("SASA table must contain 'pdb_id' or 'pdb' column.")

    df[PDB_COL] = df[PDB_COL].astype(str).str.strip().str.lower()
    df["Warhead"] = df["Warhead"].astype(str).str.strip().str.upper()

    # 5) Load 5CharMAP (ALWAYS if present)
    map_pdb_x_to_5, map_x_to_5, map_pdb_3_to_5, map_3_to_5 = load_5charmap_maps("5CharMAP.csv")

    # 6) Chain assignment
    chain_map_multi = build_chain_map_from_warpdb(args.war_pdb_root)
    df = assign_chains_from_warpdb(df, PDB_COL, chain_map_multi)

    # 7) Resolve SMILES key (may be 5-letter) + attach SMILES
    ligX_list = []
    lig5_lookup_list = []
    warhead5_list = []
    smi_list = []
    ion_drop_flags = []
    ion_drop_reasons = []
    ion_parent_smiles = []
    ion_salt_stripped = []


    print("\n🔬 BEGIN RESOLUTION TRACE\n")

    for idx, row in df.iterrows():
        pdb = str(row[PDB_COL]).strip().lower()
        warhead = str(row["Warhead"]).strip().upper()

        smiles_key, warhead_5 = resolve_smiles_key_and_warhead5(
            pdb, warhead,
            map_pdb_x_to_5, map_x_to_5,
            map_pdb_3_to_5, map_3_to_5
        )
        smiles_key = str(smiles_key).strip().upper()
        warhead_5 = str(warhead_5).strip().upper()

        smi = smiles_map.get(smiles_key, "")

        # --- NEW: row-level ion-only screening (so downstream never sees ions) ---
        drop_reason = ""
        parent_smi = ""
        was_stripped = False

        if smi:
            _mol, parent_smi, was_stripped, drop_reason = clean_parent_from_smiles(
                smi,
                drop_pure_ions=True,
                drop_metal_containing=False
            )

        if drop_reason:
            # Mark row as "drop this warhead entry"
            ion_drop_flags.append(True)
            ion_drop_reasons.append(drop_reason)
            ion_parent_smiles.append(parent_smi)
            ion_salt_stripped.append(bool(was_stripped))

            print(f"🧹 ION DROP → Row {idx} | PDB={pdb} | Warhead={warhead} | key={smiles_key} | reason={drop_reason}", flush=True)

            # OPTIONAL: blank SMILES so it can’t be used by accident anywhere
            smi = ""
        else:
            ion_drop_flags.append(False)
            ion_drop_reasons.append("")
            ion_parent_smiles.append(parent_smi)
            ion_salt_stripped.append(bool(was_stripped))


        print("--------------------------------------------------")
        print(f"Row {idx}")
        print(f"  PDB: {pdb}")
        print(f"  Warhead (input): {warhead}")
        print(f"  Warhead_5 (if mapped): {warhead_5 if warhead_5 else '(blank)'}")
        print(f"  SMILES lookup key: {smiles_key}")
        print(f"  SMILES found? {'YES' if smi else 'NO'}")
        if not smi:
            print("  ❌ No SMILES for lookup key in smiles_map.")

        ligX_list.append(warhead)
        lig5_lookup_list.append(smiles_key)
        warhead5_list.append(warhead_5)
        smi_list.append(smi)

    print("\n🔬 END RESOLUTION TRACE\n")

    # 1) Attach ALL per-row outputs (MUST happen before filtering)
    df["LigandX_Source"]   = ligX_list
    df["Ligand5_Source"]   = lig5_lookup_list
    df["Ligand_Resolved"]  = lig5_lookup_list
    df["Warhead_5"]        = warhead5_list
    df["SMILES"]           = smi_list

    df["Ion_Dropped"]      = ion_drop_flags
    df["Ion_Drop_Reason"]  = ion_drop_reasons
    df["Parent_SMILES"]    = ion_parent_smiles
    df["Salt_Stripped"]    = ion_salt_stripped

    # 2) Write drop report BEFORE filtering (so you keep full context)
    dropped_rows = df[df["Ion_Dropped"]].copy()
    if not dropped_rows.empty:
        dropped_rows.to_csv("Dropped_Ion_Rows.csv", index=False)
        print(f"🧾 Wrote Dropped_Ion_Rows.csv ({len(dropped_rows)} rows)", flush=True)

        print("📊 Ion-drop reason counts:", flush=True)
        print(dropped_rows["Ion_Drop_Reason"].value_counts().to_string(), flush=True)

    # 3) Filter ions OUT before saving Resolved_SASA_Summary.csv
    df = df[~df["Ion_Dropped"]].copy()
    print(f"✅ Filtered SASA table: removed {len(dropped_rows)} ion-only rows; remaining {len(df)} rows", flush=True)

    # 4) Save filtered SASA summary
    df.to_csv("Resolved_SASA_Summary.csv", index=False)
    print("💾 Saved Resolved_SASA_Summary.csv")


    # 8) Ligand frequency index (by Ligand_Resolved, which now uses 5-letter when available)
    grouped = (
        df.groupby("Ligand_Resolved")[PDB_COL]
          .apply(lambda x: sorted(set(str(i).lower() for i in x)))
          .reset_index()
    )
    grouped["Count"] = grouped[PDB_COL].apply(len)
    grouped.rename(columns={PDB_COL: "PDB_List"}, inplace=True)
    grouped.insert(0, "Rank", range(1, len(grouped) + 1))
    grouped.to_csv("Ligand_PDB_Index.csv", index=False)
    print("💾 Saved Ligand_PDB_Index.csv")

    # 9) Parallel metadata
    # IMPORTANT: use Ligand5_Source (which is now the SMILES lookup key; 5-letter when mapped)
    ligands = (
        df["Ligand5_Source"]
        .dropna()
        .astype(str)
        .str.upper()
        .unique()
    )

    lig_smiles = (
        df[["Ligand5_Source", "SMILES"]]
        .dropna()
        .drop_duplicates()
        .groupby("Ligand5_Source")["SMILES"]
        .first()
        .to_dict()
    )

    job_list = [(lig, lig_smiles.get(lig, "")) for lig in ligands]

    print(f"\n🚀 Launching parallel engine with {cpu_count()} cores...\n")

    results = []
    with Pool(cpu_count()) as pool:
        for row in tqdm(
            pool.imap(process_single_ligand, job_list),
            total=len(job_list),
            desc="🧪 Computing descriptors"
        ):
            if row:
                results.append(row)

    out_df = pd.DataFrame(results)
    if out_df.empty:
        print("❌ No ligand metadata rows were generated.")
        out_df.to_csv("Ligand_Metadata.csv", index=False)
        out_df.to_csv("Ligand_Metadata_Failures.csv", index=False)
        return

    fails = out_df[out_df.columns.intersection(["Error"])].notna().any(axis=1)
    out_df[fails].to_csv("Ligand_Metadata_Failures.csv", index=False)
    out_df[~fails].to_csv("Ligand_Metadata.csv", index=False)


    header = [
        "Ligand", "Name", "Formula", "Type",
        "SMILES", "Canonical_SMILES", "InChI", "InChIKey",
        "MW", "LogP", "TPSA", "HBA", "HBD",
        "Rotatable_Bonds", "Ring_Count", "Aromatic_Rings",
        "Fraction_CSP3", "Heavy_Atom_Count", "Chiral_Atoms",
        "Formal_Charge", "QED", "BertzCT", "HallKierAlpha",
        "Kappa1", "Kappa2", "Kappa3", "NumSpiroAtoms",
        "NumBridgeheadAtoms", "NumAliphaticRings",
        "NumAromaticRings", "NumSaturatedRings",
        "NumHeteroAtoms", "MolMR", "SA_Score",
        "Lipinski_Pass", "Veber_Pass", "Egan_Pass",
        "Ghose_Pass", "Muegge_Pass",
        "PAINS_Hits", "Brenk_Hits"
    ]

    out_df = out_df[[c for c in header if c in out_df.columns]]
    out_df.to_csv("Ligand_Metadata.csv", index=False)

    success_count = int((~fails).sum())
    failure_count = int(fails.sum())
    print(f"📊 Ligand metadata rows: total={len(out_df)} success={success_count} failures={failure_count}")
    if failure_count:
        preview_cols = [c for c in ["Ligand", "SMILES", "Error"] if c in pd.DataFrame(results).columns]
        if preview_cols:
            print("⚠️ Metadata failures (first 10):")
            print(pd.DataFrame(results).loc[fails, preview_cols].head(10).to_string(index=False))

    print("\n🎉 COMPLETE → Ligand_Metadata.csv")
    print("===================================================================")


if __name__ == "__main__":
    main()

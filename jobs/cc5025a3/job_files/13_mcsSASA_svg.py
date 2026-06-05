#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
===============================================================================
MCS + SASA -> SVG (Per-PDB Instance, Deterministic Mapping)
===============================================================================

What this does:
1) Reads Ligand_3D_Atoms.csv (all 3D atoms per ligand occurrence)
2) Reads Warhead_SASA_atoms.csv (per-atom SASA, usually only exposed atoms)
3) Left-joins SASA onto 3D atoms by (Target,pdb_id,Warhead,Residue_ID,Chain,atom_id)
4) Builds a 3D RDKit mol for each occurrence (heavy atoms only)
5) Uses RDKit MCS to map SMILES(2D) atoms -> 3D atoms
6) Colors the SMILES depiction (SVG) using the mapped SASA values:
   low/medium/high -> green/yellow/red

Outputs:
- MCS_Output/Ligand_MCS_SASA_Map.csv
- MCS_Output/SVGS/*_plain.svg
- MCS_Output/SVGS/*_exposed.svg
===============================================================================
"""

import re
import argparse
from pathlib import Path
from functools import partial
from multiprocessing import Pool, cpu_count

import numpy as np
import pandas as pd

from rdkit import Chem
from rdkit.Chem import rdFMCS
from rdkit.Chem import rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D
from rdkit.Geometry import Point3D

# Optional: better bond perception
try:
    from rdkit.Chem import rdDetermineBonds
    HAVE_RD_DETERMINE_BONDS = True
except Exception:
    HAVE_RD_DETERMINE_BONDS = False

# -------------------------
# Coloring thresholds (A^2)
# -------------------------
def bucket_from_exposure(a2: float) -> str:
    if a2 < 15.0:
        return "low"
    if a2 <= 35.0:
        return "medium"
    return "high"

BUCKET_RGB = {
    "low":    (0.00, 0.78, 0.33),  # green
    "medium": (1.00, 0.84, 0.00),  # yellow
    "high":   (0.84, 0.00, 0.00),  # red
}

# -------------------------
# SMILES cleaning
# -------------------------
def normalize_smiles(x):
    if not isinstance(x, str):
        return x
    raw = x.strip()
    # CASE: ['smiles']
    m = re.match(r"^\[\s*['\"]([^'\"]+)['\"]\s*\]$", raw)
    if m:
        return m.group(1).strip()
    return raw.strip('"').strip("'")

def safe_int_loose(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    s = str(x)
    m = re.search(r"-?\d+", s)
    return int(m.group(0)) if m else None

def _extract_element(atom_name: str) -> str:
    """
    Try to infer element from a PDB-ish atom name.
    Falls back to first alphabetic element-like token.
    """
    if not isinstance(atom_name, str):
        return ""
    s = atom_name.strip()
    # Common PDB atom name patterns: " C1 ", " CA ", " CL1", " H12"
    m = re.search(r"([A-Z][a-z]?)", s)
    return m.group(1) if m else ""

def recolor_svg(svg: str) -> str:
    CYAN = "#00D9FF"
    svg = re.sub(r"stroke:#000000", f"stroke:{CYAN}", svg)
    svg = re.sub(r"stroke-width:2px", "stroke-width:2.4px", svg)
    return svg

def draw_svg(mol, highlights=None, add_atom_indices=False):
    drawer = rdMolDraw2D.MolDraw2DSVG(420, 420)
    opts = drawer.drawOptions()
    opts.backgroundColour = (0, 0, 0, 0)
    opts.bondLineWidth = 2

    if add_atom_indices:
        try:
            opts.addAtomIndices = True
        except Exception:
            pass

    if highlights:
        drawer.DrawMolecule(
            mol,
            highlightAtoms=list(highlights.keys()),
            highlightAtomColors=highlights
        )
    else:
        drawer.DrawMolecule(mol)

    drawer.FinishDrawing()
    return recolor_svg(drawer.GetDrawingText())

# -------------------------
# Build 3D molecule (heavy)
# -------------------------
def build_3d_mol_heavy(df_atoms: pd.DataFrame) -> tuple[Chem.Mol, pd.DataFrame]:
    """
    df_atoms must contain: atom_name, x,y,z, atom_id, Exposure_A2 (optional)
    Returns: (mol3d_heavy, df_heavy aligned to mol atom order)
    """
    df = df_atoms.copy().reset_index(drop=True)

    if "AtomSymbol" not in df.columns:
        df["AtomSymbol"] = df["atom_name"].apply(_extract_element)

    # heavy-only
    df = df[df["AtomSymbol"].str.upper() != "H"].copy().reset_index(drop=True)

    rw = Chem.RWMol()
    for sym in df["AtomSymbol"]:
        if not sym:
            sym = "C"
        rw.AddAtom(Chem.Atom(sym))

    conf = Chem.Conformer(len(df))
    for i, row in df.iterrows():
        conf.SetAtomPosition(i, Point3D(float(row["x"]), float(row["y"]), float(row["z"])))
    rw.AddConformer(conf)

    mol = rw.GetMol()

    # Better bonding if available
    if HAVE_RD_DETERMINE_BONDS:
        try:
            rdDetermineBonds.DetermineConnectivity(mol)
        except Exception:
            pass
        try:
            rdDetermineBonds.DetermineBonds(mol)
        except Exception:
            pass
    else:
        # Fallback: crude distance bonding
        coords = df[["x", "y", "z"]].values.astype(float)
        n = len(coords)
        rw2 = Chem.RWMol(mol)
        for i in range(n):
            for j in range(i + 1, n):
                d = np.linalg.norm(coords[i] - coords[j])
                if 0.9 < d < 1.9:
                    rw2.AddBond(i, j, Chem.BondType.SINGLE)
        mol = rw2.GetMol()

    try:
        Chem.SanitizeMol(mol)
    except Exception:
        pass

    return mol, df

# -------------------------
# MCS mapping + SVG coloring
# -------------------------
def mcs_map_and_render(key, idxs, atom3d_df, lig_to_smiles, svg_dir, debug=False, timeout=30):
    """
    key: (Warhead, Target, pdb_id, Residue_ID, Chain) normalized already
    idxs: row indices in atom3d_df for this occurrence
    Returns: (rows, errs)
    """
    warhead, target, pdb_id, resid, chain = key

    df = atom3d_df.loc[idxs].copy()

    # Need SMILES
    smi = lig_to_smiles.get(warhead)
    if not smi:
        return [], [[warhead, pdb_id, chain, resid, "Missing SMILES"]]

    mol2d = Chem.MolFromSmiles(smi)
    if mol2d is None:
        return [], [[warhead, pdb_id, chain, resid, f"Invalid SMILES: {smi}"]]
    rdDepictor.Compute2DCoords(mol2d)

    # 3D mol (heavy)
    try:
        mol3d, df3d = build_3d_mol_heavy(df)
    except Exception as e:
        return [], [[warhead, pdb_id, chain, resid, f"3D mol build failed: {e}"]]

    # MCS
    try:
        m = rdFMCS.FindMCS(
            [mol2d, mol3d],
            timeout=int(timeout),
            ringMatchesRingOnly=True,
            completeRingsOnly=True,
            matchValences=False,
            matchChiralTag=False,
        )
        patt = Chem.MolFromSmarts(m.smartsString)
    except Exception as e:
        return [], [[warhead, pdb_id, chain, resid, f"MCS error: {e}"]]

    if patt is None:
        return [], [[warhead, pdb_id, chain, resid, "SMARTS failed"]]

    match2d = mol2d.GetSubstructMatch(patt)
    match3d = mol3d.GetSubstructMatch(patt)
    if not match2d or not match3d:
        return [], [[warhead, pdb_id, chain, resid, "No MCS match"]]

    # Build mapping rows + highlights
    rows = []
    highlights = {}

    # multiple 3D -> same 2D (rare): keep max exposure
    exposure_by_2d = {}

    for a3, a2 in zip(match3d, match2d):
        row3 = df3d.iloc[int(a3)]
        exp = float(row3.get("Exposure_A2", 0.0) or 0.0)

        rows.append([
            warhead,
            target,
            pdb_id,
            resid,
            chain,
            int(a2),                  # AtomIndex in SMILES space (0-based)
            row3.get("AtomSymbol", ""),
            row3.get("atom_id", ""),
            row3.get("atom_name", ""),
            float(row3.get("x", np.nan)),
            float(row3.get("y", np.nan)),
            float(row3.get("z", np.nan)),
            exp
        ])

        # prepare coloring
        prev = exposure_by_2d.get(int(a2), -1.0)
        if exp > prev:
            exposure_by_2d[int(a2)] = exp

    for a2, exp in exposure_by_2d.items():
        if exp <= 0:
            continue
        bucket = bucket_from_exposure(exp)
        highlights[a2] = BUCKET_RGB[bucket]

    # Write SVGs (per instance)
    base = f"{pdb_id}_{chain}_{warhead}_{resid}"
    svg_dir.mkdir(parents=True, exist_ok=True)

    plain_path = svg_dir / f"{base}_plain.svg"
    exposed_path = svg_dir / f"{base}_exposed.svg"

    plain_path.write_text(draw_svg(mol2d, highlights=None), encoding="utf-8")
    exposed_path.write_text(draw_svg(mol2d, highlights=highlights), encoding="utf-8")

    if debug:
        (svg_dir / f"{base}_debug_idx.svg").write_text(
            draw_svg(mol2d, highlights=None, add_atom_indices=True),
            encoding="utf-8"
        )

    return rows, []


# -------------------------
# Main
# -------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atom3d", default="Ligand_3D_Atoms.csv")
    ap.add_argument("--sasa", default="Warhead_SASA_atoms.csv")
    ap.add_argument("--smiles", default="Target_Table/Ligand_SMILES_Map.csv")
    ap.add_argument("--outdir", default="MCS_Output")
    ap.add_argument("--svgdir", default=None)
    ap.add_argument("--nproc", type=int, default=max(1, cpu_count() - 1))
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(exist_ok=True)
    svg_dir = Path(args.svgdir) if args.svgdir else (outdir / "SVGS")

    # ---- load 3D atoms ----
    atom3d = pd.read_csv(args.atom3d)

    # Canonicalize expected cols (light-touch: just ensure these exist)
    needed = ["Warhead", "Target", "pdb_id", "Residue_ID", "Chain", "atom_id", "atom_name", "x", "y", "z"]
    missing = [c for c in needed if c not in atom3d.columns]
    if missing:
        raise SystemExit(f"❌ {args.atom3d} missing columns: {missing}\nFound: {list(atom3d.columns)}")

    # normalize key fields for robust joins/grouping
    atom3d["Warhead"] = atom3d["Warhead"].astype(str).str.strip().str.upper()
    atom3d["Target"]  = atom3d["Target"].astype(str).str.strip()
    atom3d["pdb_id"]  = atom3d["pdb_id"].astype(str).str.strip().str.lower()
    atom3d["Chain"]   = atom3d["Chain"].astype(str).str.strip().str.upper()
    atom3d["Residue_ID"] = atom3d["Residue_ID"].apply(safe_int_loose)
    atom3d["atom_id"] = pd.to_numeric(atom3d["atom_id"], errors="coerce").astype("Int64")

    # ---- load SASA atoms (often exposed-only) ----
    sasa = pd.read_csv(args.sasa)
    needed_s = ["Target", "pdb_id", "Warhead", "Residue_ID", "Chain", "atom_id", "Exposure_A2"]
    missing_s = [c for c in needed_s if c not in sasa.columns]
    if missing_s:
        raise SystemExit(f"❌ {args.sasa} missing columns: {missing_s}\nFound: {list(sasa.columns)}")

    sasa["Warhead"] = sasa["Warhead"].astype(str).str.strip().str.upper()
    sasa["Target"]  = sasa["Target"].astype(str).str.strip()
    sasa["pdb_id"]  = sasa["pdb_id"].astype(str).str.strip().str.lower()
    sasa["Chain"]   = sasa["Chain"].astype(str).str.strip().str.upper()
    sasa["Residue_ID"] = sasa["Residue_ID"].apply(safe_int_loose)
    sasa["atom_id"] = pd.to_numeric(sasa["atom_id"], errors="coerce").astype("Int64")
    sasa["Exposure_A2"] = pd.to_numeric(sasa["Exposure_A2"], errors="coerce").fillna(0.0)

    # aggregate in case duplicates exist: take max SASA per atom_id
    sasa_agg = (sasa
        .groupby(["Warhead","Target","pdb_id","Residue_ID","Chain","atom_id"], dropna=False)["Exposure_A2"]
        .max()
        .reset_index()
    )

    # join SASA onto all 3D atoms (missing => 0.0)
    atom3d = atom3d.merge(
        sasa_agg,
        on=["Warhead","Target","pdb_id","Residue_ID","Chain","atom_id"],
        how="left"
    )
    atom3d["Exposure_A2"] = atom3d["Exposure_A2"].fillna(0.0)

    # optional element column
    if "AtomSymbol" not in atom3d.columns:
        atom3d["AtomSymbol"] = atom3d["atom_name"].apply(_extract_element)

    # ---- load SMILES map ----
    smiles_raw = pd.read_csv(args.smiles)
    if "Warhead" not in smiles_raw.columns or "SMILES" not in smiles_raw.columns:
        raise SystemExit(f"❌ {args.smiles} must have columns Warhead, SMILES\nFound: {list(smiles_raw.columns)}")

    smiles_raw["Warhead"] = smiles_raw["Warhead"].astype(str).str.strip().str.upper()
    smiles_raw["SMILES"] = smiles_raw["SMILES"].apply(normalize_smiles)
    lig_to_smiles = dict(zip(smiles_raw["Warhead"], smiles_raw["SMILES"]))

    # ---- group occurrences ----
    gcols = ["Warhead","Target","pdb_id","Residue_ID","Chain"]
    groups = atom3d.groupby(gcols, dropna=False).groups  # key -> index list
    keys = list(groups.keys())

    print(f"🚀 MCS+SASA+SVG on {len(keys)} ligand occurrences using {args.nproc} cores")
    print(f"📁 SVG out: {svg_dir.resolve()}")
    print(f"📄 Map out: {(outdir/'Ligand_MCS_SASA_Map.csv').resolve()}\n")

    worker = partial(
        mcs_map_and_render,
        atom3d_df=atom3d,
        lig_to_smiles=lig_to_smiles,
        svg_dir=svg_dir,
        debug=args.debug,
        timeout=args.timeout
    )

    results = []
    failures = []

    with Pool(processes=args.nproc) as pool:
        for (rows, errs) in pool.starmap(worker, [(k, groups[k]) for k in keys]):
            if rows:
                results.extend(rows)
            if errs:
                failures.extend(errs)

    # ---- save mapping ----
    df_map = pd.DataFrame(
        results,
        columns=[
            "Ligand",
            "Target",
            "pdb_id",
            "Residue_ID",
            "Chain",
            "AtomIndex",       # 0-based SMILES atom index
            "AtomSymbol",
            "atom_id",
            "atom_name",
            "x","y","z",
            "Exposure_A2"
        ]
    )
    out_map = outdir / "Ligand_MCS_SASA_Map.csv"
    df_map.to_csv(out_map, index=False)

    out_fail = outdir / "Ligand_MCS_SASA_Failures.csv"
    pd.DataFrame(
        failures,
        columns=["Ligand","pdb_id","Chain","Residue_ID","Error"]
    ).to_csv(out_fail, index=False)

    print("====================================================")
    print("🎉 COMPLETED MCS + SASA → SVG")
    print(f"📦 Map      → {out_map}")
    print(f"🖼️  SVGs     → {svg_dir}")
    print(f"⚠️ Failures → {out_fail}  (n={len(failures)})")
    print("====================================================")

if __name__ == "__main__":
    main()

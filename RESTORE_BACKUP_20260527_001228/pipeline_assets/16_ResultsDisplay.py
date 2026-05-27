#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import re
from pathlib import Path
import pandas as pd

PDB_RE = re.compile(
    r"^([0-9a-z]{4})_([A-Za-z0-9])_([A-Za-z0-9]{2,12})\.pdb$",
    re.IGNORECASE
)

def find_col(df, options):
    for opt in options:
        for col in df.columns:
            if col.lower() == opt.lower():
                return col
    return None

def main():
    # Assume you run from jobs/<job>/TARGET_RESULTS
    root = Path(".").resolve()

    war_pdb_root = None
    for cand in [root / "WAR_PDB", root.parent / "WAR_PDB"]:
        if cand.exists():
            war_pdb_root = cand
            break
    if not war_pdb_root:
        print("❌ Missing WAR_PDB (checked ./WAR_PDB and ../WAR_PDB)")
        sys.exit(1)

    # Ligand metadata for SMILES
    meta_file = None
    for cand in [root / "Ligand_Metadata.csv", root.parent / "TARGET_RESULTS" / "Ligand_Metadata.csv"]:
        if cand.exists():
            meta_file = cand
            break

    meta = pd.read_csv(meta_file) if meta_file else pd.DataFrame()
    smiles_col = None
    if not meta.empty:
        if "Canonical_SMILES" in meta.columns:
            smiles_col = "Canonical_SMILES"
        elif "SMILES" in meta.columns:
            smiles_col = "SMILES"

    meta_map = {}
    if smiles_col and "Ligand" in meta.columns:
        meta_map = dict(
            zip(
                meta["Ligand"].astype(str).str.upper(),
                meta[smiles_col].astype(str)
            )
        )

    # Exposure summary (try several common filenames)
    exp_df = None
    for f in [
        root / "Resolved_SASA_Summary.csv",
        root / "WARHEAD_RESULTS.csv",
        root / "Ligand_Exposure_Summary.csv",
        root.parent / "Resolved_SASA_Summary.csv",
        root.parent / "WARHEAD_RESULTS.csv",
        root.parent / "Ligand_Exposure_Summary.csv",
    ]:
        if f.exists():
            exp_df = pd.read_csv(f)
            break

    exp_lookup = {}
    if exp_df is not None and not exp_df.empty:
        PDB_COL = find_col(exp_df, ["pdb_id", "pdb"])
        CHN_COL = find_col(exp_df, ["Chain", "chain"])
        WAR_COL = find_col(exp_df, ["Warhead", "Ligand", "ligand"])
        EXC_COL = find_col(exp_df, ["FracExposed", "%Exposed", "percent_exposed", "ExposedFrac"])

        if all([PDB_COL, CHN_COL, WAR_COL, EXC_COL]):
            tmp = exp_df[[PDB_COL, CHN_COL, WAR_COL, EXC_COL]].copy()
            tmp[PDB_COL] = tmp[PDB_COL].astype(str).str.lower()
            tmp[CHN_COL] = tmp[CHN_COL].astype(str).str.upper()
            tmp[WAR_COL] = tmp[WAR_COL].astype(str).str.upper()
            tmp[EXC_COL] = pd.to_numeric(tmp[EXC_COL], errors="coerce").fillna(0.0)

            # Max exposure per key
            tmp = (
                tmp.groupby([PDB_COL, CHN_COL, WAR_COL], as_index=False)[EXC_COL]
                   .max()
            )

            for _, r in tmp.iterrows():
                exp_lookup[(r[PDB_COL], r[CHN_COL], r[WAR_COL])] = float(r[EXC_COL])

    rows = []

    for target_dir in war_pdb_root.iterdir():
        if not target_dir.is_dir():
            continue
        target_name = target_dir.name  # e.g. HER2

        for pdb_file in target_dir.glob("*.pdb"):
            m = PDB_RE.match(pdb_file.name)
            if not m:
                continue

            pdb_id, chain, warhead = (
                m.group(1).lower(),
                m.group(2).upper(),
                m.group(3).upper()
            )

            rows.append({
                "Target": target_name,
                "pdb_id": pdb_id,
                "Chain": chain,
                "Warhead": warhead,
                "SMILES": meta_map.get(warhead, ""),
                "%Exposed": exp_lookup.get((pdb_id, chain, warhead), 0.0),
                "pdb_path": str(pdb_file.resolve()),
            })

    out = pd.DataFrame(rows)
    if out.empty:
        print("❌ No PDBs found in WAR_PDB/<TARGET>/")
        sys.exit(1)

    out["%Exposed"] = pd.to_numeric(out["%Exposed"], errors="coerce").fillna(0.0)
    out = out.sort_values(["%Exposed", "pdb_id", "Warhead", "Chain"], ascending=[False, True, True, True])

    out_file = root / "Results_Display.csv"
    out.to_csv(out_file, index=False)
    print(f"✅ Wrote {out_file} ({len(out)} entries)")

if __name__ == "__main__":
    main()

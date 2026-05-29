#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
16_ResultsDisplay.py

Builds Results_Display.csv for the gallery.

Important fix:
  - Do NOT blindly display every PDB in WAR_PDB.
  - Only display entries that resolve to an actual generated SDF from Step 11/12.
  - This keeps Results_Display.csv aligned with MCS_Output/MCS_SDF and prevents
    final SDF validation mismatch errors.
"""

import sys
import re
from pathlib import Path
import pandas as pd

PDB_RE = re.compile(
    r"^([0-9a-z]{4})_([A-Za-z0-9])_([A-Za-z0-9]{2,12})\.pdb$",
    re.IGNORECASE,
)


def find_col(df, options):
    for opt in options:
        for col in df.columns:
            if col.lower() == opt.lower():
                return col
    return None


def norm_pdb(value):
    return str(value).strip().lower()


def norm_chain(value):
    return str(value).strip().upper()


def norm_ligand(value):
    return str(value).strip().upper()


def norm_resid(value):
    s = str(value).strip()
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    return s


def discover_root():
    """
    Script usually runs from jobs/<job_id>.
    It may also be run from jobs/<job_id>/TARGET_RESULTS.
    """
    return Path(".").resolve()


def find_war_pdb_root(root: Path) -> Path:
    candidates = [
        root / "WAR_PDB",
        root / "TARGET_RESULTS" / "WAR_PDB",
        root.parent / "WAR_PDB",
        root.parent / "TARGET_RESULTS" / "WAR_PDB",
    ]

    for cand in candidates:
        if cand.exists() and cand.is_dir():
            return cand

    print("❌ Missing WAR_PDB. Checked:")
    for cand in candidates:
        print(f"  - {cand}")
    sys.exit(1)


def find_first_existing(root: Path, names):
    candidates = []
    for name in names:
        candidates.extend([
            root / name,
            root / "TARGET_RESULTS" / name,
            root.parent / name,
            root.parent / "TARGET_RESULTS" / name,
        ])

    for cand in candidates:
        if cand.exists() and cand.is_file():
            return cand

    return None


def find_sdf_roots(root: Path):
    candidates = [
        root / "MCS_Output" / "MCS_SDF",
        root / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF",
        root.parent / "MCS_Output" / "MCS_SDF",
        root.parent / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF",
    ]

    out = []
    seen = set()
    for cand in candidates:
        if cand.exists() and cand.is_dir():
            resolved = str(cand.resolve())
            if resolved not in seen:
                out.append(cand)
                seen.add(resolved)
    return out


def load_sdf_index(root: Path):
    """
    Returns:
      sdf_index_exact: set of (pdb, chain, ligand, resid)
      sdf_index_loose: set of (pdb, chain, ligand)
      sdf_path_lookup: dict exact key -> path
    """
    sdf_index_exact = set()
    sdf_index_loose = set()
    sdf_path_lookup = {}

    sdf_roots = find_sdf_roots(root)

    for sdf_root in sdf_roots:
        for sdf_file in sdf_root.glob("*.sdf"):
            stem = sdf_file.stem
            parts = stem.split("_")
            if len(parts) < 4:
                continue

            pdb_id = norm_pdb(parts[0])
            chain = norm_chain(parts[1])
            ligand = norm_ligand(parts[2])
            resid = norm_resid("_".join(parts[3:]))

            exact_key = (pdb_id, chain, ligand, resid)
            loose_key = (pdb_id, chain, ligand)

            sdf_index_exact.add(exact_key)
            sdf_index_loose.add(loose_key)
            sdf_path_lookup[exact_key] = sdf_file

    return sdf_index_exact, sdf_index_loose, sdf_path_lookup, sdf_roots


def load_residue_lookup(root: Path):
    """
    Builds residue lookup from the actual ligand atom / summary tables.

    This lets Results_Display attach the correct Residue_ID even though WAR_PDB
    filenames only contain pdb, chain, and ligand.
    """
    lookup = {}

    for filename in ["Ligand_3D_Atoms.csv", "Resolved_SASA_Summary.csv"]:
        path = find_first_existing(root, [filename])
        if path is None:
            continue

        try:
            df = pd.read_csv(path, dtype=str).fillna("")
        except Exception:
            continue

        pdb_col = find_col(df, ["pdb_id", "pdb"])
        chain_col = find_col(df, ["Chain", "chain"])
        lig_col = find_col(df, ["Warhead", "Ligand", "ligand"])
        resid_col = find_col(df, ["Residue_ID", "resid", "Residue"])

        if not all([pdb_col, chain_col, lig_col, resid_col]):
            continue

        for _, row in df.iterrows():
            pdb_id = norm_pdb(row[pdb_col])
            chain = norm_chain(row[chain_col])
            ligand = norm_ligand(row[lig_col])
            resid = norm_resid(row[resid_col])

            if pdb_id and chain and ligand and resid:
                lookup.setdefault((pdb_id, chain, ligand), resid)

    return lookup


def load_smiles_lookup(root: Path):
    """
    Prefer Ligand_Metadata.csv, but also fall back to Resolved_SASA_Summary.csv
    because Step 7 stores per-row SMILES there.
    """
    meta_map = {}

    meta_file = find_first_existing(root, ["Ligand_Metadata.csv"])
    if meta_file is not None:
        try:
            meta = pd.read_csv(meta_file, dtype=str).fillna("")
            smiles_col = None
            if "Canonical_SMILES" in meta.columns:
                smiles_col = "Canonical_SMILES"
            elif "SMILES" in meta.columns:
                smiles_col = "SMILES"

            if smiles_col and "Ligand" in meta.columns:
                for _, row in meta.iterrows():
                    ligand = norm_ligand(row["Ligand"])
                    smi = str(row[smiles_col]).strip()
                    if ligand and smi:
                        meta_map.setdefault(ligand, smi)
        except Exception as exc:
            print(f"⚠️ Could not read Ligand_Metadata.csv for SMILES lookup: {exc}")

    summary_file = find_first_existing(root, ["Resolved_SASA_Summary.csv"])
    if summary_file is not None:
        try:
            summ = pd.read_csv(summary_file, dtype=str).fillna("")
            lig_col = find_col(summ, ["Warhead", "Ligand", "Ligand_Resolved"])
            smiles_col = find_col(summ, ["SMILES", "smiles", "Canonical_SMILES"])

            if lig_col and smiles_col:
                for _, row in summ.iterrows():
                    ligand = norm_ligand(row[lig_col])
                    smi = str(row[smiles_col]).strip()
                    if ligand and smi:
                        meta_map.setdefault(ligand, smi)

            # Also map Ligand_Resolved / Ligand5_Source if present.
            for alt_col in ["Ligand_Resolved", "Ligand5_Source", "Warhead_5"]:
                if alt_col in summ.columns and smiles_col:
                    for _, row in summ.iterrows():
                        ligand = norm_ligand(row[alt_col])
                        smi = str(row[smiles_col]).strip()
                        if ligand and smi:
                            meta_map.setdefault(ligand, smi)

        except Exception as exc:
            print(f"⚠️ Could not read Resolved_SASA_Summary.csv for SMILES lookup: {exc}")

    return meta_map


def load_exposure_lookup(root: Path):
    exp_lookup = {}

    exp_file = find_first_existing(root, [
        "Resolved_SASA_Summary.csv",
        "WARHEAD_RESULTS.csv",
        "Ligand_Exposure_Summary.csv",
    ])

    if exp_file is None:
        return exp_lookup

    try:
        exp_df = pd.read_csv(exp_file, dtype=str).fillna("")
    except Exception as exc:
        print(f"⚠️ Could not read exposure summary: {exc}")
        return exp_lookup

    if exp_df.empty:
        return exp_lookup

    pdb_col = find_col(exp_df, ["pdb_id", "pdb"])
    chain_col = find_col(exp_df, ["Chain", "chain"])
    war_col = find_col(exp_df, ["Warhead", "Ligand", "ligand"])
    exc_col = find_col(exp_df, ["FracExposed", "%Exposed", "percent_exposed", "ExposedFrac"])

    if not all([pdb_col, chain_col, war_col, exc_col]):
        return exp_lookup

    tmp = exp_df[[pdb_col, chain_col, war_col, exc_col]].copy()
    tmp[pdb_col] = tmp[pdb_col].map(norm_pdb)
    tmp[chain_col] = tmp[chain_col].map(norm_chain)
    tmp[war_col] = tmp[war_col].map(norm_ligand)
    tmp[exc_col] = pd.to_numeric(tmp[exc_col], errors="coerce").fillna(0.0)

    tmp = (
        tmp.groupby([pdb_col, chain_col, war_col], as_index=False)[exc_col]
        .max()
    )

    for _, row in tmp.iterrows():
        exp_lookup[(row[pdb_col], row[chain_col], row[war_col])] = float(row[exc_col])

    return exp_lookup


def main():
    root = discover_root()
    war_pdb_root = find_war_pdb_root(root)

    sdf_index_exact, sdf_index_loose, sdf_path_lookup, sdf_roots = load_sdf_index(root)
    residue_lookup = load_residue_lookup(root)
    meta_map = load_smiles_lookup(root)
    exp_lookup = load_exposure_lookup(root)

    print(f"📁 ResultsDisplay root: {root}")
    print(f"📁 WAR_PDB root: {war_pdb_root}")
    print(f"📁 SDF roots: {[str(p) for p in sdf_roots]}")
    print(f"🧪 Indexed SDF exact keys: {len(sdf_index_exact)}")
    print(f"🧪 Indexed SDF loose keys: {len(sdf_index_loose)}")
    print(f"🧾 Residue lookup keys: {len(residue_lookup)}")

    rows = []
    skipped_no_sdf = []
    skipped_bad_name = []

    for target_dir in sorted(war_pdb_root.iterdir()):
        if not target_dir.is_dir():
            continue

        target_name = target_dir.name

        for pdb_file in sorted(target_dir.glob("*.pdb")):
            match = PDB_RE.match(pdb_file.name)
            if not match:
                skipped_bad_name.append(str(pdb_file))
                continue

            pdb_id = norm_pdb(match.group(1))
            chain = norm_chain(match.group(2))
            warhead = norm_ligand(match.group(3))

            resid = residue_lookup.get((pdb_id, chain, warhead), "")

            exact_key = (pdb_id, chain, warhead, resid) if resid else None
            loose_key = (pdb_id, chain, warhead)

            has_sdf = False
            sdf_path = ""

            if exact_key and exact_key in sdf_index_exact:
                has_sdf = True
                sdf_path = str(sdf_path_lookup.get(exact_key, ""))
            elif loose_key in sdf_index_loose:
                has_sdf = True
                # Get first matching exact SDF key so we can recover residue if missing.
                for key, path in sdf_path_lookup.items():
                    if key[:3] == loose_key:
                        resid = key[3]
                        sdf_path = str(path)
                        break

            if not has_sdf:
                skipped_no_sdf.append({
                    "Target": target_name,
                    "pdb_id": pdb_id,
                    "Chain": chain,
                    "Warhead": warhead,
                    "Residue_ID": resid,
                    "pdb_path": str(pdb_file.resolve()),
                    "reason": "No matching SDF in MCS_Output/MCS_SDF",
                })
                continue

            rows.append({
                "Target": target_name,
                "pdb_id": pdb_id,
                "Chain": chain,
                "Warhead": warhead,
                "Residue_ID": resid,
                "SMILES": meta_map.get(warhead, ""),
                "%Exposed": exp_lookup.get((pdb_id, chain, warhead), 0.0),
                "pdb_path": str(pdb_file.resolve()),
                "sdf_path": sdf_path,
                "sdf_available": True,
            })

    out = pd.DataFrame(rows)

    if out.empty:
        skipped_file = root / "Results_Display_Skipped_NoSDF.csv"
        pd.DataFrame(skipped_no_sdf).to_csv(skipped_file, index=False)
        print(f"❌ No SDF-backed PDBs found for display.")
        print(f"🧾 Wrote skipped report: {skipped_file}")
        sys.exit(1)

    out["%Exposed"] = pd.to_numeric(out["%Exposed"], errors="coerce").fillna(0.0)

    out = out.sort_values(
        ["%Exposed", "pdb_id", "Warhead", "Chain"],
        ascending=[False, True, True, True],
    )

    out_file = root / "Results_Display.csv"
    out.to_csv(out_file, index=False)

    if skipped_no_sdf:
        skipped_file = root / "Results_Display_Skipped_NoSDF.csv"
        pd.DataFrame(skipped_no_sdf).to_csv(skipped_file, index=False)
        print(f"⚠️ Skipped {len(skipped_no_sdf)} PDB rows without SDF support → {skipped_file}")

    if skipped_bad_name:
        bad_file = root / "Results_Display_Skipped_BadNames.csv"
        pd.DataFrame({"pdb_path": skipped_bad_name}).to_csv(bad_file, index=False)
        print(f"⚠️ Skipped {len(skipped_bad_name)} badly named PDB files → {bad_file}")

    print(f"✅ Wrote {out_file} ({len(out)} SDF-backed entries)")
    print(f"📊 Input WAR_PDB rows skipped due to missing SDF: {len(skipped_no_sdf)}")


if __name__ == "__main__":
    main()
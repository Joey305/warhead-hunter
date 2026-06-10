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
    s = str(value).strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip().strip("'").strip('"').strip()
        if inner:
            s = inner
    return s.upper()


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


def find_svg_roots(root: Path):
    candidates = [
        root / "MCS_Output" / "MCS_SVG",
        root / "TARGET_RESULTS" / "MCS_Output" / "MCS_SVG",
        root.parent / "MCS_Output" / "MCS_SVG",
        root.parent / "TARGET_RESULTS" / "MCS_Output" / "MCS_SVG",
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
      sdf_loose_lookup: dict loose key -> list[(exact_key, path)]
    """
    sdf_index_exact = set()
    sdf_index_loose = set()
    sdf_path_lookup = {}
    sdf_loose_lookup = {}

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
            sdf_loose_lookup.setdefault(loose_key, []).append((exact_key, sdf_file))

    return sdf_index_exact, sdf_index_loose, sdf_path_lookup, sdf_loose_lookup, sdf_roots


def load_svg_index(root: Path):
    svg_index = {
        "plain_exact": set(),
        "plain_loose": set(),
        "plain_lookup": {},
        "plain_loose_lookup": {},
        "exposed_exact": set(),
        "exposed_loose": set(),
        "exposed_lookup": {},
        "exposed_loose_lookup": {},
        "roots": find_svg_roots(root),
    }

    for svg_root in svg_index["roots"]:
        for svg_file in svg_root.glob("*.svg"):
            stem = svg_file.stem
            kind = None
            base = ""
            if stem.endswith("_plain"):
                kind = "plain"
                base = stem[:-6]
            elif stem.endswith("_exposed"):
                kind = "exposed"
                base = stem[:-8]
            else:
                continue

            parts = base.split("_")
            if len(parts) < 4:
                continue

            pdb_id = norm_pdb(parts[0])
            chain = norm_chain(parts[1])
            ligand = norm_ligand(parts[2])
            resid = norm_resid("_".join(parts[3:]))

            exact_key = (pdb_id, chain, ligand, resid)
            loose_key = (pdb_id, chain, ligand)

            svg_index[f"{kind}_exact"].add(exact_key)
            svg_index[f"{kind}_loose"].add(loose_key)
            svg_index[f"{kind}_lookup"][exact_key] = svg_file
            svg_index[f"{kind}_loose_lookup"].setdefault(loose_key, []).append((exact_key, svg_file))

    return svg_index


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

    sdf_index_exact, sdf_index_loose, sdf_path_lookup, sdf_loose_lookup, sdf_roots = load_sdf_index(root)
    svg_index = load_svg_index(root)
    residue_lookup = load_residue_lookup(root)
    meta_map = load_smiles_lookup(root)
    exp_lookup = load_exposure_lookup(root)

    print(f"📁 ResultsDisplay root: {root}")
    print(f"📁 WAR_PDB root: {war_pdb_root}")
    print(f"📁 SDF roots: {[str(p) for p in sdf_roots]}")
    print(f"📁 SVG roots: {[str(p) for p in svg_index['roots']]}")
    print(f"🧪 Indexed SDF exact keys: {len(sdf_index_exact)}")
    print(f"🧪 Indexed SDF loose keys: {len(sdf_index_loose)}")
    print(f"🧪 Indexed SVG exact keys: plain={len(svg_index['plain_exact'])} exposed={len(svg_index['exposed_exact'])}")
    print(f"🧾 Residue lookup keys: {len(residue_lookup)}")

    rows = []
    skipped_no_sdf = []
    skipped_bad_name = []
    artifact_audit = []

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
                matches = sdf_loose_lookup.get(loose_key, [])
                if len(matches) == 1:
                    has_sdf = True
                    resid = matches[0][0][3]
                    sdf_path = str(matches[0][1])

            if not has_sdf:
                skipped_no_sdf.append({
                    "Target": target_name,
                    "pdb_id": pdb_id,
                    "Chain": chain,
                    "Warhead": warhead,
                    "Residue_ID": resid,
                    "pdb_path": str(pdb_file.resolve()),
                    "reason": "No unique matching SDF in MCS_Output/MCS_SDF",
                })
                continue

            plain_svg_path = ""
            exposed_svg_path = ""
            exact_svg_key = (pdb_id, chain, warhead, resid) if resid else None
            loose_svg_key = (pdb_id, chain, warhead)

            if exact_svg_key and exact_svg_key in svg_index["plain_exact"]:
                plain_svg_path = str(svg_index["plain_lookup"].get(exact_svg_key, ""))
            elif loose_svg_key in svg_index["plain_loose"]:
                matches = svg_index["plain_loose_lookup"].get(loose_svg_key, [])
                if len(matches) == 1:
                    plain_svg_path = str(matches[0][1])

            if exact_svg_key and exact_svg_key in svg_index["exposed_exact"]:
                exposed_svg_path = str(svg_index["exposed_lookup"].get(exact_svg_key, ""))
            elif loose_svg_key in svg_index["exposed_loose"]:
                matches = svg_index["exposed_loose_lookup"].get(loose_svg_key, [])
                if len(matches) == 1:
                    exposed_svg_path = str(matches[0][1])

            smiles_value = meta_map.get(warhead, "")
            svg_plain_available = bool(plain_svg_path)
            svg_exposed_available = bool(exposed_svg_path)

            rows.append({
                "Target": target_name,
                "pdb_id": pdb_id,
                "Chain": chain,
                "Warhead": warhead,
                "Residue_ID": resid,
                "SMILES": smiles_value,
                "%Exposed": exp_lookup.get((pdb_id, chain, warhead), 0.0),
                "pdb_path": str(pdb_file.resolve()),
                "sdf_path": sdf_path,
                "sdf_available": True,
                "svg_plain_available": svg_plain_available,
                "svg_exposed_available": svg_exposed_available,
                "svg_available": bool(svg_plain_available or svg_exposed_available),
                "svg_plain_path": plain_svg_path,
                "svg_exposed_path": exposed_svg_path,
            })

            artifact_audit.append({
                "Target": target_name,
                "pdb_id": pdb_id,
                "Chain": chain,
                "Warhead": warhead,
                "Residue_ID": resid,
                "SMILES": smiles_value,
                "sdf_available": True,
                "sdf_path": sdf_path,
                "svg_plain_available": svg_plain_available,
                "svg_exposed_available": svg_exposed_available,
                "svg_available": bool(svg_plain_available or svg_exposed_available),
                "svg_plain_path": plain_svg_path,
                "svg_exposed_path": exposed_svg_path,
                "artifact_status": "ok" if (svg_plain_available or svg_exposed_available) else "missing_svg",
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
    audit_file = root / "Results_Display_Artifact_Audit.csv"
    pd.DataFrame(artifact_audit).to_csv(audit_file, index=False)

    if skipped_no_sdf:
        skipped_file = root / "Results_Display_Skipped_NoSDF.csv"
        pd.DataFrame(skipped_no_sdf).to_csv(skipped_file, index=False)
        print(f"⚠️ Skipped {len(skipped_no_sdf)} PDB rows without SDF support → {skipped_file}")

    if skipped_bad_name:
        bad_file = root / "Results_Display_Skipped_BadNames.csv"
        pd.DataFrame({"pdb_path": skipped_bad_name}).to_csv(bad_file, index=False)
        print(f"⚠️ Skipped {len(skipped_bad_name)} badly named PDB files → {bad_file}")

    missing_svg_count = sum(1 for row in artifact_audit if row["artifact_status"] == "missing_svg")
    if missing_svg_count:
        print(f"⚠️ Included {missing_svg_count} display rows without SVG support → {audit_file}")
    else:
        print(f"✅ All display rows have routable SVG support → {audit_file}")

    print(f"✅ Wrote {out_file} ({len(out)} SDF-backed entries)")
    print(f"📊 Input WAR_PDB rows skipped due to missing SDF: {len(skipped_no_sdf)}")


if __name__ == "__main__":
    main()

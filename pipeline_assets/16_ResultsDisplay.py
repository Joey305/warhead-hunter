#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
16_ResultsDisplay.py

Builds Results_Display.csv for the gallery from successful Step 11 occurrences,
not from WAR_PDB filenames alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from occurrence_keys import (
    asset_key,
    asset_key_from_occurrence,
    normalize_chain,
    normalize_ligand,
    normalize_pdb,
    normalize_residue_id,
    normalize_target,
    occurrence_key,
    parse_war_pdb_filename,
)

EXPOSED_THRESHOLD = 0.1


def find_col(df, options):
    for option in options:
        for col in df.columns:
            if col.lower() == option.lower():
                return col
    return None


def discover_root() -> Path:
    return Path(".").resolve()


def find_war_pdb_root(root: Path) -> Path:
    candidates = [
        root / "WAR_PDB",
        root / "TARGET_RESULTS" / "WAR_PDB",
        root.parent / "WAR_PDB",
        root.parent / "TARGET_RESULTS" / "WAR_PDB",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    print("❌ Missing WAR_PDB. Checked:")
    for candidate in candidates:
        print(f"  - {candidate}")
    sys.exit(1)


def find_first_existing(root: Path, names):
    for name in names:
        for candidate in (
            root / name,
            root / "TARGET_RESULTS" / name,
            root.parent / name,
            root.parent / "TARGET_RESULTS" / name,
        ):
            if candidate.exists() and candidate.is_file():
                return candidate
    return None


def find_existing_dirs(root: Path, names):
    found = []
    seen = set()
    for name in names:
        for candidate in (
            root / name,
            root / "TARGET_RESULTS" / name,
            root.parent / name,
            root.parent / "TARGET_RESULTS" / name,
        ):
            if candidate.exists() and candidate.is_dir():
                resolved = str(candidate.resolve())
                if resolved not in seen:
                    found.append(candidate)
                    seen.add(resolved)
    return found


def first_nonempty(series: pd.Series) -> str:
    for value in series:
        text = str(value or "").strip()
        if text and text.lower() != "nan":
            return text
    return ""


def load_step11_failure_keys(root: Path) -> set[tuple[str, str, str, str, str]]:
    failures = set()
    path = find_first_existing(root, ["MCS_Output/Ligand_MCS_Item_Failures.csv"])
    if path is None:
        return failures

    df = pd.read_csv(path, dtype=str).fillna("")
    for _, row in df.iterrows():
        failures.add(
            occurrence_key(
                row.get("Target"),
                row.get("pdb_id"),
                row.get("Chain"),
                row.get("Ligand") or row.get("Warhead"),
                row.get("Residue_ID"),
            )
        )
    return failures


def load_smiles_lookup(root: Path) -> dict[tuple[str, str], dict[str, str]]:
    out: dict[tuple[str, str], dict[str, str]] = {}
    for path in [
        find_first_existing(root, ["Resolved_SASA_Summary.csv"]),
        find_first_existing(root, ["Ligand_Metadata.csv"]),
    ]:
        if path is None:
            continue
        df = pd.read_csv(path, dtype=str).fillna("")
        pdb_col = find_col(df, ["pdb_id", "pdb"])
        lig_col = find_col(df, ["Warhead", "Ligand", "Ligand_Resolved", "Ligand5_Source"])
        smiles_col = find_col(df, ["SMILES", "Canonical_SMILES", "smiles"])
        smiles_id_col = find_col(df, ["Ligand_Resolved", "Ligand5_Source", "Warhead_5", "SMILES_ID"])
        if not all([pdb_col, lig_col, smiles_col]):
            continue

        for _, row in df.iterrows():
            key = (normalize_pdb(row[pdb_col]), normalize_ligand(row[lig_col]))
            current = out.setdefault(key, {"SMILES": "", "SMILES_ID": ""})
            if not current["SMILES"]:
                current["SMILES"] = str(row[smiles_col]).strip()
            if smiles_id_col and not current["SMILES_ID"]:
                current["SMILES_ID"] = normalize_ligand(row[smiles_id_col])
    return out


def load_occurrence_manifest(root: Path):
    source = find_first_existing(
        root,
        [
            "MCS_Output/Ligand_MCS_SASA_ALL_ATOMS.csv",
            "MCS_Output/Ligand_AllAtoms_Map.csv",
            "MCS_Output/Ligand_MCS_SASA.csv",
            "MCS_Output/Ligand_MCS_Map.csv",
        ],
    )
    if source is None:
        raise RuntimeError("Could not find a Step 11 occurrence table in MCS_Output/")

    df = pd.read_csv(source, dtype=str).fillna("")
    target_col = find_col(df, ["Target", "target"])
    pdb_col = find_col(df, ["pdb_id", "pdb"])
    chain_col = find_col(df, ["Chain", "chain"])
    lig_col = find_col(df, ["Ligand", "Warhead", "ligand"])
    resid_col = find_col(df, ["Residue_ID", "residue_id", "resid"])
    smiles_col = find_col(df, ["SMILES", "smiles"])
    smiles_id_col = find_col(df, ["SMILES_ID", "Ligand_Resolved", "Ligand5_Source"])

    if not all([target_col, pdb_col, chain_col, lig_col, resid_col]):
        raise RuntimeError(
            f"Occurrence table missing required columns: {source} cols={list(df.columns)}"
        )

    df["_Target"] = df[target_col].map(normalize_target)
    df["_pdb_id"] = df[pdb_col].map(normalize_pdb)
    df["_Chain"] = df[chain_col].map(normalize_chain)
    df["_Ligand"] = df[lig_col].map(normalize_ligand)
    df["_Residue_ID"] = df[resid_col].map(normalize_residue_id)

    failures = load_step11_failure_keys(root)
    if failures:
        failure_mask = df.apply(
            lambda row: occurrence_key(
                row["_Target"], row["_pdb_id"], row["_Chain"], row["_Ligand"], row["_Residue_ID"]
            ) in failures,
            axis=1,
        )
        df = df.loc[~failure_mask].copy()

    occurrence_cols = ["_Target", "_pdb_id", "_Chain", "_Ligand", "_Residue_ID"]
    grouped = df.groupby(occurrence_cols, dropna=False, sort=True)

    smiles_fallback = load_smiles_lookup(root)
    manifest = {}
    for key_vals, group in grouped:
        target, pdb_id, chain, ligand, residue_id = key_vals
        key = occurrence_key(target, pdb_id, chain, ligand, residue_id)

        record = {
            "Target": target,
            "pdb_id": pdb_id,
            "Chain": chain,
            "Warhead": ligand,
            "Residue_ID": residue_id,
            "SMILES": first_nonempty(group[smiles_col]) if smiles_col else "",
            "SMILES_ID": normalize_ligand(first_nonempty(group[smiles_id_col])) if smiles_id_col else "",
        }

        if not record["SMILES"]:
            fallback = smiles_fallback.get((pdb_id, ligand), {})
            record["SMILES"] = fallback.get("SMILES", "")
            record["SMILES_ID"] = record["SMILES_ID"] or fallback.get("SMILES_ID", "")

        if "Exposure_A2" in group.columns:
            exposure = pd.to_numeric(group["Exposure_A2"], errors="coerce").fillna(0.0)
            total_atoms = int(len(group))
            exposed_atoms = int((exposure > EXPOSED_THRESHOLD).sum())
            sasa_total = float(exposure.sum())
            percent_exposed = (exposed_atoms / total_atoms) if total_atoms > 0 else 0.0
            record.update({
                "Total_atoms": total_atoms,
                "Exposed_atoms": exposed_atoms,
                "SASA_in_complex_A2": round(sasa_total, 3),
                "%Exposed": round(percent_exposed, 3),
                "%Buried": round(1 - percent_exposed, 3),
            })

        manifest[key] = record

    if not manifest:
        raise RuntimeError(f"Occurrence manifest from {source} was empty after filtering Step 11 failures.")

    return manifest, source


def load_chain_rename_map(root: Path) -> dict[tuple[str, str], str]:
    path = find_first_existing(root, ["ChainRenameMAP.csv"])
    if path is None:
        return {}
    try:
        df = pd.read_csv(path, dtype=str).fillna("")
    except Exception:
        return {}
    pdb_col = find_col(df, ["pdb"])
    orig_col = find_col(df, ["orig_chain"])
    new_col = find_col(df, ["new_chain"])
    if not all([pdb_col, orig_col, new_col]):
        return {}
    mapping = {}
    for _, row in df.iterrows():
        pdb_id = normalize_pdb(row[pdb_col])
        orig_chain = str(row[orig_col]).strip()
        new_chain = normalize_chain(row[new_col])
        if pdb_id and orig_chain and new_chain:
            mapping[(pdb_id, orig_chain)] = new_chain
    return mapping


def infer_chain_from_file(pdb_file: Path) -> str:
    chains = []
    try:
        with open(pdb_file, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.startswith(("ATOM", "HETATM")) and len(line) > 21:
                    chain = normalize_chain(line[21].strip())
                    if chain:
                        chains.append(chain)
    except Exception:
        return ""
    unique = sorted(set(chains))
    if len(unique) == 1:
        return unique[0]
    return ""


def build_war_pdb_inventory(war_pdb_root: Path, chain_map: dict[tuple[str, str], str]):
    inventory = {}
    bad_names = []
    audit = []

    for target_dir in sorted(war_pdb_root.iterdir()):
        if not target_dir.is_dir():
            continue
        target = normalize_target(target_dir.name)
        for pdb_file in sorted(target_dir.glob("*.pdb")):
            parsed = parse_war_pdb_filename(pdb_file.name)
            if parsed is None:
                bad_names.append(str(pdb_file.resolve()))
                continue

            pdb_id, chain_token, ligand = parsed
            resolved_chain = chain_map.get((pdb_id, chain_token), "")
            chain_method = ""
            if resolved_chain:
                chain_method = "ChainRenameMAP"
            elif len(chain_token) == 1:
                resolved_chain = normalize_chain(chain_token)
                chain_method = "filename"
            else:
                resolved_chain = infer_chain_from_file(pdb_file)
                if resolved_chain:
                    chain_method = "file_contents"
                else:
                    resolved_chain = normalize_chain(chain_token)
                    chain_method = "filename_unmapped"

            key = (target, pdb_id, resolved_chain, ligand)
            record = {
                "Target": target,
                "pdb_id": pdb_id,
                "Warhead": ligand,
                "filename_chain_token": chain_token,
                "resolved_chain": resolved_chain,
                "chain_resolution": chain_method,
                "pdb_path": str(pdb_file.resolve()),
            }
            inventory.setdefault(key, []).append(record)
            audit.append(record)

    return inventory, bad_names, audit


def find_sdf_roots(root: Path):
    return find_existing_dirs(root, ["MCS_Output/MCS_SDF"])


def find_svg_roots(root: Path):
    return find_existing_dirs(root, ["MCS_Output/MCS_SVG"])


def load_sdf_index(root: Path):
    exact = set()
    loose = set()
    lookup = {}
    loose_lookup = {}
    roots = find_sdf_roots(root)
    for sdf_root in roots:
        for sdf_file in sdf_root.glob("*.sdf"):
            parts = sdf_file.stem.split("_")
            if len(parts) < 4:
                continue
            key = asset_key(parts[0], parts[1], parts[2], "_".join(parts[3:]))
            loose_key = key[:3]
            exact.add(key)
            loose.add(loose_key)
            lookup[key] = sdf_file
            loose_lookup.setdefault(loose_key, []).append((key, sdf_file))
    return exact, loose, lookup, loose_lookup, roots


def load_svg_index(root: Path):
    index = {
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
    for svg_root in index["roots"]:
        for svg_file in svg_root.glob("*.svg"):
            stem = svg_file.stem
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
            key = asset_key(parts[0], parts[1], parts[2], "_".join(parts[3:]))
            loose_key = key[:3]
            index[f"{kind}_exact"].add(key)
            index[f"{kind}_loose"].add(loose_key)
            index[f"{kind}_lookup"][key] = svg_file
            index[f"{kind}_loose_lookup"].setdefault(loose_key, []).append((key, svg_file))
    return index


def resolve_exact_or_unique_loose(exact_key, loose_key, exact_lookup, exact_index, loose_lookup):
    if exact_key in exact_index:
        return str(exact_lookup[exact_key]), "exact"
    matches = loose_lookup.get(loose_key, [])
    if len(matches) == 1:
        return str(matches[0][1]), "unique_loose"
    if len(matches) > 1:
        return "", "ambiguous_loose"
    return "", "missing"


def main():
    root = discover_root()
    war_pdb_root = find_war_pdb_root(root)

    manifest, manifest_source = load_occurrence_manifest(root)
    chain_map = load_chain_rename_map(root)
    inventory, bad_names, _inventory_audit = build_war_pdb_inventory(war_pdb_root, chain_map)
    sdf_exact, sdf_loose, sdf_lookup, sdf_loose_lookup, sdf_roots = load_sdf_index(root)
    svg_index = load_svg_index(root)

    print(f"📁 ResultsDisplay root: {root}")
    print(f"📁 WAR_PDB root: {war_pdb_root}")
    print(f"📁 Manifest source: {manifest_source}")
    print(f"📁 SDF roots: {[str(p) for p in sdf_roots]}")
    print(f"📁 SVG roots: {[str(p) for p in svg_index['roots']]}")
    print(f"🧪 Successful occurrences: {len(manifest)}")
    print(f"🧪 Indexed SDF exact keys: {len(sdf_exact)}")
    print(f"🧪 Indexed SDF loose keys: {len(sdf_loose)}")
    print(f"🧪 Indexed SVG exact keys: plain={len(svg_index['plain_exact'])} exposed={len(svg_index['exposed_exact'])}")

    display_rows = []
    audit_rows = []

    successful_occurrences = set(manifest.keys())
    display_occurrences = set()
    sdf_occurrences = set()
    plain_svg_occurrences = set()
    exposed_svg_occurrences = set()
    unroutable_pdb = set()

    for occ_key in sorted(successful_occurrences):
        target, pdb_id, chain, ligand, residue_id = occ_key
        info = dict(manifest[occ_key])
        artifact_key = asset_key_from_occurrence(occ_key)
        loose_key = artifact_key[:3]

        pdb_candidates = inventory.get((target, pdb_id, chain, ligand), [])
        if len(pdb_candidates) == 1:
            pdb_path = pdb_candidates[0]["pdb_path"]
            pdb_status = "ok"
            pdb_detail = pdb_candidates[0]["chain_resolution"]
        elif len(pdb_candidates) > 1:
            pdb_path = ""
            pdb_status = "ambiguous"
            pdb_detail = "multiple matching WAR_PDB files"
            unroutable_pdb.add(occ_key)
        else:
            pdb_path = ""
            pdb_status = "missing"
            pdb_detail = "no matching WAR_PDB file"
            unroutable_pdb.add(occ_key)

        sdf_path, sdf_status = resolve_exact_or_unique_loose(
            artifact_key, loose_key, sdf_lookup, sdf_exact, sdf_loose_lookup
        )
        plain_svg_path, plain_svg_status = resolve_exact_or_unique_loose(
            artifact_key,
            loose_key,
            svg_index["plain_lookup"],
            svg_index["plain_exact"],
            svg_index["plain_loose_lookup"],
        )
        exposed_svg_path, exposed_svg_status = resolve_exact_or_unique_loose(
            artifact_key,
            loose_key,
            svg_index["exposed_lookup"],
            svg_index["exposed_exact"],
            svg_index["exposed_loose_lookup"],
        )

        if sdf_path:
            sdf_occurrences.add(artifact_key)
        if plain_svg_path:
            plain_svg_occurrences.add(artifact_key)
        if exposed_svg_path:
            exposed_svg_occurrences.add(artifact_key)

        svg_status = "ok"
        if plain_svg_status != "exact" or exposed_svg_status != "exact":
            if plain_svg_path and exposed_svg_path:
                svg_status = "fallback"
            else:
                svg_status = "missing"

        audit_row = {
            **info,
            "PDB_File": pdb_path,
            "SDF_File": sdf_path,
            "SVG_Plain": plain_svg_path,
            "SVG_Exposed": exposed_svg_path,
            "PDB_Status": pdb_status,
            "PDB_Detail": pdb_detail,
            "SDF_Status": sdf_status,
            "SVG_Plain_Status": plain_svg_status,
            "SVG_Exposed_Status": exposed_svg_status,
            "SVG_Status": svg_status,
        }
        audit_rows.append(audit_row)

        if pdb_status == "ok" and sdf_path and plain_svg_path and exposed_svg_path:
            display_rows.append({
                **info,
                "pdb_path": pdb_path,
                "sdf_path": sdf_path,
                "sdf_available": True,
                "svg_plain_available": True,
                "svg_exposed_available": True,
                "svg_available": True,
                "svg_plain_path": plain_svg_path,
                "svg_exposed_path": exposed_svg_path,
            })
            display_occurrences.add(occ_key)

    missing_display = successful_occurrences - display_occurrences
    missing_sdf = {key for key in successful_occurrences if asset_key_from_occurrence(key) not in sdf_occurrences}
    missing_plain_svg = {key for key in successful_occurrences if asset_key_from_occurrence(key) not in plain_svg_occurrences}
    missing_exposed_svg = {key for key in successful_occurrences if asset_key_from_occurrence(key) not in exposed_svg_occurrences}

    display_df = pd.DataFrame(display_rows)
    if not display_df.empty:
        display_df = display_df.sort_values(
            ["%Exposed", "pdb_id", "Warhead", "Chain", "Residue_ID"],
            ascending=[False, True, True, True, True],
        )

    results_file = root / "Results_Display.csv"
    audit_file = root / "Results_Display_Artifact_Audit.csv"
    pd.DataFrame(audit_rows).to_csv(audit_file, index=False)
    display_df.to_csv(results_file, index=False)

    if bad_names:
        bad_file = root / "Results_Display_Skipped_BadNames.csv"
        pd.DataFrame({"pdb_path": bad_names}).to_csv(bad_file, index=False)
        print(f"⚠️ Skipped {len(bad_names)} badly named PDB files → {bad_file}")

    print(
        "🧪 Display coverage: "
        f"successful_occurrences={len(successful_occurrences)} "
        f"display_rows={len(display_rows)} "
        f"sdf_occurrences={len(sdf_occurrences)} "
        f"plain_svg_occurrences={len(plain_svg_occurrences)} "
        f"exposed_svg_occurrences={len(exposed_svg_occurrences)}"
    )
    print(
        "🧪 Missing coverage: "
        f"missing_display={len(missing_display)} "
        f"missing_sdf={len(missing_sdf)} "
        f"missing_plain_svg={len(missing_plain_svg)} "
        f"missing_exposed_svg={len(missing_exposed_svg)} "
        f"unroutable_pdb={len(unroutable_pdb)}"
    )

    if display_df.empty:
        print("❌ No occurrence-complete display rows could be generated.")
        print(f"🧾 Wrote audit: {audit_file}")
        sys.exit(1)

    if missing_display or missing_sdf or missing_plain_svg or missing_exposed_svg or unroutable_pdb:
        print("❌ Results display coverage is incomplete. See audit for details:")
        print(f"🧾 Audit file: {audit_file}")
        if missing_display:
            print(f"   missing_display sample: {sorted(missing_display)[:10]}")
        if missing_sdf:
            print(f"   missing_sdf sample: {sorted(missing_sdf)[:10]}")
        if missing_plain_svg:
            print(f"   missing_plain_svg sample: {sorted(missing_plain_svg)[:10]}")
        if missing_exposed_svg:
            print(f"   missing_exposed_svg sample: {sorted(missing_exposed_svg)[:10]}")
        if unroutable_pdb:
            print(f"   unroutable_pdb sample: {sorted(unroutable_pdb)[:10]}")
        sys.exit(1)

    print(f"✅ Wrote {results_file} ({len(display_df)} occurrence-backed entries)")
    print(f"✅ Wrote artifact audit → {audit_file}")


if __name__ == "__main__":
    main()

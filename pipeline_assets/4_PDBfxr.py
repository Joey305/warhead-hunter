#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
from collections import Counter

import pandas as pd


_OCC_BFAC_GLUE = re.compile(r"^(\d+\.\d{2})(\d+\.\d{2})$")


def format_pdb_hetatm(
    serial: int,
    atom_name: str,
    resname: str,
    chain: str,
    resseq: int,
    x: float,
    y: float,
    z: float,
    occ: float,
    bfac: float,
    element: str = "",
) -> str:
    """Write a clean fixed-width PDB HETATM line after remapping ligand codes."""
    atom_name = str(atom_name or "")
    if len(atom_name) < 4:
        if atom_name and atom_name[0].isdigit():
            atom_field = atom_name.rjust(4)
        else:
            atom_field = atom_name.rjust(4)
    else:
        atom_field = atom_name[:4]

    element = (str(element).strip()[:2]).rjust(2) if element else "  "

    return (
        f"HETATM{int(serial):5d} {atom_field}"
        f" {str(resname).strip().upper()[:3].ljust(3)} {str(chain).strip()[:1]}"
        f"{int(resseq):4d}    "
        f"{float(x):8.3f}{float(y):8.3f}{float(z):8.3f}"
        f"{float(occ):6.2f}{float(bfac):6.2f}          "
        f"{element}\n"
    )


def get_resname(line: str) -> str:
    return line[17:20].strip().upper()


def parse_hetatm_line(line: str) -> dict[str, object]:
    """
    Parse a HETATM line that may already be malformed by a 5-character residue
    name spilling past the 3-character PDB residue field.
    """
    tokens = line.split()
    if len(tokens) >= 11 and tokens[0] == "HETATM":
        serial = int(tokens[1])
        atom_name = tokens[2]
        resname = tokens[3]
        chain = tokens[4]
        resseq = int(tokens[5])
        x = float(tokens[6])
        y = float(tokens[7])
        z = float(tokens[8])
        occ_raw = tokens[9]
        bfac_raw = tokens[10]
        if _OCC_BFAC_GLUE.match(occ_raw):
            match = _OCC_BFAC_GLUE.match(occ_raw)
            assert match is not None
            occ = float(match.group(1))
            bfac = float(match.group(2))
        else:
            occ = float(occ_raw)
            bfac = float(bfac_raw)
        element = tokens[11] if len(tokens) >= 12 else line[76:78].strip()
        return {
            "serial": serial,
            "atom_name": atom_name,
            "resname": str(resname).strip().upper(),
            "chain": chain,
            "resseq": resseq,
            "x": x,
            "y": y,
            "z": z,
            "occ": occ,
            "bfac": bfac,
            "element": element,
        }

    return {
        "serial": int(line[6:11]),
        "atom_name": line[12:16].strip(),
        "resname": line[17:20].strip().upper(),
        "chain": line[21].strip(),
        "resseq": int(line[22:26]),
        "x": float(line[30:38]),
        "y": float(line[38:46]),
        "z": float(line[46:54]),
        "occ": float(line[54:60]),
        "bfac": float(line[60:66]),
        "element": line[76:78].strip(),
    }


def normalize_map_frame(df: pd.DataFrame) -> pd.DataFrame:
    lower_map = {c.lower(): c for c in df.columns}
    rename_map = {}
    for canonical in ("ligand5", "ligand3", "ligandX"):
        current = lower_map.get(canonical.lower())
        if current and current != canonical:
            rename_map[current] = canonical
    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def load_ligand_mappings(map_file: str) -> list[dict[str, str]]:
    if not os.path.exists(map_file):
        print("✅ 5CharMAP.csv not found. Nothing to rename.")
        return []

    try:
        df = pd.read_csv(map_file, dtype=str).fillna("")
    except pd.errors.EmptyDataError:
        print("✅ 5CharMAP.csv exists but is empty. Nothing to rename.")
        return []

    df = normalize_map_frame(df).drop_duplicates()
    required = {"ligand5", "ligandX"}
    missing = required - set(df.columns)
    if missing:
        print(f"⚠️ 5CharMAP.csv missing required columns: {sorted(missing)}")
        return []

    rows = []
    duplicate_keys = []
    seen = {}
    for row in df.to_dict("records"):
        lig5 = str(row.get("ligand5", "")).strip().upper()
        lig3 = str(row.get("ligand3", "")).strip().upper() or lig5[:3]
        ligx = str(row.get("ligandX", "")).strip().upper()
        if not lig5 or not ligx:
            continue
        if lig5 in seen and seen[lig5] != ligx:
            duplicate_keys.append((lig5, seen[lig5], ligx))
            continue
        seen[lig5] = ligx
        rows.append({
            "ligand5": lig5,
            "ligand3": lig3,
            "ligandX": ligx,
        })

    if duplicate_keys:
        for lig5, old, new in duplicate_keys:
            print(f"⚠️ Duplicate ligand5 mapping ignored: {lig5} -> {new} (keeping {old})")

    return sorted(rows, key=lambda row: (row["ligand5"], row["ligandX"]))


def count_hetatm_resnames(lines: list[str]) -> Counter:
    counts = Counter()
    for line in lines:
        if line.startswith("HETATM"):
            counts[get_resname(line)] += 1
    return counts


def should_rewrite_resname(resname: str, ligand5: str, ligand3: str, ligandx: str) -> bool:
    """Step 3 writes ligand-specific files, so the mapped ligand normally appears as ligand3."""
    if not resname:
        return False
    return resname in {ligand5, ligand3, ligandx}


def main():
    print("\n============================================")
    print("🔥 STEP 4: LIGAND5 → LIGANDX (TOKEN PARSE + STRICT REFORMAT)")
    print("============================================\n")

    if not os.path.exists("CIFdata.csv"):
        print("❌ CIFdata.csv not found.")
        return

    cifinfo = pd.read_csv("CIFdata.csv")
    pdb_root = str(cifinfo.iloc[0]["outdir"]).rstrip("/") + "_PDB"

    if not os.path.isdir(pdb_root):
        print(f"❌ PDB directory not found: {pdb_root}")
        return

    mapping_rows = load_ligand_mappings("5CharMAP.csv")
    if not mapping_rows:
        if os.path.exists("Skip4.txt"):
            print("🛑 Skip4.txt detected and no usable mappings found.")
        print("✅ Step 4 exiting safely.")
        return

    if os.path.exists("Skip4.txt"):
        print("⚠️ Skip4.txt detected, but 5CharMAP.csv has usable rows. Continuing with Step 4.")

    lig5_to_row = {row["ligand5"]: row for row in mapping_rows}

    print(f"🔬 Loaded {len(mapping_rows)} ligand5→ligandX mappings")
    for row in mapping_rows:
        print(
            f"   • ligand5={row['ligand5']} ligand3={row['ligand3']} ligandX={row['ligandX']}"
        )
    print()

    total_files_written = 0
    total_atoms_preserved = 0
    total_hetatm_inspected = 0

    for protein in sorted(os.listdir(pdb_root)):
        pdir = os.path.join(pdb_root, protein)
        if not os.path.isdir(pdir):
            continue

        for fname in sorted(os.listdir(pdir)):
            if not fname.endswith(".pdb"):
                continue

            stem = fname[:-4]
            parts = stem.split("_")
            if len(parts) < 3:
                continue

            file_lig = parts[-1].strip().upper()
            mapping = lig5_to_row.get(file_lig)
            if not mapping:
                continue

            lig5 = mapping["ligand5"]
            lig3 = mapping["ligand3"]
            ligx = mapping["ligandX"]
            source_path = os.path.join(pdir, fname)

            print(f"🔧 Considering {fname}")
            print(f"   source ligand5: {lig5}")
            print(f"   source ligand3: {lig3}")
            print(f"   target ligandX: {ligx}")

            with open(source_path, "r") as handle:
                lines = handle.readlines()

            out = []
            hetatm_inspected = 0
            hetatm_rewritten = 0

            for line in lines:
                if not line.startswith("HETATM"):
                    out.append(line)
                    continue

                hetatm_inspected += 1
                try:
                    parsed = parse_hetatm_line(line)
                except Exception as exc:
                    print(f"⚠️ Could not parse HETATM in {fname}: {exc}")
                    continue

                resname = str(parsed["resname"]).strip().upper()
                if should_rewrite_resname(resname, lig5, lig3, ligx):
                    out.append(
                        format_pdb_hetatm(
                            serial=int(parsed["serial"]),
                            atom_name=str(parsed["atom_name"]),
                            resname=ligx,
                            chain=str(parsed["chain"]),
                            resseq=int(parsed["resseq"]),
                            x=float(parsed["x"]),
                            y=float(parsed["y"]),
                            z=float(parsed["z"]),
                            occ=float(parsed["occ"]),
                            bfac=float(parsed["bfac"]),
                            element=str(parsed["element"]),
                        )
                    )
                    hetatm_rewritten += 1
                # Intentionally drop unrelated HETATM rows; Step 5 will later
                # retain only the target ligand for each ligand-specific file.

            parts[-1] = ligx
            output_name = "_".join(parts) + ".pdb"
            output_path = os.path.join(pdir, output_name)

            with open(output_path, "w") as handle:
                handle.writelines(out)

            if output_path != source_path and os.path.exists(source_path):
                os.remove(source_path)

            resname_counts = count_hetatm_resnames(out)
            ligx_count = resname_counts.get(ligx, 0)

            print(f"   output filename: {output_name}")
            print(f"   HETATM rows inspected: {hetatm_inspected}")
            print(f"   HETATM rows rewritten/appended: {hetatm_rewritten}")
            if hetatm_rewritten == 0 or ligx_count == 0:
                print(
                    f"⚠️ Warning: zero ligand atoms rewritten for {fname} "
                    f"({lig5} -> {ligx})"
                )
            print(f"   output HETATM {ligx} count: {ligx_count}\n")

            total_files_written += 1
            total_atoms_preserved += hetatm_rewritten
            total_hetatm_inspected += hetatm_inspected

    print("\n============================================")
    print(f"🎉 DONE — {total_files_written} mapped files written")
    print(f"✅ Total HETATM rows inspected: {total_hetatm_inspected}")
    print(f"✅ Total ligand atoms preserved: {total_atoms_preserved}")
    print("============================================\n")


if __name__ == "__main__":
    main()

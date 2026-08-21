#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
from collections import Counter

import pandas as pd


_OCC_BFAC_GLUE = re.compile(r"^(\d+\.\d{2})(\d+\.\d{2})$")


def _safe_float(value: str) -> float:
    return float(str(value).strip())


def _safe_int(value: str) -> int:
    text = str(value).strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    as_float = float(text)
    if as_float.is_integer():
        return int(as_float)
    raise ValueError(f"Expected integer token, got {value!r}")


def _split_occ_bfac(occ_token: str, bfac_token: str | None = None) -> tuple[float, float]:
    glued = _OCC_BFAC_GLUE.match(str(occ_token).strip())
    if glued:
        return float(glued.group(1)), float(glued.group(2))
    if bfac_token is None:
        raise ValueError(f"Could not split occupancy/B-factor from token {occ_token!r}")
    return _safe_float(occ_token), _safe_float(bfac_token)


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


def _parse_hetatm_tokens(line: str) -> dict[str, object]:
    tokens = line.split()
    if len(tokens) < 10 or tokens[0] != "HETATM":
        raise ValueError("token parser requires a HETATM line with at least 10 tokens")

    serial = _safe_int(tokens[1])
    atom_name = tokens[2]

    element = line[76:78].strip()
    tail_end = len(tokens)
    if tail_end >= 1 and re.fullmatch(r"[A-Za-z]{1,2}", tokens[-1]):
        element = tokens[-1]
        tail_end -= 1

    numeric_tail = tokens[:tail_end]
    if len(numeric_tail) < 9:
        raise ValueError(f"too few tokens for numeric tail: {tokens}")

    bfac_token = numeric_tail[-1]
    occ_token = numeric_tail[-2]
    z_token = numeric_tail[-3]
    y_token = numeric_tail[-4]
    x_token = numeric_tail[-5]
    resseq_token = numeric_tail[-6]
    head = numeric_tail[3:-6]
    if not head:
        raise ValueError(f"missing residue tokens in line: {line.rstrip()}")

    if len(head) == 1:
        token = str(head[0]).strip()
        chain = line[21].strip()
        if len(token) >= 6 and token[-1].isalpha():
            resname = token[:-1]
            chain = token[-1]
        else:
            resname = token
    else:
        resname = "".join(head[:-1])
        chain = head[-1]

    occ, bfac = _split_occ_bfac(occ_token, bfac_token)
    return {
        "serial": serial,
        "atom_name": atom_name,
        "resname": str(resname).strip().upper(),
        "chain": str(chain).strip(),
        "resseq": _safe_int(resseq_token),
        "x": _safe_float(x_token),
        "y": _safe_float(y_token),
        "z": _safe_float(z_token),
        "occ": occ,
        "bfac": bfac,
        "element": element,
    }


def _parse_standard_fixed_width(line: str) -> dict[str, object]:
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


def _parse_five_char_fixed_width(line: str) -> dict[str, object]:
    occ, bfac = _split_occ_bfac(line[57:63], line[63:69])
    return {
        "serial": int(line[6:11]),
        "atom_name": line[12:16].strip(),
        "resname": line[17:22].strip().upper(),
        "chain": line[22].strip(),
        "resseq": int(line[23:27]),
        "x": float(line[31:39]),
        "y": float(line[39:47]),
        "z": float(line[47:55]),
        "occ": occ,
        "bfac": bfac,
        "element": line[78:80].strip() if len(line) >= 80 else line[76:78].strip(),
    }


def parse_hetatm_line(line: str) -> dict[str, object]:
    """
    Parse a HETATM line that may be standard-width or shifted by a 5-character
    ligand code inserted into the residue-name field.
    """
    errors = []
    for parser in (_parse_hetatm_tokens, _parse_standard_fixed_width, _parse_five_char_fixed_width):
        try:
            return parser(line)
        except Exception as exc:
            errors.append(f"{parser.__name__}: {exc}")
    raise ValueError("; ".join(errors))


def raw_line_mentions_target(line: str, *codes: str) -> bool:
    text = str(line).upper()
    return any(code and code.upper() in text for code in codes)


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
    rewrite_audit_rows = []
    hard_failures = []

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
            hetatm_parsed = 0
            hetatm_rewritten = 0
            hetatm_failed = 0
            ligand_hetatm_inspected = 0
            ligand_hetatm_parsed = 0
            ligand_hetatm_failed = 0

            for line in lines:
                if not line.startswith("HETATM"):
                    out.append(line)
                    continue

                hetatm_inspected += 1
                line_mentions_target = raw_line_mentions_target(line, lig5, lig3, ligx)
                if line_mentions_target:
                    ligand_hetatm_inspected += 1
                try:
                    parsed = parse_hetatm_line(line)
                    hetatm_parsed += 1
                except Exception as exc:
                    print(f"⚠️ Could not parse HETATM in {fname}: {exc}")
                    hetatm_failed += 1
                    if line_mentions_target:
                        ligand_hetatm_failed += 1
                    continue

                resname = str(parsed["resname"]).strip().upper()
                if should_rewrite_resname(resname, lig5, lig3, ligx):
                    ligand_hetatm_parsed += 1
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
            print(f"   HETATM rows parsed: {hetatm_parsed}")
            print(f"   HETATM rows rewritten/appended: {hetatm_rewritten}")
            print(f"   HETATM rows failed: {hetatm_failed}")
            if hetatm_rewritten == 0 or ligx_count == 0:
                print(
                    f"⚠️ Warning: zero ligand atoms rewritten for {fname} "
                    f"({lig5} -> {ligx})"
                )
            print(f"   output HETATM {ligx} count: {ligx_count}\n")

            audit_row = {
                "protein": protein,
                "source_file": fname,
                "output_file": output_name,
                "source_ligand5": lig5,
                "source_ligand3": lig3,
                "output_ligandX": ligx,
                "hetatm_inspected": hetatm_inspected,
                "hetatm_parsed": hetatm_parsed,
                "hetatm_rewritten": hetatm_rewritten,
                "hetatm_failed": hetatm_failed,
                "ligand_hetatm_inspected": ligand_hetatm_inspected,
                "ligand_hetatm_parsed": ligand_hetatm_parsed,
                "ligand_hetatm_rewritten": hetatm_rewritten,
                "ligand_hetatm_failed": ligand_hetatm_failed,
                "output_ligand_atom_count": ligx_count,
            }
            rewrite_audit_rows.append(audit_row)

            if ligand_hetatm_inspected > 0 and hetatm_rewritten == 0:
                hard_failures.append({
                    **audit_row,
                    "error": (
                        f"Ligand rewrite failure: PDB={parts[0]} "
                        f"source_ligand={lig5} output_ligand={ligx} "
                        f"source_HETATM={ligand_hetatm_inspected} "
                        f"parsed={ligand_hetatm_parsed} rewritten={hetatm_rewritten} "
                        f"parse_failures={ligand_hetatm_failed}"
                    ),
                })

            total_files_written += 1
            total_atoms_preserved += hetatm_rewritten
            total_hetatm_inspected += hetatm_inspected

    pd.DataFrame(rewrite_audit_rows).to_csv("Step4_Ligand_Rewrite_Audit.csv", index=False)
    if hard_failures:
        pd.DataFrame(hard_failures).to_csv("Step4_Ligand_Rewrite_Failures.csv", index=False)
        raise RuntimeError(
            "Step 4 ligand-preservation invariant failed. "
            f"Failing files={len(hard_failures)} "
            f"first_failure={hard_failures[0]['error']}"
        )

    print("\n============================================")
    print(f"🎉 DONE — {total_files_written} mapped files written")
    print(f"✅ Total HETATM rows inspected: {total_hetatm_inspected}")
    print(f"✅ Total ligand atoms preserved: {total_atoms_preserved}")
    print("============================================\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import pandas as pd
import os
import sys


# ----------------------------
# Robust float parsing helpers
# ----------------------------

_OCC_BFAC_GLUE = re.compile(r"^(\d+\.\d{2})(\d+\.\d{2})$")  # e.g. "1.00100.91" -> "1.00", "100.91"

def split_occ_bfac_if_glued(s: str):
    """
    If occupancy and b-factor got glued (e.g. '1.00100.91'), split them.
    Returns (occ, bfac) as floats or raises ValueError.
    """
    m = _OCC_BFAC_GLUE.match(s)
    if not m:
        raise ValueError(f"Not a glued occ/bfac token: {s}")
    return float(m.group(1)), float(m.group(2))


def format_pdb_hetatm(serial, atom_name, resname, chain, resseq, x, y, z, occ, bfac, element=""):
    """
    Strict fixed-width PDB line (HETATM).
    Atom name alignment: if atom_name is 3 chars and starts with letter, pad left.
    """
    atom_name = str(atom_name)
    if len(atom_name) < 4:
        # PDB convention: right-justify if starts with digit, else left-pad to 4
        if atom_name and atom_name[0].isdigit():
            atom_field = atom_name.rjust(4)
        else:
            atom_field = atom_name.ljust(4) if len(atom_name) == 4 else atom_name.rjust(4)
    else:
        atom_field = atom_name[:4]

    element = (str(element).strip()[:2]).rjust(2) if element else "  "

    return (
        f"HETATM{int(serial):5d} {atom_field}"
        f" {str(resname).ljust(3)[:3]} {str(chain)[:1]}"
        f"{int(resseq):4d}    "
        f"{float(x):8.3f}{float(y):8.3f}{float(z):8.3f}"
        f"{float(occ):6.2f}{float(bfac):6.2f}          "
        f"{element}\n"
    )



def main():

    if os.path.exists("Skip4.txt"):
        print("🛑 Skip4.txt detected — no ligand5→ligandX renaming required.")
        print("✅ Step 4 exiting safely.")
        return

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

    map_file = "5CharMAP.csv"
    if not os.path.exists(map_file):
        print("✅ 5CharMAP.csv not found. Nothing to rename.")
        return

    try:
        df = pd.read_csv(map_file).drop_duplicates()
    except pd.errors.EmptyDataError:
        print("✅ 5CharMAP.csv exists but is empty. Nothing to rename.")
        return

    if df.empty:
        print("✅ 5CharMAP.csv has no rows. Nothing to rename.")
        return

    # Expect columns: protein,pdb,ligand5,ligand3,ligandX
    # We only need ligand5->ligandX (your requirement)
    lig5_to_X = {}
    for _, r in df.iterrows():
        if pd.isna(r.get("ligand5")) or pd.isna(r.get("ligandX")):
            continue
        lig5_to_X[str(r["ligand5"]).strip()] = str(r["ligandX"]).strip()

    print(f"🔬 Loaded {len(lig5_to_X)} ligand5→ligandX mappings\n")

    total_files = 0
    total_atoms = 0

    for protein in os.listdir(pdb_root):
        pdir = os.path.join(pdb_root, protein)
        if not os.path.isdir(pdir):
            continue

        for fname in os.listdir(pdir):
            if not fname.endswith(".pdb"):
                continue

            full = os.path.join(pdir, fname)
            stem = fname[:-4]
            parts = stem.split("_")
            if len(parts) < 3:
                continue

            file_lig = parts[-1]  # could be ligand5 (A1IFO) or already ligandX (A03) etc.

            # Only act if filename ligand is ligand5 that we can map
            if file_lig not in lig5_to_X:
                continue

            lig5 = file_lig
            ligX = lig5_to_X[lig5]

            print(f"🔧 Fixing {fname} | {lig5} → {ligX}")

            with open(full, "r") as f:
                lines = f.readlines()

            out = []
            renamed_atoms_this_file = 0

            for line in lines:
                if not line.startswith("HETATM"):
                    out.append(line)
                    continue

                # Token parse
                try:
                    serial   = int(line[6:11])
                    atom_name= line[12:16].strip()
                    resname  = line[17:20].strip()
                    chain    = line[21].strip()
                    resseq   = int(line[22:26])
                    x        = float(line[30:38])
                    y        = float(line[38:46])
                    z        = float(line[46:54])
                    occ      = float(line[54:60])
                    bfac     = float(line[60:66])
                    element  = line[76:78].strip()
                except Exception:
                    out.append(line)
                    continue


                
            # ✅ Rename the filename ligand5 → ligandX
            parts[-1] = ligX
            newname = "_".join(parts) + ".pdb"
            newfull = os.path.join(pdir, newname)

            with open(newfull, "w") as f:
                f.writelines(out)

            if newfull != full:
                os.remove(full)

            print(f"   → {newname} | atoms updated: {renamed_atoms_this_file}\n")
            total_files += 1

    print("\n============================================")
    print(f"🎉 DONE — {total_files} files fixed")
    print(f"✅ Total ligand atoms renamed: {total_atoms}")
    print("============================================\n")


if __name__ == "__main__":
    main()



# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-

# import os
# import pandas as pd


# def replace_resname_column(line: str, newname: str) -> str:
#     """
#     Replace residue name (columns 17–20 in PDB format).
#     """
#     return line[:17] + newname.ljust(3)[:3] + line[20:]


# def get_resname(line: str) -> str:
#     return line[17:20].strip()


# def main():
#     print("\n============================================")
#     print("🔥 STEP 4: SMART LIGAND RENAMER")
#     print("============================================\n")

#     if not os.path.exists("CIFdata.csv"):
#         print("❌ CIFdata.csv not found. Skipping.")
#         return

#     cifinfo = pd.read_csv("CIFdata.csv")
#     pdb_root = cifinfo.iloc[0]["outdir"].rstrip("/") + "_PDB"

#     if not os.path.isdir(pdb_root):
#         print(f"⚠️ PDB directory not found: {pdb_root}. Skipping.")
#         return

#     map_file = "5CharMAP.csv"

#     if not os.path.exists(map_file):
#         print("✅ 5CharMAP.csv not found. Nothing to rename.")
#         return

#     try:
#         df = pd.read_csv(map_file)
#     except pd.errors.EmptyDataError:
#         print("✅ 5CharMAP.csv empty. Nothing to rename.")
#         return

#     df = df.drop_duplicates()

#     if df.empty:
#         print("✅ 5CharMAP.csv has no rows. Nothing to rename.")
#         return

#     # ---------------------------------------------------------
#     # Detect fallback mode (ligand5 == ligandX for ALL rows)
#     # ---------------------------------------------------------
#     fallback_mode = all(
#         str(r["ligand5"]) == str(r["ligandX"])
#         for _, r in df.iterrows()
#     )

#     if fallback_mode:
#         print("⚠️ Fallback 3-letter mode detected.")
#         print("👉 No 5→X renaming required. Skipping Step 4.\n")
#         return

#     # ---------------------------------------------------------
#     # Normal 5-letter → ligandX renaming mode
#     # ---------------------------------------------------------
#     lig5_to_X = {
#         str(r["ligand5"]).strip(): str(r["ligandX"]).strip()
#         for _, r in df.iterrows()
#         if pd.notna(r["ligand5"]) and pd.notna(r["ligandX"])
#     }

#     count = 0

#     for protein in os.listdir(pdb_root):
#         pdir = os.path.join(pdb_root, protein)
#         if not os.path.isdir(pdir):
#             continue

#         for fname in os.listdir(pdir):
#             if not fname.endswith(".pdb"):
#                 continue

#             full = os.path.join(pdir, fname)
#             parts = fname.replace(".pdb", "").split("_")

#             if len(parts) < 3:
#                 continue

#             lig5 = parts[-1]

#             if lig5 not in lig5_to_X:
#                 continue

#             ligX = lig5_to_X[lig5]

#             # If identical, skip
#             if lig5 == ligX:
#                 continue

#             print(f"🔧 Fixing {fname} | {lig5} → {ligX}")

#             with open(full, "r") as f:
#                 lines = f.readlines()

#             out = []

#             for line in lines:
#                 if line.startswith("HETATM"):
#                     res = get_resname(line)
#                     if res == lig5:
#                         line = replace_resname_column(line, ligX)
#                         out.append(line)
#                     else:
#                         continue
#                 else:
#                     out.append(line)

#             newname = fname.replace(lig5, ligX)
#             newfull = os.path.join(pdir, newname)

#             with open(newfull, "w") as f:
#                 f.writelines(out)

#             os.remove(full)
#             count += 1

#     print(f"\n🎉 DONE — Renamed {count} files.\n")


# if __name__ == "__main__":
#     main()

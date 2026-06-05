#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import sys
from rdkit import Chem

try:
    from rdkit.Chem import rdDetermineBonds
except Exception as e:
    print("❌ rdDetermineBonds import failed:", e)
    print("   Use OpenBabel fallback: obabel -ipdb in.pdb -osdf -O out.sdf")
    sys.exit(2)


def pdb_to_sdf(pdb_path: Path, sdf_path: Path) -> tuple[bool, str]:
    # Try file-based parse
    mol = Chem.MolFromPDBFile(str(pdb_path), removeHs=False, sanitize=False)
    if mol is None:
        # Try block-based parse
        txt = pdb_path.read_text(errors="ignore")
        mol = Chem.MolFromPDBBlock(txt, removeHs=False, sanitize=False)

    if mol is None:
        return False, "RDKit could not parse PDB (MolFromPDBFile/MolFromPDBBlock returned None)"

    if mol.GetNumConformers() == 0:
        return False, "No conformer/coords found in parsed molecule"

    # Infer bonds (keeps coords)
    bond_msg = ""
    try:
        rdDetermineBonds.DetermineBonds(mol)
        bond_msg = "DetermineBonds OK"
    except Exception as e:
        # Connectivity-only fallback
        try:
            rdDetermineBonds.DetermineConnectivity(mol)
            bond_msg = f"DetermineBonds failed -> DetermineConnectivity OK ({e})"
        except Exception as e2:
            bond_msg = f"DetermineBonds+DetermineConnectivity failed ({e} | {e2})"

    # Optional sanitize (don’t fail if weird valence/metal)
    try:
        Chem.SanitizeMol(mol)
    except Exception as e:
        # keep unsanitized
        bond_msg += f"; Sanitize failed (kept unsanitized): {e}"

    mol.SetProp("_Name", pdb_path.stem)

    w = Chem.SDWriter(str(sdf_path))
    w.write(mol)
    w.close()

    return True, f"wrote atoms={mol.GetNumAtoms()} confs={mol.GetNumConformers()} | {bond_msg}"


def main():
    # Usage:
    #   python 15_PDB2SDF.py jobs/<jobid>/TARGET_RESULTS/LIGAND_PDB jobs/<jobid>/TARGET_RESULTS/LIGAND_SDF
    if len(sys.argv) == 3:
        in_dir = Path(sys.argv[1])
        out_dir = Path(sys.argv[2])
    else:
        # default assumes you're running from the job root that contains TARGET_RESULTS/
        in_dir = Path("TARGET_RESULTS") / "LIGAND_PDB"
        out_dir = Path("TARGET_RESULTS") / "LIGAND_SDF"

    if not in_dir.exists():
        print("❌ Input dir not found:", in_dir)
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)

    pdbs = sorted(in_dir.glob("*.pdb"))
    if not pdbs:
        print("❌ No .pdb files found in:", in_dir)
        sys.exit(1)

    ok_n = 0
    fail_n = 0

    for pdb_path in pdbs:
        sdf_path = out_dir / f"{pdb_path.stem}.sdf"
        ok, msg = pdb_to_sdf(pdb_path, sdf_path)
        if ok:
            ok_n += 1
            print(f"✅ {pdb_path.name} -> {sdf_path.name} | {msg}")
        else:
            fail_n += 1
            print(f"⚠️  {pdb_path.name} | {msg}")

    print("\n====================")
    print(f"✅ SDF written: {ok_n}")
    print(f"⚠️  Failed    : {fail_n}")
    print(f"📁 OUT        : {out_dir}")
    print("====================")


if __name__ == "__main__":
    main()

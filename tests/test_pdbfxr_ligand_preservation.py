from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from Bio.PDB import Atom, Chain, Model, PDBIO, Residue, Structure


ROOT = Path("/Users/jxs794/Documents/warhead-hunter")
PDBFXR_PATH = ROOT / "pipeline_assets" / "4_PDBfxr.py"


def _load_pdbfxr_module():
    spec = importlib.util.spec_from_file_location("test_pdbfxr_module", PDBFXR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _five_char_hetatm_line(
    *,
    resname: str,
    resseq: int,
    atom_name: str,
    coords: tuple[float, float, float],
    occupancy: float,
    bfactor: float,
    element: str,
) -> str:
    structure = Structure.Structure("fixture")
    model = Model.Model(0)
    chain = Chain.Chain("A")
    residue = Residue.Residue((f"H_{resname}", resseq, " "), resname, " ")
    atom = Atom.Atom(
        atom_name,
        np.array(coords, dtype=float),
        bfactor,
        occupancy,
        " ",
        f"{atom_name:>4}",
        1,
        element=element,
    )
    residue.add(atom)
    chain.add(residue)
    model.add(chain)
    structure.add(model)

    sio = io.StringIO()
    io_pdb = PDBIO()
    io_pdb.set_structure(structure)
    io_pdb.save(sio)
    return sio.getvalue().splitlines()[0] + "\n"


class PdbfxrLigandPreservationTests(unittest.TestCase):
    def test_biopython_step3_serializes_five_char_ligand_with_shifted_columns(self):
        line = _five_char_hetatm_line(
            resname="A1I5W",
            resseq=1000,
            atom_name="C26",
            coords=(-23.695, 16.865, 14.424),
            occupancy=1.00,
            bfactor=15.02,
            element="C",
        )
        self.assertEqual(
            line,
            "HETATM    1  C26 A1I5W A1000     -23.695  16.865  14.424  1.00 15.02           C  \n",
        )

    def test_parser_accepts_shifted_five_character_residue_line(self):
        pdbfxr = _load_pdbfxr_module()
        parsed = pdbfxr.parse_hetatm_line(
            _five_char_hetatm_line(
                resname="A1I5W",
                resseq=1000,
                atom_name="C26",
                coords=(-23.695, 16.865, 14.424),
                occupancy=1.00,
                bfactor=15.02,
                element="C",
            )
        )
        self.assertEqual(parsed["resname"], "A1I5W")
        self.assertEqual(parsed["chain"], "A")
        self.assertEqual(parsed["resseq"], 1000)
        self.assertAlmostEqual(parsed["x"], -23.695)

    def test_parser_splits_glued_occupancy_and_high_bfactor_from_shifted_line(self):
        pdbfxr = _load_pdbfxr_module()
        parsed = pdbfxr.parse_hetatm_line(
            _five_char_hetatm_line(
                resname="A1I5G",
                resseq=601,
                atom_name="O1",
                coords=(-22.327, 13.237, 13.206),
                occupancy=0.50,
                bfactor=105.95,
                element="O",
            )
        )
        self.assertEqual(parsed["resname"], "A1I5G")
        self.assertEqual(parsed["chain"], "A")
        self.assertEqual(parsed["resseq"], 601)
        self.assertAlmostEqual(parsed["occ"], 0.50)
        self.assertAlmostEqual(parsed["bfac"], 105.95)

    def test_step4_fails_hard_when_target_ligand_rewrite_preserves_zero_atoms(self):
        with tempfile.TemporaryDirectory(prefix="pdbfxr-fail-") as tmp:
            root = Path(tmp)
            outdir = root / "fixture"
            pdb_root = Path(str(outdir) + "_PDB") / "NTSR1"
            pdb_root.mkdir(parents=True)

            pd.DataFrame([{"outdir": str(outdir)}]).to_csv(root / "CIFdata.csv", index=False)
            pd.DataFrame([{"ligand5": "A1I5W", "ligand3": "A1I", "ligandX": "A01"}]).to_csv(
                root / "5CharMAP.csv",
                index=False,
            )

            (pdb_root / "9qd4_A_A1I5W.pdb").write_text("HETATM broken A1I5W\n", encoding="utf-8")
            (root / "4_PDBfxr.py").write_text(PDBFXR_PATH.read_text(encoding="utf-8"), encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, "4_PDBfxr.py"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("ligand-preservation invariant failed", proc.stdout)

            failures = pd.read_csv(root / "Step4_Ligand_Rewrite_Failures.csv")
            self.assertEqual(len(failures), 1)
            self.assertEqual(int(failures.loc[0, "ligand_hetatm_inspected"]), 1)
            self.assertEqual(int(failures.loc[0, "ligand_hetatm_rewritten"]), 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import io
import sys
import types
import unittest
from pathlib import Path

import numpy as np
from Bio.PDB import Atom, Chain, Model, PDBIO, Residue, Structure


ROOT = Path("/Users/jxs794/Documents/warhead-hunter")
PDBMKR_PATH = ROOT / "pipeline_assets" / "3_PDBmkr.py"


def _load_pdbmkr_module():
    tqdm_stub = types.ModuleType("tqdm")
    tqdm_stub.tqdm = lambda iterable=None, **kwargs: iterable
    sys.modules.setdefault("tqdm", tqdm_stub)

    spec = importlib.util.spec_from_file_location("test_pdbmkr_module", PDBMKR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _build_structure():
    structure = Structure.Structure("fixture")
    model = Model.Model(0)
    chain = Chain.Chain("A")

    def add_residue(resname: str, resseq: int, serial: int, element: str = "C") -> None:
        residue = Residue.Residue((f"H_{resname}", resseq, " "), resname, " ")
        atom_name = f"{element}{serial}"
        atom = Atom.Atom(
            atom_name,
            np.array((-23.695 + serial, 16.865, 14.424), dtype=float),
            15.02,
            1.00,
            " ",
            f"{atom_name:>4}",
            serial,
            element=element,
        )
        residue.add(atom)
        chain.add(residue)

    add_residue("A1I5W", 1000, 1)
    add_residue("COF5X", 1001, 2)
    model.add(chain)
    structure.add(model)
    return structure


def _serialize_hetatm_lines(structure) -> list[str]:
    sio = io.StringIO()
    io_pdb = PDBIO()
    io_pdb.set_structure(structure)
    io_pdb.save(sio)
    return [line for line in sio.getvalue().splitlines() if line.startswith("HETATM")]


class PdbmkrFiveCharAliasingTests(unittest.TestCase):
    def test_selected_five_char_ligand_is_aliased_before_pdbio(self):
        pdbmkr = _load_pdbmkr_module()
        structure = _build_structure()

        before = _serialize_hetatm_lines(structure)
        self.assertTrue(any("A1I5W A1000" in line for line in before))
        self.assertTrue(any("COF5X A1001" in line for line in before))

        renamed = pdbmkr.rename_target_ligand_residues(structure, "A", "A1I5W", "A00")
        self.assertEqual(renamed, 1)

        after = _serialize_hetatm_lines(structure)
        self.assertTrue(any("A00 A1000" in line for line in after))
        self.assertFalse(any("A1I5W A1000" in line for line in after))
        self.assertTrue(any("COF5X A1001" in line for line in after))


if __name__ == "__main__":
    unittest.main()

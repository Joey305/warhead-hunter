from __future__ import annotations

import sys
import unittest
from pathlib import Path

from rdkit import Chem


PIPELINE_ASSETS = Path(__file__).resolve().parents[1] / "pipeline_assets"
if str(PIPELINE_ASSETS) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ASSETS))

from step11_atom_semantics import element_from_atom_record, infer_element_from_atom_name, is_hydrogen_symbol, mol_graph_atom_indices


class Step11AtomSemanticsTests(unittest.TestCase):
    def test_hydrogen_atom_names_do_not_fall_through_to_mercury(self):
        self.assertEqual(infer_element_from_atom_name("HG2"), "H")
        self.assertEqual(infer_element_from_atom_name("HG3"), "H")
        self.assertEqual(element_from_atom_record("HD2", None), "H")

    def test_explicit_element_column_preserves_real_heavy_elements(self):
        self.assertEqual(element_from_atom_record("HG", "Hg"), "Hg")
        self.assertFalse(is_hydrogen_symbol(element_from_atom_record("HG", "Hg")))

    def test_explicit_h_smiles_validates_on_non_h_graph(self):
        smiles = "[H]/N=C(/NCCC[C@@H](C(=O)OCc1ccccc1)NC(=O)OCc2ccccc2)\\N(C)CCC[C@@H]3[C@H]([C@H]([C@@H](O3)n4cnc5c4ncnc5N)O)O"
        mol = Chem.MolFromSmiles(smiles)
        self.assertIsNotNone(mol)
        self.assertEqual(mol.GetNumAtoms(), 51)
        self.assertEqual(len(mol_graph_atom_indices(mol)), 50)

        atom_symbols = (["C"] * 34) + (["N"] * 9) + (["O"] * 7) + (["H"] * 41)
        atom_symbols.extend(
            [
                element_from_atom_record("HG2", None),
                element_from_atom_record("HG3", None),
            ]
        )

        graph_count = sum(1 for symbol in atom_symbols if not is_hydrogen_symbol(symbol))
        self.assertEqual(len(atom_symbols), 93)
        self.assertEqual(graph_count, len(mol_graph_atom_indices(mol)))


if __name__ == "__main__":
    unittest.main()

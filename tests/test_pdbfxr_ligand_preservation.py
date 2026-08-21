from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path("/Users/jxs794/Documents/warhead-hunter")
PDBFXR_PATH = ROOT / "pipeline_assets" / "4_PDBfxr.py"


def _load_pdbfxr_module():
    spec = importlib.util.spec_from_file_location("test_pdbfxr_module", PDBFXR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _five_char_hetatm_line() -> str:
    return (
        "HETATM"
        f"{1:5d} "
        f"{'C1':>4}"
        f" {'A1I5W':<5}"
        f"{'A':1}"
        f"{601:4d}    "
        f"{-23.695:8.3f}{12.345:8.3f}{6.789:8.3f}"
        f"{1.00:6.2f}{20.00:6.2f}          "
        f"{'C':>2}\n"
    )


class PdbfxrLigandPreservationTests(unittest.TestCase):
    def test_parser_accepts_shifted_five_character_residue_line(self):
        pdbfxr = _load_pdbfxr_module()
        parsed = pdbfxr.parse_hetatm_line(_five_char_hetatm_line())
        self.assertEqual(parsed["resname"], "A1I5W")
        self.assertEqual(parsed["chain"], "A")
        self.assertEqual(parsed["resseq"], 601)
        self.assertAlmostEqual(parsed["x"], -23.695)

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

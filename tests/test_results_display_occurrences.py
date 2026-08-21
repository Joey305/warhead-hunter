from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path("/Users/jxs794/Documents/warhead-hunter")
PIPELINE_ASSETS = ROOT / "pipeline_assets"


def _write_occurrence_atom_rows(csv_path: Path) -> None:
    rows = []
    for pdb_id, chain, ligand, resid, smiles in [
        ("6yvr", "A", "BNG", "4001", "CCO"),
        ("6yvr", "A", "BNG", "4002", "CCO"),
        ("9qc1", "A", "A00", "601", "CCC"),
        ("9qc1", "A", "A00", "602", "CCC"),
    ]:
        rows.append({
            "Ligand": ligand,
            "Target": "NTSR1",
            "pdb_id": pdb_id,
            "Residue_ID": resid,
            "Chain": chain,
            "AtomIndex": 0,
            "AtomSymbol": "C",
            "atom_id": 1,
            "atom_name": "C1",
            "x": 1.0,
            "y": 2.0,
            "z": 3.0,
            "SMILES_ID": ligand,
            "SMILES": smiles,
            "Exposure_A2": 4.2,
        })
        rows.append({
            "Ligand": ligand,
            "Target": "NTSR1",
            "pdb_id": pdb_id,
            "Residue_ID": resid,
            "Chain": chain,
            "AtomIndex": 1,
            "AtomSymbol": "C",
            "atom_id": 2,
            "atom_name": "C2",
            "x": 2.0,
            "y": 3.0,
            "z": 4.0,
            "SMILES_ID": ligand,
            "SMILES": smiles,
            "Exposure_A2": 0.0,
        })
    pd.DataFrame(rows).to_csv(csv_path, index=False)


def _write_source_pdb(path: Path, ligand: str, chain: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            f"HETATM    1   C1 {ligand:>3} {chain} 501      11.111  12.222  13.333  1.00 20.00           C\n"
            f"HETATM    2   C2 {ligand:>3} {chain} 501      14.444  15.555  16.666  1.00 20.00           C\n"
        ),
        encoding="utf-8",
    )


class ResultsDisplayOccurrenceTests(unittest.TestCase):
    def test_results_display_is_occurrence_driven_even_when_war_pdb_is_shared(self):
        with tempfile.TemporaryDirectory(prefix="results-display-occ-") as tmp:
            job_root = Path(tmp)
            (job_root / "MCS_Output" / "MCS_SDF").mkdir(parents=True)
            (job_root / "MCS_Output" / "MCS_SVG").mkdir(parents=True)
            (job_root / "WAR_PDB" / "NTSR1").mkdir(parents=True)

            _write_occurrence_atom_rows(job_root / "MCS_Output" / "Ligand_MCS_SASA_ALL_ATOMS.csv")
            pd.DataFrame(
                [
                    {"pdb": "6yvr", "orig_chain": "AAA", "new_chain": "A"},
                    {"pdb": "9qc1", "orig_chain": "AAA", "new_chain": "A"},
                ]
            ).to_csv(job_root / "ChainRenameMAP.csv", index=False)

            _write_source_pdb(job_root / "WAR_PDB" / "NTSR1" / "6yvr_AAA_BNG.pdb", "BNG", "A")
            _write_source_pdb(job_root / "WAR_PDB" / "NTSR1" / "9qc1_AAA_A00.pdb", "A00", "A")

            for base in ["6yvr_A_BNG_4001", "6yvr_A_BNG_4002", "9qc1_A_A00_601", "9qc1_A_A00_602"]:
                (job_root / "MCS_Output" / "MCS_SDF" / f"{base}.sdf").write_text("stub sdf\n", encoding="utf-8")
                (job_root / "MCS_Output" / "MCS_SVG" / f"{base}_plain.svg").write_text("<svg/>", encoding="utf-8")
                (job_root / "MCS_Output" / "MCS_SVG" / f"{base}_exposed.svg").write_text("<svg/>", encoding="utf-8")

            for filename in ["16_ResultsDisplay.py", "occurrence_keys.py"]:
                (job_root / filename).write_text((PIPELINE_ASSETS / filename).read_text(encoding="utf-8"), encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, "16_ResultsDisplay.py"],
                cwd=job_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)

            results = pd.read_csv(job_root / "Results_Display.csv")
            self.assertEqual(len(results), 4)
            self.assertEqual(sorted(results["Residue_ID"].astype(str).tolist()), ["4001", "4002", "601", "602"])

            by_pdb = results.groupby("pdb_path")["Residue_ID"].apply(list).to_dict()
            self.assertEqual(
                sorted(str(v) for v in by_pdb[str((job_root / "WAR_PDB" / "NTSR1" / "6yvr_AAA_BNG.pdb").resolve())]),
                ["4001", "4002"],
            )
            self.assertEqual(
                sorted(str(v) for v in by_pdb[str((job_root / "WAR_PDB" / "NTSR1" / "9qc1_AAA_A00.pdb").resolve())]),
                ["601", "602"],
            )

            audit = pd.read_csv(job_root / "Results_Display_Artifact_Audit.csv")
            self.assertEqual(len(audit), 4)
            self.assertTrue((audit["PDB_Status"] == "ok").all())
            self.assertTrue((audit["SDF_Status"] == "exact").all())


if __name__ == "__main__":
    unittest.main()

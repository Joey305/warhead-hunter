from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

import job_runner


def _write_job_fixture(job_dir: Path, *, include_second_display_row: bool) -> None:
    target_results = job_dir / "TARGET_RESULTS"
    (target_results / "MCS_Output" / "MCS_SDF").mkdir(parents=True)
    (target_results / "MCS_Output" / "MCS_SVG").mkdir(parents=True)

    manifest_rows = []
    display_rows = []
    for resid in ["4001", "4002"]:
        manifest_rows.extend([
            {
                "Ligand": "BNG",
                "Target": "NTSR1",
                "pdb_id": "6yvr",
                "Residue_ID": resid,
                "Chain": "A",
                "AtomIndex": 0,
                "AtomSymbol": "C",
                "atom_id": 1,
                "atom_name": "C1",
                "x": 1.0,
                "y": 2.0,
                "z": 3.0,
                "SMILES_ID": "BNG",
                "SMILES": "CCO",
                "Exposure_A2": 3.0,
            },
            {
                "Ligand": "BNG",
                "Target": "NTSR1",
                "pdb_id": "6yvr",
                "Residue_ID": resid,
                "Chain": "A",
                "AtomIndex": 1,
                "AtomSymbol": "C",
                "atom_id": 2,
                "atom_name": "C2",
                "x": 1.5,
                "y": 2.5,
                "z": 3.5,
                "SMILES_ID": "BNG",
                "SMILES": "CCO",
                "Exposure_A2": 0.0,
            },
        ])

        if resid == "4001" or include_second_display_row:
            display_rows.append({
                "Target": "NTSR1",
                "pdb_id": "6yvr",
                "Chain": "A",
                "Warhead": "BNG",
                "Residue_ID": resid,
                "pdb_path": "TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_A_A00.pdb",
                "sdf_path": str((target_results / "MCS_Output" / "MCS_SDF" / f"6yvr_A_BNG_{resid}.sdf").resolve()),
            })

        (target_results / "MCS_Output" / "MCS_SDF" / f"6yvr_A_BNG_{resid}.sdf").write_text("stub sdf\n", encoding="utf-8")
        (target_results / "MCS_Output" / "MCS_SVG" / f"6yvr_A_BNG_{resid}_plain.svg").write_text("<svg/>", encoding="utf-8")
        (target_results / "MCS_Output" / "MCS_SVG" / f"6yvr_A_BNG_{resid}_exposed.svg").write_text("<svg/>", encoding="utf-8")

    pd.DataFrame(manifest_rows).to_csv(target_results / "MCS_Output" / "Ligand_MCS_SASA_ALL_ATOMS.csv", index=False)
    pd.DataFrame(display_rows).to_csv(target_results / "Results_Display.csv", index=False)
    pd.DataFrame([{"x": 1}]).to_csv(target_results / "Warhead_SASA_atoms.csv", index=False)
    pd.DataFrame([{"x": 1}]).to_csv(target_results / "Ligand_3D_Atoms_with_SASA.csv", index=False)

    pdb_path = job_dir / "TARGET_RESULTS" / "WAR_PDB" / "NTSR1" / "6yvr_A_A00.pdb"
    pdb_path.parent.mkdir(parents=True)
    pdb_path.write_text("ATOM      1  CA  GLY A   1      11.111  12.222  13.333  1.00 20.00           C\n", encoding="utf-8")


class JobRunnerDisplayValidationTests(unittest.TestCase):
    def test_validator_rejects_partial_gallery_coverage(self):
        with tempfile.TemporaryDirectory(prefix="display-validator-fail-") as tmp:
            job_dir = Path(tmp) / "job1234"
            job_dir.mkdir()
            _write_job_fixture(job_dir, include_second_display_row=False)

            with self.assertRaisesRegex(RuntimeError, "missing_display=1"):
                job_runner.validate_required_display_artifacts("job1234", str(job_dir))

    def test_validator_accepts_full_gallery_coverage(self):
        with tempfile.TemporaryDirectory(prefix="display-validator-pass-") as tmp:
            job_dir = Path(tmp) / "job1234"
            job_dir.mkdir()
            _write_job_fixture(job_dir, include_second_display_row=True)

            job_runner.validate_required_display_artifacts("job1234", str(job_dir))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app


class AppProteinArtifactResolutionTests(unittest.TestCase):
    def test_results_display_pdb_path_is_authoritative_for_chain_renamed_files(self):
        with tempfile.TemporaryDirectory(prefix="app-protein-artifact-") as tmp:
            jobs_root = Path(tmp)
            job_id = "job1234"
            job_dir = jobs_root / job_id
            target_results = job_dir / "TARGET_RESULTS"
            pdb_path = target_results / "WAR_PDB" / "NTSR1" / "6yvr_AAA_BNG.pdb"
            pdb_path.parent.mkdir(parents=True)
            pdb_path.write_text(
                "ATOM      1  CA  GLY A   1      11.111  12.222  13.333  1.00 20.00           C\n",
                encoding="utf-8",
            )
            pd.DataFrame(
                [
                    {
                        "Target": "NTSR1",
                        "pdb_id": "6yvr",
                        "Chain": "A",
                        "Warhead": "BNG",
                        "Residue_ID": "4001",
                        "pdb_path": "TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb",
                    }
                ]
            ).to_csv(target_results / "Results_Display.csv", index=False)

            with patch.object(app, "JOBS_DIR", jobs_root):
                resolved = app._resolve_complex_pdb_artifact("job1234", "6yvr", "A", "BNG", "4001")

            self.assertTrue(resolved.get("ok"), resolved)
            self.assertEqual(resolved.get("source"), "local_results_display")
            self.assertEqual(resolved.get("relative_path"), "TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb")
            self.assertEqual(Path(resolved["local_path"]), pdb_path.resolve())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import randy_backup_client


class RandyBackupPlanTests(unittest.TestCase):
    def _job_dir(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="randy-backup-plan-"))
        (root / "job_metadata.json").write_text("{}", encoding="utf-8")
        (root / "job.log").write_text("log", encoding="utf-8")
        (root / "input.csv").write_text("id\n1\n", encoding="utf-8")
        (root / "Protein_Data.csv").write_text("protein\nBACE1\n", encoding="utf-8")
        (root / "summary.json").write_text("{}", encoding="utf-8")
        (root / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF").mkdir(parents=True)
        (root / "TARGET_RESULTS" / "MCS_Output" / "MCS_SVG").mkdir(parents=True)
        (root / "TARGET_RESULTS" / "WAR_PDB").mkdir(parents=True)
        (root / "TARGET_RESULTS" / "Results_Display.csv").write_text("pdb_id,Chain\n1abc,A\n", encoding="utf-8")
        (root / "TARGET_RESULTS" / "Resolved_SASA_Summary.csv").write_text("pdb_id,Chain\n1abc,A\n", encoding="utf-8")
        (root / "TARGET_RESULTS" / "Warhead_SASA_summary.csv").write_text("warhead,sasa\nABC,1\n", encoding="utf-8")
        (root / "TARGET_RESULTS" / "Warhead_SASA_atoms.csv").write_text("warhead,atom\nABC,C1\n", encoding="utf-8")
        (root / "TARGET_RESULTS" / "Ligand_Metadata.csv").write_text("ligand,smiles\nABC,C\n", encoding="utf-8")
        (root / "TARGET_RESULTS" / "Ligand_3D_Atoms.csv").write_text("atom\nC1\n", encoding="utf-8")
        (root / "TARGET_RESULTS" / "Ligand_3D_Atoms_with_SASA.csv").write_text("atom,sasa\nC1,1\n", encoding="utf-8")
        (root / "TARGET_RESULTS" / "3DSASAmapped.csv").write_text("atom,sasa\nC1,1\n", encoding="utf-8")
        (root / "TARGET_RESULTS" / "MCS_Output" / "MCS_SDF" / "1abc_A_ABC_10.sdf").write_text("sdf", encoding="utf-8")
        (root / "TARGET_RESULTS" / "MCS_Output" / "MCS_SVG" / "1abc_A_ABC_10.svg").write_text("<svg/>", encoding="utf-8")
        (root / "TARGET_RESULTS" / "WAR_PDB" / "1abc_A_ABC.pdb").write_text("ATOM", encoding="utf-8")
        return root

    def test_curated_plan_selects_required_and_skips_optional(self):
        job_dir = self._job_dir()
        (job_dir / "optional.bin").write_bytes(b"x" * 4096)
        plan = randy_backup_client.build_backup_plan(job_dir, max_bytes=2048)
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["archive_profile"], "curated_results")
        self.assertIn("job_metadata.json", plan["required_selected"])
        self.assertEqual(plan["required_skipped"], [])
        self.assertGreater(plan["skipped_file_count"], 0)

    def test_fails_when_required_file_cannot_fit(self):
        job_dir = self._job_dir()
        (job_dir / "job.log").write_bytes(b"x" * 5000)
        plan = randy_backup_client.build_backup_plan(job_dir, max_bytes=1024)
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["plan_status"], "archive_too_large")
        self.assertIn("job.log", plan["plan_reason"])

    def test_duplicate_root_results_are_not_selected_when_target_results_exists(self):
        job_dir = self._job_dir()
        (job_dir / "WAR_PDB").mkdir()
        (job_dir / "WAR_PDB" / "1abc_A_ABC.pdb").write_bytes(b"x" * 5000)
        (job_dir / "MCS_Output" / "MCS_SDF").mkdir(parents=True)
        (job_dir / "MCS_Output" / "MCS_SDF" / "1abc_A_ABC_10.sdf").write_bytes(b"y" * 3000)
        plan = randy_backup_client.build_backup_plan(job_dir, max_bytes=50_000)
        selected_rels = {item.rel for item in plan["selected_files"]}
        self.assertIn("TARGET_RESULTS/WAR_PDB/1abc_A_ABC.pdb", selected_rels)
        self.assertNotIn("WAR_PDB/1abc_A_ABC.pdb", selected_rels)
        self.assertNotIn("MCS_Output/MCS_SDF/1abc_A_ABC_10.sdf", selected_rels)

    def test_cif_exclusion_respects_env(self):
        job_dir = self._job_dir()
        (job_dir / "TARGET_RESULTS" / "extra.cif").write_bytes(b"x" * 2048)
        with patch.dict("os.environ", {"WARHEAD_JOB_BACKUP_EXCLUDE_CIF": "1"}, clear=False):
            plan = randy_backup_client.build_backup_plan(job_dir, max_bytes=50_000)
        selected_rels = {item.rel for item in plan["selected_files"]}
        self.assertNotIn("TARGET_RESULTS/extra.cif", selected_rels)
        self.assertTrue(plan["cif_excluded"])

    def test_metadata_only_profile_when_no_viewer_critical_results_exist(self):
        root = Path(tempfile.mkdtemp(prefix="randy-backup-plan-meta-"))
        (root / "job_metadata.json").write_text("{}", encoding="utf-8")
        (root / "job.log").write_text("log", encoding="utf-8")
        (root / "input.csv").write_text("id\n1\n", encoding="utf-8")
        plan = randy_backup_client.build_backup_plan(root, max_bytes=2048)
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["archive_profile"], "metadata_only")

    def test_missing_required_groups_are_reported(self):
        root = Path(tempfile.mkdtemp(prefix="randy-backup-plan-missing-"))
        (root / "job_metadata.json").write_text("{}", encoding="utf-8")
        (root / "job.log").write_text("log", encoding="utf-8")
        plan = randy_backup_client.build_backup_plan(root, max_bytes=2048)
        self.assertTrue(plan["ok"])
        self.assertIn("input.csv", plan["required_missing"])
        self.assertIn("summary.json", plan["required_missing"])
        self.assertNotIn("job_metadata.json", plan["required_missing"])

    def test_plan_fails_when_results_display_references_missing_artifact(self):
        job_dir = self._job_dir()
        (job_dir / "TARGET_RESULTS" / "WAR_PDB" / "1abc_A_ABC.pdb").unlink()
        (job_dir / "TARGET_RESULTS" / "Results_Display.csv").write_text(
            (
                "pdb_id,Chain,pdb_path,sdf_path,svg_plain_path,svg_exposed_path\n"
                "1abc,A,TARGET_RESULTS/WAR_PDB/1abc_A_ABC.pdb,"
                "TARGET_RESULTS/MCS_Output/MCS_SDF/1abc_A_ABC_10.sdf,"
                "TARGET_RESULTS/MCS_Output/MCS_SVG/1abc_A_ABC_10_plain.svg,"
                "TARGET_RESULTS/MCS_Output/MCS_SVG/1abc_A_ABC_10_exposed.svg\n"
            ),
            encoding="utf-8",
        )
        (job_dir / "TARGET_RESULTS" / "MCS_Output" / "MCS_SVG" / "1abc_A_ABC_10_plain.svg").write_text("<svg/>", encoding="utf-8")
        (job_dir / "TARGET_RESULTS" / "MCS_Output" / "MCS_SVG" / "1abc_A_ABC_10_exposed.svg").write_text("<svg/>", encoding="utf-8")

        plan = randy_backup_client.build_backup_plan(job_dir, max_bytes=50_000)

        self.assertFalse(plan["ok"])
        self.assertEqual(plan["plan_status"], "job_corrupt")
        self.assertIn(
            "TARGET_RESULTS/WAR_PDB/1abc_A_ABC.pdb",
            plan["route_critical_checks"]["missing_results_display_artifacts"],
        )


if __name__ == "__main__":
    unittest.main()

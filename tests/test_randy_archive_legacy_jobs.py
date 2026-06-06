from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import importlib.util
import sys
import types


def _load_randy_app():
    backup_receiver = types.ModuleType("backup_receiver")
    e3_data_routes = types.ModuleType("backup_receiver.e3_data_routes")
    e3_data_routes.register_e3_routes = lambda _app: None
    backup_receiver.e3_data_routes = e3_data_routes
    sys.modules.setdefault("backup_receiver", backup_receiver)
    sys.modules["backup_receiver.e3_data_routes"] = e3_data_routes

    spec = importlib.util.spec_from_file_location(
        "test_randy_app_module",
        "/Users/jxs794/Documents/warhead-hunter/RANDY/app.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


randy_app = _load_randy_app()


class RandyArchiveLegacyJobsTests(unittest.TestCase):
    def test_archive_job_metadata_reads_root_level_legacy_input_csv(self):
        with tempfile.TemporaryDirectory(prefix="randy-legacy-root-") as tmp:
            job_dir = Path(tmp) / "2d9b72a8"
            job_dir.mkdir()
            (job_dir / "input.csv").write_text(
                "protein,search_query,fasta\n"
                'OGA,"O-GlcNAcase 9BA9 6PM9 5UN9 5M7T",">sp|OGA\\nMVQKES"\n',
                encoding="utf-8",
            )
            (job_dir / "Results_Display.csv").write_text("pdb_id,Chain\n9ba9,A\n", encoding="utf-8")
            (job_dir / "job_metadata.json").write_text(
                '{"job_id":"2d9b72a8","status":"completed","request":{"target_name":"","search_query":"","fasta_seq":""}}',
                encoding="utf-8",
            )

            meta = randy_app._archive_job_metadata(job_dir)

        self.assertEqual(meta["protein"], "OGA")
        self.assertEqual(meta["target_name"], "OGA")
        self.assertEqual(meta["search_query"], "O-GlcNAcase 9BA9 6PM9 5UN9 5M7T")
        self.assertEqual(meta["status"], "completed")
        self.assertTrue(meta["has_results"])

    def test_archive_job_metadata_reads_packaged_job_files_layout(self):
        with tempfile.TemporaryDirectory(prefix="randy-job-files-") as tmp:
            job_dir = Path(tmp) / "8926f69a"
            job_files = job_dir / "job_files"
            job_files.mkdir(parents=True)
            (job_files / "input.csv").write_text(
                "protein,search_query,fasta\nBACE1,Beta secretase 1,>sp|BACE1\nMAYP\n",
                encoding="utf-8",
            )
            (job_files / "Results_Display.csv").write_text("pdb_id,Chain\n1fkn,A\n", encoding="utf-8")
            (job_files / "job_metadata.json").write_text('{"job_id":"8926f69a","status":"completed"}', encoding="utf-8")

            meta = randy_app._archive_job_metadata(job_dir)

        self.assertEqual(meta["protein"], "BACE1")
        self.assertEqual(meta["search_query"], "Beta secretase 1")
        self.assertEqual(meta["status"], "completed")
        self.assertTrue(meta["has_results"])


if __name__ == "__main__":
    unittest.main()

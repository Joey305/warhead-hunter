from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class BrowseLegacyJobsTests(unittest.TestCase):
    def test_read_job_request_csv_accepts_legacy_alias_columns(self):
        with tempfile.TemporaryDirectory(prefix="browse-legacy-csv-") as tmp:
            job_dir = Path(tmp)
            (job_dir / "input.csv").write_text(
                "target,query,sequence\n"
                'OGA,"O-GlcNAcase 9BA9 6PM9 5UN9 5M7T",">sp|OGA\\nMVQKES"\n',
                encoding="utf-8",
            )

            meta = app._read_job_request_csv(job_dir, "input.csv")

        self.assertEqual(meta, {
            "protein": "OGA",
            "search_query": "O-GlcNAcase 9BA9 6PM9 5UN9 5M7T",
            "fasta": ">sp|OGA\\nMVQKES",
        })

    def test_job_meta_from_dir_uses_legacy_top_level_target_and_query(self):
        with tempfile.TemporaryDirectory(prefix="browse-legacy-job-") as tmp:
            job_id = "2d9b72a8"
            job_dir = Path(tmp) / job_id
            job_dir.mkdir()
            with patch.object(app, "_read_job_metadata", return_value={
                "job_id": job_id,
                "status": "completed",
                "target": "OGA",
                "query": "O-GlcNAcase 9BA9 6PM9 5UN9 5M7T",
            }), patch.object(app.disk_jobs, "hydrate_job_from_disk", return_value={}):
                meta = app._job_meta_from_dir(job_dir)

        self.assertEqual(meta["protein"], "OGA")
        self.assertEqual(meta["target_name"], "OGA")
        self.assertEqual(meta["search_query"], "O-GlcNAcase 9BA9 6PM9 5UN9 5M7T")


if __name__ == "__main__":
    unittest.main()

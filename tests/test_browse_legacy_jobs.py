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

    def test_merge_browse_job_records_prefers_original_metadata(self):
        merged = app._merge_browse_job_records(
            {
                "job_id": "2d9b72a8",
                "protein": "OGA",
                "target_name": "OGA",
                "search_query": "O-GlcNAcase 9BA9 6PM9 5UN9 5M7T",
                "has_results": True,
                "available": True,
                "status": "completed",
            },
            {
                "job_id": "2d9b72a8",
                "protein": "2d9b72a8",
                "target_name": "2d9b72a8",
                "search_query": "",
                "has_results": True,
                "available": True,
                "status": "completed",
            },
        )

        self.assertEqual(merged["protein"], "OGA")
        self.assertEqual(merged["target_name"], "OGA")
        self.assertEqual(merged["search_query"], "O-GlcNAcase 9BA9 6PM9 5UN9 5M7T")

    def test_browse_normalization_keeps_submission_channel_separate_from_archive_source(self):
        record = app._normalize_browse_job(
            {
                "job_id": "2d9b72a8",
                "status": "failed",
                "created_at": "2026-05-26T22:41:35Z",
                "finished_at": "2026-05-27T00:41:42Z",
                "metadata": {"source": "api"},
            },
            source="randy_hunter_job_archive",
            mode="randy",
        )

        self.assertEqual(record["source"], "randy_hunter_job_archive")
        self.assertEqual(record["submission_source"], "api")
        self.assertEqual(record["submission_source_label"], "API")
        self.assertEqual(record["finished_at"], "2026-05-27T00:41:42Z")

    def test_legacy_web_submission_is_labeled_hunter_tool(self):
        source, label = app._submission_channel({"source": "web"})

        self.assertEqual(source, "tool")
        self.assertEqual(label, "HUNTER TOOL")


if __name__ == "__main__":
    unittest.main()

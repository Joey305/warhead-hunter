from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from flask import Response

import app as warhead_app


class AppArchiveRoutesTests(unittest.TestCase):
    def test_archive_protein_route_uses_results_display_exact_path(self):
        df = pd.DataFrame(
            [
                {
                    "pdb_id": "6yvr",
                    "Chain": "A",
                    "Warhead": "BNG",
                    "Residue_ID": "4001",
                    "pdb_path": "TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb",
                }
            ]
        )
        pdb_bytes = (
            b"ATOM      1  CA  GLY A   1      11.111  12.222  13.333  1.00 20.00           C\n"
            b"HETATM    2  C1  BNG B4001      11.111  12.222  13.333  1.00 20.00           C\n"
        )

        with tempfile.TemporaryDirectory(prefix="app-archive-protein-") as tmp:
            missing_job_dir = Path(tmp) / "61bf4697"
            with patch.object(warhead_app, "safe_job_dir", return_value=missing_job_dir), patch.object(
                warhead_app, "load_results_display", return_value=df
            ), patch.object(
                warhead_app,
                "randy_find_file",
                return_value={
                    "relative_path": "job_files/TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb",
                    "filename": "6yvr_AAA_BNG.pdb",
                    "source": "randy_files",
                },
            ), patch.object(
                warhead_app,
                "randy_get_file_bytes",
                return_value=(pdb_bytes, "chemical/x-pdb"),
            ):
                client = warhead_app.app.test_client()
                response = client.get("/api/protein/61bf4697/6yvr/A?ligand=BNG&resid=4001")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ATOM", response.data)
        self.assertNotIn(b'"ok": false', response.data.lower())

    def test_archive_job_files_routes_work_without_local_job_dir(self):
        archive_files = [
            {
                "relative_path": "job_files/TARGET_RESULTS/Results_Display.csv",
                "size_bytes": 23,
                "source": "randy_files",
            },
            {
                "relative_path": "job_files/TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb",
                "size_bytes": 91,
                "source": "randy_files",
            },
        ]

        def proxy(job_id, relative_path, mimetype="application/octet-stream", **kwargs):
            self.assertEqual(job_id, "61bf4697")
            self.assertEqual(relative_path, "job_files/TARGET_RESULTS/Results_Display.csv")
            self.assertTrue(kwargs.get("as_attachment"))
            self.assertEqual(kwargs.get("download_name"), "Results_Display.csv")
            return Response(b"pdb_id,Chain\n6yvr,A\n", mimetype="text/csv")

        with tempfile.TemporaryDirectory(prefix="app-archive-files-") as tmp:
            missing_job_dir = Path(tmp) / "61bf4697"
            with patch.object(warhead_app, "safe_job_dir", return_value=missing_job_dir), patch.object(
                warhead_app, "randy_job_exists", return_value=True
            ), patch.object(
                warhead_app, "randy_list_files", return_value=archive_files
            ), patch.object(
                warhead_app,
                "randy_find_file",
                return_value={
                    "relative_path": "job_files/TARGET_RESULTS/Results_Display.csv",
                    "filename": "Results_Display.csv",
                },
            ), patch.object(warhead_app, "randy_proxy_file_response", side_effect=proxy):
                client = warhead_app.app.test_client()
                listing = client.get("/api/jobs/61bf4697/files")
                download = client.get("/api/jobs/61bf4697/files/TARGET_RESULTS/Results_Display.csv")

        self.assertEqual(listing.status_code, 200)
        payload = listing.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(
            {item["relative_path"] for item in payload["files"]},
            {
                "TARGET_RESULTS/Results_Display.csv",
                "TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb",
            },
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"pdb_id,Chain\n6yvr,A\n")

    def test_archive_bundle_route_builds_valid_zip_without_local_job_dir(self):
        archive_files = [
            {
                "relative_path": "job_files/TARGET_RESULTS/Results_Display.csv",
                "size_bytes": 23,
                "source": "randy_files",
            },
            {
                "relative_path": "job_files/TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb",
                "size_bytes": 91,
                "source": "randy_files",
            },
        ]

        def get_bytes(job_id, relative_path, timeout=30):
            expected = {
                "job_files/TARGET_RESULTS/Results_Display.csv": b"pdb_id,Chain\n6yvr,A\n",
                "job_files/TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb": (
                    b"ATOM      1  CA  GLY A   1      11.111  12.222  13.333  1.00 20.00           C\n"
                ),
            }
            return expected.get(relative_path), "application/octet-stream"

        with tempfile.TemporaryDirectory(prefix="app-archive-bundle-") as tmp:
            missing_job_dir = Path(tmp) / "61bf4697"
            with patch.object(warhead_app, "safe_job_dir", return_value=missing_job_dir), patch.object(
                warhead_app, "randy_job_exists", return_value=True
            ), patch.object(
                warhead_app, "randy_get_job_index", return_value={}
            ), patch.object(
                warhead_app, "randy_list_files", return_value=archive_files
            ), patch.object(
                warhead_app, "randy_get_file_bytes", side_effect=get_bytes
            ):
                client = warhead_app.app.test_client()
                response = client.get("/api/jobs/61bf4697/bundle")

        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(response.data))
        self.assertEqual(
            sorted(archive.namelist()),
            [
                "61bf4697/TARGET_RESULTS/Results_Display.csv",
                "61bf4697/TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb",
            ],
        )


if __name__ == "__main__":
    unittest.main()

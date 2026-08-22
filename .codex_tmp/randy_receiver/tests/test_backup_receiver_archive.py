from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))

try:
    import backup_receiver.app as backup_app
except ModuleNotFoundError:
    import app as backup_app


class BackupReceiverArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="randy-archive-tests-")
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.jobs_dir = self.root / "hunter_jobs"
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(backup_app, "BACKUP_TOKEN", "test-token"))
        self.stack.enter_context(patch.object(backup_app, "BACKUP_DIR", self.data_dir))
        self.stack.enter_context(patch.object(backup_app, "HUNTER_JOBS_DIR", self.jobs_dir))
        self.stack.enter_context(patch.object(backup_app, "DB_PATH", self.data_dir / "protac_backup.sqlite3"))
        self.stack.enter_context(patch.object(backup_app, "JSONL_PATH", self.data_dir / "protac_events.jsonl"))
        self.stack.enter_context(patch.object(backup_app, "EVENTS_CSV_PATH", self.data_dir / "protac_events.csv"))
        self.stack.enter_context(patch.object(backup_app, "COMPONENTS_CSV_PATH", self.data_dir / "protac_components.csv"))
        self.stack.enter_context(patch.object(backup_app, "LINKER_USAGE_CSV_PATH", self.data_dir / "protac_linker_library_usage.csv"))
        self.stack.enter_context(patch.object(backup_app, "HUNTER_HANDOFF_CSV_PATH", self.data_dir / "warhead_hunter_handoffs.csv"))
        self.stack.enter_context(patch.object(backup_app, "HUNTER_ARCHIVES_CSV_PATH", self.data_dir / "warhead_hunter_job_archives.csv"))
        backup_app.init_storage()
        self.client = backup_app.APP.test_client()

    def tearDown(self):
        self.stack.close()
        self.tmp.cleanup()

    def _auth(self):
        return {"Authorization": "Bearer test-token"}

    def _job_dir(self, job_id: str = "61bf4697") -> Path:
        job_dir = self.jobs_dir / job_id
        (job_dir / "job_files").mkdir(parents=True, exist_ok=True)
        (job_dir / "archives").mkdir(parents=True, exist_ok=True)
        return job_dir

    def _write_results_display(self, job_dir: Path, rows: list[dict[str, str]]):
        lines = [
            "Target,pdb_id,Chain,Warhead,Residue_ID,pdb_path,sdf_path,svg_plain_path,svg_exposed_path"
        ]
        for row in rows:
            lines.append(",".join([
                row.get("Target", "NTSR1"),
                row.get("pdb_id", ""),
                row.get("Chain", ""),
                row.get("Warhead", ""),
                row.get("Residue_ID", ""),
                row.get("pdb_path", ""),
                row.get("sdf_path", ""),
                row.get("svg_plain_path", ""),
                row.get("svg_exposed_path", ""),
            ]))
        (job_dir / "job_files" / "Results_Display.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_artifact(self, job_dir: Path, relative_path: str, content: bytes | str):
        target = job_dir / "job_files" / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def _base_occurrence_job(self, job_id: str = "61bf4697") -> Path:
        job_dir = self._job_dir(job_id)
        pdb_rel = "TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb"
        self._write_artifact(
            job_dir,
            pdb_rel,
            "ATOM      1  CA  GLY A   1      11.111  12.222  13.333  1.00 20.00           C\n"
            "HETATM    2  C1  BNG A4001      11.111  12.222  13.333  1.00 20.00           C\n",
        )
        rows = []
        for resid in ("4001", "4002", "4003"):
            sdf_rel = f"TARGET_RESULTS/MCS_Output/MCS_SDF/6yvr_A_BNG_{resid}.sdf"
            plain_rel = f"TARGET_RESULTS/MCS_Output/MCS_SVG/6yvr_A_BNG_{resid}_plain.svg"
            exposed_rel = f"TARGET_RESULTS/MCS_Output/MCS_SVG/6yvr_A_BNG_{resid}_exposed.svg"
            self._write_artifact(job_dir, sdf_rel, f"sdf-{resid}")
            self._write_artifact(job_dir, plain_rel, f"<svg>plain-{resid}</svg>")
            self._write_artifact(job_dir, exposed_rel, f"<svg>exposed-{resid}</svg>")
            rows.append(
                {
                    "pdb_id": "6yvr",
                    "Chain": "A",
                    "Warhead": "BNG",
                    "Residue_ID": resid,
                    "pdb_path": pdb_rel,
                    "sdf_path": f"/app/jobs/{job_id}/{sdf_rel}",
                    "svg_plain_path": f"/app/jobs/{job_id}/{plain_rel}",
                    "svg_exposed_path": f"/app/jobs/{job_id}/{exposed_rel}",
                }
            )
        self._write_results_display(job_dir, rows)
        return job_dir

    def test_results_display_occurrence_resolves_chain_renamed_pdb(self):
        self._base_occurrence_job()
        pdb_file, sdf_file, svg_files = backup_app._locate_stored_handoff_files("61bf4697", "6yvr", "A", "BNG", "4001")
        self.assertEqual(pdb_file.name, "6yvr_AAA_BNG.pdb")
        self.assertEqual(sdf_file.name, "6yvr_A_BNG_4001.sdf")
        self.assertEqual(sorted(path.name for path in svg_files), ["6yvr_A_BNG_4001_exposed.svg", "6yvr_A_BNG_4001_plain.svg"])

    def test_one_character_chain_occurrence_still_works(self):
        job_dir = self._job_dir("joba001")
        pdb_rel = "TARGET_RESULTS/WAR_PDB/NTSR1/9qc1_A_A00.pdb"
        sdf_rel = "TARGET_RESULTS/MCS_Output/MCS_SDF/9qc1_A_A00_601.sdf"
        plain_rel = "TARGET_RESULTS/MCS_Output/MCS_SVG/9qc1_A_A00_601_plain.svg"
        exposed_rel = "TARGET_RESULTS/MCS_Output/MCS_SVG/9qc1_A_A00_601_exposed.svg"
        self._write_artifact(job_dir, pdb_rel, "ATOM      1  CA  GLY A   1      11.111  12.222  13.333  1.00 20.00           C\n")
        self._write_artifact(job_dir, sdf_rel, "sdf")
        self._write_artifact(job_dir, plain_rel, "<svg/>")
        self._write_artifact(job_dir, exposed_rel, "<svg/>")
        self._write_results_display(
            job_dir,
            [{
                "pdb_id": "9qc1",
                "Chain": "A",
                "Warhead": "A00",
                "Residue_ID": "601",
                "pdb_path": pdb_rel,
                "sdf_path": sdf_rel,
                "svg_plain_path": plain_rel,
                "svg_exposed_path": exposed_rel,
            }],
        )

        pdb_file, sdf_file, svg_files = backup_app._locate_stored_handoff_files("joba001", "9qc1", "A", "A00", "601")
        self.assertEqual(pdb_file.name, "9qc1_A_A00.pdb")
        self.assertEqual(sdf_file.name, "9qc1_A_A00_601.sdf")
        self.assertEqual(len(svg_files), 2)

    def test_results_display_options_keep_distinct_occurrence_artifacts(self):
        job_dir = self._base_occurrence_job()
        options = backup_app._scan_hunter_job_options("61bf4697", job_dir)
        self.assertEqual(len(options), 3)
        self.assertEqual({item["pdb_path"] for item in options}, {"TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb"})
        self.assertEqual(
            {item["sdf_path"] for item in options},
            {
                "TARGET_RESULTS/MCS_Output/MCS_SDF/6yvr_A_BNG_4001.sdf",
                "TARGET_RESULTS/MCS_Output/MCS_SDF/6yvr_A_BNG_4002.sdf",
                "TARGET_RESULTS/MCS_Output/MCS_SDF/6yvr_A_BNG_4003.sdf",
            },
        )

    def test_exact_file_route_serves_extracted_pdb(self):
        self._base_occurrence_job()
        response = self.client.get(
            "/backup/hunter-job/61bf4697/file/TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb",
            headers=self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "chemical/x-pdb")
        self.assertTrue(response.data.startswith(b"ATOM"))

    def test_exact_file_route_materializes_from_zip_when_extracted_copy_missing(self):
        job_dir = self._job_dir("jobzip01")
        member = "job_files/TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb"
        archive = job_dir / "archives" / "jobzip01_warhead_hunter_results.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(member, "ATOM      1  CA  GLY A   1      11.111  12.222  13.333  1.00 20.00           C\n")
        (job_dir / "job_archive_manifest.json").write_text(
            json.dumps({"archive_file": str(archive), "extracted_files_sample": []}),
            encoding="utf-8",
        )
        (job_dir / backup_app.FULL_ARCHIVE_FILE_MANIFEST_NAME).write_text(
            json.dumps({"files": [{"relative_path": "TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb", "size_bytes": 91}]}),
            encoding="utf-8",
        )

        response = self.client.get(
            "/backup/hunter-job/jobzip01/file/TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb",
            headers=self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"ATOM"))
        self.assertTrue((job_dir / "job_files" / "TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb").exists())

    def test_bundle_route_returns_valid_zip(self):
        job_dir = self._base_occurrence_job()
        archive = job_dir / "archives" / "61bf4697_warhead_hunter_results.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("job_files/Results_Display.csv", "Target,pdb_id,Chain,Warhead,Residue_ID\n")
        (job_dir / "job_archive_manifest.json").write_text(
            json.dumps({"archive_file": str(archive), "extracted_files_sample": []}),
            encoding="utf-8",
        )

        response = self.client.get("/backup/hunter-job/61bf4697/bundle", headers=self._auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")
        self.assertTrue(response.data.startswith(b"PK"))
        zipfile.ZipFile(io.BytesIO(response.data))

    def test_traversal_is_rejected(self):
        resolved = backup_app.resolve_hunter_archive_file("61bf4697", "../../etc/passwd")
        self.assertFalse(resolved["ok"])
        self.assertEqual(resolved["status"], 400)

    def test_full_archive_manifest_supports_more_than_sampled_first_100_files(self):
        job_dir = self._job_dir("jobmf001")
        records = []
        for idx in range(120):
            rel = f"TARGET_RESULTS/MCS_Output/MCS_SDF/item_{idx:03d}.sdf"
            self._write_artifact(job_dir, rel, f"sdf-{idx}")
            records.append({"relative_path": rel, "size_bytes": 7})
        (job_dir / "job_archive_manifest.json").write_text(
            json.dumps({"extracted_files_sample": records[:100]}),
            encoding="utf-8",
        )
        (job_dir / backup_app.FULL_ARCHIVE_FILE_MANIFEST_NAME).write_text(
            json.dumps({"files": records}),
            encoding="utf-8",
        )

        loaded = backup_app._load_archive_file_manifest(job_dir)
        matches = backup_app._manifest_path_matches(job_dir, "TARGET_RESULTS/MCS_Output/MCS_SDF/item_119.sdf")
        self.assertEqual(len(loaded), 120)
        self.assertIn("job_files/TARGET_RESULTS/MCS_Output/MCS_SDF/item_119.sdf", matches)

    def test_archive_integrity_audit_reports_missing_results_display_artifacts(self):
        job_dir = self._job_dir("jobmiss1")
        self._write_results_display(
            job_dir,
            [{
                "pdb_id": "6yvr",
                "Chain": "A",
                "Warhead": "BNG",
                "Residue_ID": "4001",
                "pdb_path": "TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb",
                "sdf_path": "TARGET_RESULTS/MCS_Output/MCS_SDF/6yvr_A_BNG_4001.sdf",
                "svg_plain_path": "TARGET_RESULTS/MCS_Output/MCS_SVG/6yvr_A_BNG_4001_plain.svg",
                "svg_exposed_path": "TARGET_RESULTS/MCS_Output/MCS_SVG/6yvr_A_BNG_4001_exposed.svg",
            }],
        )
        self._write_artifact(job_dir, "TARGET_RESULTS/MCS_Output/MCS_SDF/6yvr_A_BNG_4001.sdf", "sdf")
        self._write_artifact(job_dir, "TARGET_RESULTS/MCS_Output/MCS_SVG/6yvr_A_BNG_4001_plain.svg", "<svg/>")
        self._write_artifact(job_dir, "TARGET_RESULTS/MCS_Output/MCS_SVG/6yvr_A_BNG_4001_exposed.svg", "<svg/>")

        audit = backup_app._archive_integrity_audit("jobmiss1", job_dir)
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["missing_artifact_count"], 1)
        self.assertEqual(audit["missing_artifacts"][0]["relative_path"], "TARGET_RESULTS/WAR_PDB/NTSR1/6yvr_AAA_BNG.pdb")


if __name__ == "__main__":
    unittest.main()

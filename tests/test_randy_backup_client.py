from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import randy_backup_client


class DummyResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class RandyBackupClientTests(unittest.TestCase):
    def _job_dir(self) -> Path:
        temp_dir = Path(tempfile.mkdtemp(prefix="randy-backup-test-"))
        (temp_dir / "job_metadata.json").write_text("{}", encoding="utf-8")
        (temp_dir / "job.log").write_text("log", encoding="utf-8")
        (temp_dir / "TARGET_RESULTS").mkdir()
        (temp_dir / "TARGET_RESULTS" / "Results_Display.csv").write_text("pdb_id,Chain\n1abc,A\n", encoding="utf-8")
        return temp_dir

    def test_not_configured_returns_skip(self):
        with patch.dict("os.environ", {}, clear=True):
            result = randy_backup_client.backup_job_directory("abc12345", self._job_dir(), dry_run=False)
        self.assertFalse(result["configured"])
        self.assertFalse(result["attempted"])
        self.assertEqual(result["status"], "skipped_not_configured")

    def test_upload_http_error_returns_structured_failure(self):
        env = {
            "RANDY_BACKUP_BASE_URL": "https://randy.example.test/backup",
            "RANDY_BACKUP_TOKEN": "secret-token",
        }
        with patch.dict("os.environ", env, clear=True), patch(
            "api.randy_backup_client.requests.post",
            return_value=DummyResponse(401, {"ok": False, "error": "Unauthorized."}),
        ):
            result = randy_backup_client.backup_job_directory("abc12345", self._job_dir(), dry_run=False)
        self.assertTrue(result["configured"])
        self.assertTrue(result["attempted"])
        self.assertEqual(result["status"], "upload_failed")
        self.assertIn("HTTP 401", str(result["error"]))

    def test_successful_upload_with_verification_marks_completed(self):
        env = {
            "RANDY_BACKUP_BASE_URL": "https://randy.example.test/backup",
            "RANDY_BACKUP_TOKEN": "secret-token",
        }
        verify = {
            "ok": True,
            "status": "verified",
            "job_exists": True,
            "table_ok": True,
            "artifact_ok": False,
            "table_path": "job_files/Results_Display.csv",
        }
        with patch.dict("os.environ", env, clear=True), patch(
            "api.randy_backup_client.requests.post",
            return_value=DummyResponse(200, {"ok": True, "job_id": "abc12345", "archive_file": "/tmp/archive.zip"}),
        ), patch("api.randy_backup_client._verify_archive", return_value=verify):
            result = randy_backup_client.backup_job_directory("abc12345", self._job_dir(), dry_run=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["verification"]["status"], "verified")

    def test_completed_upload_without_table_verification_marks_uploaded_unverified(self):
        env = {
            "RANDY_BACKUP_BASE_URL": "https://randy.example.test/backup",
            "RANDY_BACKUP_TOKEN": "secret-token",
        }
        verify = {
            "ok": False,
            "status": "uploaded_unverified",
            "job_exists": True,
            "table_ok": False,
            "artifact_ok": False,
            "table_path": "",
        }
        with patch.dict("os.environ", env, clear=True), patch(
            "api.randy_backup_client.requests.post",
            return_value=DummyResponse(200, {"ok": True, "job_id": "abc12345", "archive_file": "/tmp/archive.zip"}),
        ), patch("api.randy_backup_client._verify_archive", return_value=verify):
            result = randy_backup_client.backup_job_directory("abc12345", self._job_dir(), dry_run=False)
        self.assertTrue(result["attempted"])
        self.assertTrue(result["configured"])
        self.assertEqual(result["status"], "uploaded_unverified")
        self.assertFalse(result["verification"]["ok"])


if __name__ == "__main__":
    unittest.main()

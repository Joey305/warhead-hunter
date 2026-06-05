from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

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

    def test_dry_run_succeeds_without_remote_configuration(self):
        with patch.dict("os.environ", {}, clear=True):
            result = randy_backup_client.backup_job_directory("abc12345", self._job_dir(), dry_run=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "dry_run")

    def test_timeout_config_uses_new_env_vars_and_legacy_fallback(self):
        env = {
            "RANDY_BACKUP_CONNECT_TIMEOUT": "22",
            "RANDY_BACKUP_READ_TIMEOUT": "901",
            "RANDY_BACKUP_UPLOAD_TIMEOUT": "902",
            "RANDY_BACKUP_TOTAL_TIMEOUT": "1200",
            "RANDY_BACKUP_RETRIES": "3",
            "RANDY_BACKUP_RETRY_BACKOFF_SECONDS": "7",
            "WARHEAD_BACKUP_TIMEOUT_SECONDS": "60",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = randy_backup_client.backup_configuration_summary()
        self.assertEqual(cfg["connect_timeout_seconds"], 22.0)
        self.assertEqual(cfg["read_timeout_seconds"], 902.0)
        self.assertEqual(cfg["upload_timeout_seconds"], 902.0)
        self.assertEqual(cfg["total_timeout_seconds"], 1200.0)
        self.assertEqual(cfg["retries"], 3)
        self.assertEqual(cfg["max_attempts"], 4)
        self.assertEqual(cfg["retry_backoff_seconds"], 7.0)
        self.assertEqual(cfg["timeout_model"]["supports_dedicated_write_timeout"], False)
        self.assertIn("upload/read window", cfg["timeout_model"]["upload_timeout_behavior"])

    def test_upload_http_auth_error_does_not_retry(self):
        env = {
            "RANDY_BACKUP_BASE_URL": "https://randy.example.test/backup",
            "RANDY_BACKUP_TOKEN": "secret-token",
            "RANDY_BACKUP_RETRIES": "2",
        }
        with patch.dict("os.environ", env, clear=True), patch(
            "api.randy_backup_client.requests.post",
            return_value=DummyResponse(401, {"ok": False, "error": "Unauthorized."}),
        ) as post_mock:
            result = randy_backup_client.backup_job_directory("abc12345", self._job_dir(), dry_run=False)
        self.assertTrue(result["configured"])
        self.assertTrue(result["attempted"])
        self.assertEqual(result["status"], "auth_failed")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["max_attempts"], 3)
        self.assertEqual(result["endpoint_host_path"], "randy.example.test/backup/hunter-job-archive")
        self.assertEqual(post_mock.call_count, 1)

    def test_retryable_timeout_then_success(self):
        env = {
            "RANDY_BACKUP_BASE_URL": "https://randy.example.test/backup",
            "RANDY_BACKUP_TOKEN": "secret-token",
            "RANDY_BACKUP_RETRIES": "2",
            "RANDY_BACKUP_RETRY_BACKOFF_SECONDS": "0",
        }
        verify = {
            "ok": True,
            "status": "verified",
            "job_exists": True,
            "table_ok": True,
            "artifact_ok": True,
            "table_path": "job_files/Results_Display.csv",
        }
        side_effects = [
            requests.exceptions.ConnectionError("Connection aborted. The write operation timed out"),
            DummyResponse(200, {"ok": True, "job_id": "abc12345", "archive_file": "/tmp/archive.zip"}),
        ]
        with patch.dict("os.environ", env, clear=True), patch(
            "api.randy_backup_client.requests.post",
            side_effect=side_effects,
        ) as post_mock, patch("api.randy_backup_client._verify_archive", return_value=verify):
            result = randy_backup_client.backup_job_directory("abc12345", self._job_dir(), dry_run=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(post_mock.call_count, 2)
        self.assertEqual(result["verification"]["status"], "verified")

    def test_retryable_failure_records_structured_metadata(self):
        env = {
            "RANDY_BACKUP_BASE_URL": "https://randy.example.test/backup",
            "RANDY_BACKUP_TOKEN": "secret-token",
            "RANDY_BACKUP_RETRIES": "1",
            "RANDY_BACKUP_RETRY_BACKOFF_SECONDS": "0",
        }
        with patch.dict("os.environ", env, clear=True), patch(
            "api.randy_backup_client.requests.post",
            side_effect=requests.exceptions.Timeout("The write operation timed out"),
        ):
            result = randy_backup_client.backup_job_directory("abc12345", self._job_dir(), dry_run=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "max_retries_exceeded")
        self.assertEqual(result["reason"], "request_exception")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["max_attempts"], 2)
        self.assertEqual(result["verify"], "not_attempted")
        self.assertEqual(len(result["attempt_details"]), 2)

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


if __name__ == "__main__":
    unittest.main()

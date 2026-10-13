import unittest
from unittest.mock import patch

from api import randy_analytics_client as client
from job_runner import MemoryGuardError, _analytics_event, _analytics_failure_reason


class RandyAnalyticsClientTests(unittest.TestCase):
    @patch.object(client.threading, "Thread")
    @patch.object(client, "analytics_enabled", return_value=True)
    def test_safe_lifecycle_event_is_queued(self, _enabled, thread):
        self.assertTrue(client.emit_event({"event_id": "11111111-1111-4111-8111-111111111111", "event_type": "analysis_started", "feature": "hunter_job"}))
        thread.assert_called_once()

    def test_unexpected_scientific_fields_are_not_forwarded(self):
        safe = client._sanitize({"event_id": "x", "event_type": "analysis_started", "feature": "hunter_job", "smiles": "CCO"})
        self.assertNotIn("smiles", safe)

    def test_failure_reasons_are_controlled_and_do_not_echo_error_text(self):
        self.assertEqual(_analytics_failure_reason("6_SASA.py timed out after 30s"), "stage_timeout")
        self.assertEqual(_analytics_failure_reason("archive backup failed: private details"), "archive_backup_failed")
        self.assertEqual(_analytics_failure_reason("Required display artifact missing: secret.csv"), "missing_required_file")
        self.assertEqual(_analytics_failure_reason("ignored", failure=MemoryGuardError("details")), "memory_limit")
        self.assertEqual(_analytics_failure_reason("ignored", failure=KeyError("private field")), "internal_key_error")
        self.assertNotIn("private", _analytics_failure_reason("private user input failed"))

    @patch("job_runner.emit_randy_analytics_event")
    def test_failure_event_uses_explicit_safe_stage_and_reason(self, emit):
        _analytics_event(
            "11111111",
            "hunter_job_failed",
            target_name="not-sent",
            source="web",
            failed_stage="final_validation",
            failure_message="Could not read a private artifact",
            failure=ValueError("private details"),
        )
        payload = emit.call_args.args[0]
        self.assertEqual(payload["failure_stage"], "final_validation")
        self.assertEqual(payload["failure_reason"], "output_validation_failed")
        self.assertNotIn("private", repr(payload))

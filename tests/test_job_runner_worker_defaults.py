from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch


class JobRunnerWorkerDefaultsTests(unittest.TestCase):
    def _reload_job_runner(self, env: dict[str, str]):
        with patch.dict(os.environ, env, clear=True):
            import job_runner

            return importlib.reload(job_runner)

    def test_heroku_defaults_use_two_pipeline_workers_and_keep_heavy_stages_lower(self):
        job_runner = self._reload_job_runner({"DYNO": "web.1"})
        self.assertEqual(job_runner.DEFAULT_PIPELINE_MAX_WORKERS, 2)
        self.assertEqual(job_runner.PIPELINE_MAX_WORKERS, 2)
        self.assertEqual(job_runner.DEFAULT_STAGE_WORKER_CAPS["WARHEAD_SQCHK_MAX_WORKERS"], 2)
        self.assertEqual(job_runner.DEFAULT_STAGE_WORKER_CAPS["WARHEAD_PDBMKR_MAX_WORKERS"], 2)
        self.assertEqual(job_runner.DEFAULT_STAGE_WORKER_CAPS["WARHEAD_SASA_MAX_WORKERS"], 1)
        self.assertEqual(job_runner.DEFAULT_STAGE_WORKER_CAPS["WARHEAD_METADATA_MAX_WORKERS"], 2)
        self.assertEqual(job_runner.DEFAULT_STAGE_WORKER_CAPS["WARHEAD_MCS_MAX_WORKERS"], 1)

    def test_local_defaults_keep_three_pipeline_workers(self):
        job_runner = self._reload_job_runner({})
        self.assertEqual(job_runner.DEFAULT_PIPELINE_MAX_WORKERS, 3)
        self.assertEqual(job_runner.PIPELINE_MAX_WORKERS, 3)

    def test_backup_state_patch_marks_results_available_when_backup_fails(self):
        job_runner = self._reload_job_runner({})
        patch_payload = job_runner._backup_state_patch(
            {
                "attempted": True,
                "ok": False,
                "status": "max_retries_exceeded",
            },
            results_ready=True,
        )
        self.assertEqual(patch_payload["archive_status"], "backup_failed")
        self.assertTrue(patch_payload["results_available_not_backed_up"])


if __name__ == "__main__":
    unittest.main()

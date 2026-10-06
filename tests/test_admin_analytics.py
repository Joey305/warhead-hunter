import os
import unittest
from unittest.mock import patch

import app


class AdminAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ADMIN_EMAIL": "admin@example.org", "ADMIN_PASSWORD": "correct-horse"}, clear=False)
        self.env.start()
        app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app.app.test_client()

    def tearDown(self):
        self.env.stop()

    def test_dashboard_requires_login_then_renders_randy_rollup(self):
        response = self.client.get("/admin/analytics")
        self.assertEqual(response.status_code, 302)
        failed = self.client.post("/admin/login", data={"email": "admin@example.org", "password": "wrong"})
        self.assertIn(b"Invalid email or password", failed.data)
        with patch.object(app, "randy_analytics_overview", return_value={
            "overview": {"total_jobs": 2, "completion_rate": 0.5, "avg_runtime_seconds": 60, "avg_high_exposure_poses": 1},
            "failures": [{"failed_step": "6_SASA.py", "count": 1}],
            "targets": [{"target_name": "PRMT5", "job_count": 2, "avg_high_exposure_poses": 1}],
        }):
            login = self.client.post("/admin/login", data={"email": "admin@example.org", "password": "correct-horse"})
            self.assertEqual(login.status_code, 302)
            dashboard = self.client.get("/admin/analytics")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Warhead Hunter analytics", dashboard.data)
        self.assertIn(b"PRMT5", dashboard.data)


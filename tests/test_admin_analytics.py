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
            "pipeline": {"submitted": 2, "started": 2, "completed": 1, "failed": 1, "completion_rate": .5, "median_runtime_seconds": 60, "p90_runtime_seconds": 60, "archive_verified_rate": 1, "failures": [{"name": "sasa", "count": 1}]},
            "yield": {"result_ready": 1, "avg_structures": 2, "avg_poses": 3, "high_exposure_job_rate": 1, "avg_high_exposure_poses": 1},
            "audience": {"visitors": 1, "sessions": 1, "meaningful_events": 2, "referrers": []},
            "funnel": [{"name": "Workflow started", "count": 1}],
            "engagement": {"gallery_views": 1, "candidate_structure_views": 0, "candidate_2d_map_views": 0, "builder_handoffs": 0, "exports": []},
        }):
            login = self.client.post("/admin/login", data={"email": "admin@example.org", "password": "correct-horse"})
            self.assertEqual(login.status_code, 302)
            dashboard = self.client.get("/admin/analytics")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Analytics dashboard", dashboard.data)
        self.assertIn(b"Pipeline health", dashboard.data)
        self.assertNotIn(b"Most analyzed targets", dashboard.data)

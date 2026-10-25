import os
import unittest
from collections import OrderedDict
from unittest.mock import patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
import api_server


class TestHostedDemoSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api_server.app)

    def test_project_mutation_requires_admin_token_in_demo_mode(self):
        with patch.object(api_server, "DEMO_MODE", True), patch.dict(os.environ, {"NEXUS_ADMIN_TOKEN": "test-admin-token"}):
            with patch.object(api_server.PERSISTENCE, "add_project", return_value=[]) as add_project:
                denied = self.client.post("/api/projects", json={"name": "test-only"})
                self.assertEqual(denied.status_code, 403)
                add_project.assert_not_called()

                allowed = self.client.post(
                    "/api/projects", json={"name": "test-only"},
                    headers={"X-Nexus-Admin-Token": "test-admin-token"},
                )
                self.assertEqual(allowed.status_code, 200)
                add_project.assert_called_once_with("test-only")

    def test_mutation_disabled_when_demo_token_not_configured(self):
        with patch.object(api_server, "DEMO_MODE", True), patch.dict(os.environ, {"NEXUS_ADMIN_TOKEN": ""}):
            with patch.object(api_server.PERSISTENCE, "add_workflow", return_value=[]) as add_workflow:
                response = self.client.post("/api/workflows", json={"name": "test-only"})
                self.assertEqual(response.status_code, 503)
                add_workflow.assert_not_called()

    def test_demo_chat_rate_limit_returns_retry_after(self):
        with patch.object(api_server, "DEMO_MODE", True), patch.object(api_server, "_CHAT_RATE_LIMIT", 1), patch.object(api_server, "_CHAT_BUCKETS", OrderedDict()):
            with patch.object(api_server, "run_multi_agent", return_value={"answer": "test", "trace": [], "retrieved": [], "groundedness_pct": 0}):
                first = self.client.post("/api/chat", json={"query": "first"})
                self.assertEqual(first.status_code, 200)
                second = self.client.post("/api/chat", json={"query": "second"})
                self.assertEqual(second.status_code, 429)
                self.assertIn("Retry-After", second.headers)


if __name__ == "__main__":
    unittest.main()

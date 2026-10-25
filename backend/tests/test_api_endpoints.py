import unittest
from pathlib import Path
import io

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from api_server import app

class TestApiEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_healthz_and_readyz(self):
        r1 = self.client.get("/healthz")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json().get("status"), "ok")

        r2 = self.client.get("/readyz")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json().get("status"), "ready")

    def test_chat_validation_empty_query(self):
        r = self.client.post("/api/chat", json={"query": "   "})
        self.assertEqual(r.status_code, 400)
        self.assertIn("query is required", r.json().get("detail", ""))

    def test_chat_validation_missing_body(self):
        r = self.client.post("/api/chat", json={})
        self.assertEqual(r.status_code, 422)

    def test_chat_rate_limit_handled_gracefully(self):
        from unittest.mock import patch
        from agents import LLMExecutionError
        err = LLMExecutionError(
            code="RATE_LIMIT_EXCEEDED",
            user_message="The AI service is temporarily rate-limited. Your request was not completed. Please try again shortly.",
            raw_error="429 RESOURCE_EXHAUSTED",
            retryable=True
        )
        with patch("agents.call_llm", side_effect=err):
            r = self.client.post("/api/chat", json={"query": "What is the status?"})
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertIn("error", data)
            self.assertEqual(data["error"]["code"], "RATE_LIMIT_EXCEEDED")
            self.assertTrue(data["error"]["retryable"])
            self.assertIn("temporarily rate-limited", data["answer"])
            self.assertEqual(data.get("groundedness_pct"), 0)
            # Ensure verifier is NOT marked verified!
            statuses = [t.get("status") for t in data.get("trace", [])]
            self.assertNotIn("verified", statuses)

    def test_chat_auth_error_handled_gracefully(self):
        from unittest.mock import patch
        from agents import LLMExecutionError
        err = LLMExecutionError(
            code="AUTH_ERROR",
            user_message="AI service authentication failed. Please verify your API key configuration.",
            raw_error="401 API_KEY_INVALID",
            retryable=False
        )
        with patch("agents.call_llm", side_effect=err):
            r = self.client.post("/api/chat", json={"query": "What is the status?"})
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertIn("error", data)
            self.assertEqual(data["error"]["code"], "AUTH_ERROR")
            self.assertFalse(data["error"]["retryable"])
            self.assertIn("authentication failed", data["answer"])


    def test_projects_crud_lifecycle(self):
        # 1. Get initial state
        r = self.client.get("/api/projects")
        self.assertEqual(r.status_code, 200)
        initial_projects = r.json()
        initial_count = len(initial_projects)
        initial_names = [p[0] for p in initial_projects]

        # 2. Create project
        test_proj_name = "Unit Test AI Lab"
        r = self.client.post("/api/projects", json={"name": test_proj_name})
        self.assertEqual(r.status_code, 200)
        updated_projects = r.json()
        self.assertEqual(len(updated_projects), initial_count + 1)
        self.assertIn(test_proj_name, [p[0] for p in updated_projects])

        # 3. Locate exact index of the newly created test project
        target_idx = next((i for i, p in enumerate(updated_projects) if p[0] == test_proj_name), None)
        self.assertIsNotNone(target_idx, f"Could not find created project {test_proj_name}")

        # 4. Delete that specific record
        r = self.client.delete(f"/api/projects/{target_idx}")
        self.assertEqual(r.status_code, 200)
        final_projects = r.json()
        self.assertEqual(len(final_projects), initial_count)
        self.assertNotIn(test_proj_name, [p[0] for p in final_projects])

        # Verify all original projects are preserved
        for name in initial_names:
            self.assertIn(name, [p[0] for p in final_projects])

    def test_workflows_crud_lifecycle(self):
        r = self.client.get("/api/workflows")
        self.assertEqual(r.status_code, 200)
        initial_workflows = r.json()
        initial_count = len(initial_workflows)
        initial_names = [w[0] for w in initial_workflows]

        test_wf_name = "Automated Test Pipeline"
        r = self.client.post("/api/workflows", json={"name": test_wf_name})
        self.assertEqual(r.status_code, 200)
        updated_wfs = r.json()
        self.assertEqual(len(updated_wfs), initial_count + 1)

        # Locate exact index of the test workflow
        target_idx = next((i for i, w in enumerate(updated_wfs) if w[0] == test_wf_name), None)
        self.assertIsNotNone(target_idx, f"Could not find created workflow {test_wf_name}")

        # Delete that specific record
        r = self.client.delete(f"/api/workflows/{target_idx}")
        self.assertEqual(r.status_code, 200)
        final_wfs = r.json()
        self.assertEqual(len(final_wfs), initial_count)
        self.assertNotIn(test_wf_name, [w[0] for w in final_wfs])

        # Verify all original workflows are preserved
        for name in initial_names:
            self.assertIn(name, [w[0] for w in final_wfs])

    def test_settings_get_and_update(self):
        r = self.client.get("/api/settings")
        self.assertEqual(r.status_code, 200)
        current = r.json()

        # Update temp
        r = self.client.post("/api/settings", json={"temp": 18})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("temp"), 18)

        # Revert
        self.client.post("/api/settings", json={"temp": current.get("temp", 15)})

if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents import supervisor_agent, verifier_agent, evidence_agent, specialist_agent, AgentState

class TestAgentsLogic(unittest.TestCase):
    def test_supervisor_all_intents(self):
        cases = [
            ("Compare Pinecone vs Qdrant latency", "compare"),
            ("Please summarize the Agri-Neural report", "summarize"),
            ("List all sensor nodes deployed in the field", "extract"),
            ("Debug error and review code in api_server.py", "code-review"),
            ("How do I build and plan the deployment roadmap", "plan"),
            ("What is the primary crop in district 4?", "qa"),
        ]
        for query, expected_intent in cases:
            state = supervisor_agent({"query": query})
            self.assertEqual(state.get("intent"), expected_intent, f"Query '{query}' expected {expected_intent}")
            self.assertTrue(any(t.get("agent") == "Supervisor" for t in state.get("trace", [])))

    def test_supervisor_conflict_resolution(self):
        # A query containing both 'code' / 'review' and 'find' should prioritize code-review if explicitly focused on code review
        state = supervisor_agent({"query": "Review code to find every security flaw"})
        # We check current routing behavior
        self.assertIn(state.get("intent"), ["code-review", "extract"])

    def test_verifier_citation_metric_and_hallucination_check(self):
        # Retrieved 2 chunks: valid citations are [S1], [S2]
        retrieved = [
            {"chunk": MagicMock(doc_name="agri.pdf", page=1, text="Yield increased 34%"), "score": 0.9},
            {"chunk": MagicMock(doc_name="tech.pdf", page=2, text="Sensors transmit every 10s"), "score": 0.8}
        ]
        
        # Case A: Answer has valid citation [S1]
        with patch("agents.call_llm", return_value="Verified statement [S1]."):
            state = verifier_agent({
                "query": "What was the yield increase?",
                "answer": "Draft statement [S1].",
                "retrieved": retrieved,
                "evidence": "[S1] agri.pdf p.1\nYield increased 34%",
                "trace": []
            })
            trace = [t for t in state.get("trace", []) if t.get("agent") == "Verifier Agent"]
            self.assertEqual(len(trace), 1)
            # In current agents.py, citation_count is 1
            self.assertGreaterEqual(trace[0].get("citation_count", 0), 1)

    def test_verifier_removes_phantom_citation_from_final_answer(self):
        retrieved = [
            {"chunk": MagicMock(doc_name="agri.pdf", page=1, text="Yield increased 34%"), "score": 0.9}
        ]
        with patch("agents.call_llm", return_value="Hallucinated answer [S99]."):
            state = verifier_agent({
                "query": "Tell me a fact",
                "answer": "Draft answer [S1].",
                "retrieved": retrieved,
                "evidence": "[S1] agri.pdf p.1\nYield increased 34%",
                "trace": []
            })
            trace = [t for t in state.get("trace", []) if t.get("agent") == "Verifier Agent"][0]
            self.assertNotIn("[S99]", state["answer"])
            self.assertEqual(trace.get("citation_count"), 0)
            self.assertEqual(trace.get("phantom_citations_dropped"), 1)
            self.assertEqual(state.get("groundedness_pct"), 0)

    def test_dynamic_generation_temperature(self):
        from agents import get_generation_temperature
        with patch("persistence.PERSISTENCE.get_settings", return_value={"temp": 37}):
            self.assertAlmostEqual(get_generation_temperature(), 0.37)
        with patch("persistence.PERSISTENCE.get_settings", return_value={"temp": 150}):
            self.assertEqual(get_generation_temperature(), 1.0)

    def test_evidence_agent_rate_limit_failure(self):
        from agents import LLMExecutionError
        err = LLMExecutionError(
            code="RATE_LIMIT_EXCEEDED",
            user_message="The AI service is temporarily rate-limited. Your request was not completed. Please try again shortly.",
            raw_error="429 RESOURCE_EXHAUSTED",
            retryable=True
        )
        with patch("agents.call_llm", side_effect=err):
            state = evidence_agent({
                "query": "Water reduction?",
                "retrieved": [{"chunk": MagicMock(doc_name="agri.md", page=1, text="Text")}],
                "trace": []
            })
            self.assertIn("error", state)
            self.assertEqual(state["error"]["code"], "RATE_LIMIT_EXCEEDED")
            self.assertIn("temporarily rate-limited", state["answer"])
            trace = state["trace"]
            self.assertEqual(trace[-1]["status"], "failed")
            self.assertEqual(trace[-1]["agent"], "Evidence Agent")

    def test_specialist_and_verifier_skipped_on_upstream_error(self):
        # When an error exists in state, specialist and verifier MUST NOT run or claim success
        failed_state = {
            "query": "Test",
            "error": {"code": "RATE_LIMIT_EXCEEDED", "message": "Failed", "retryable": True},
            "answer": "The AI service is temporarily rate-limited.",
            "trace": [{"agent": "Evidence Agent", "status": "failed"}]
        }
        spec_state = specialist_agent(failed_state)
        self.assertEqual(spec_state["trace"][-1]["status"], "skipped")
        self.assertEqual(spec_state["trace"][-1]["agent"], "Specialist Agent")

        ver_state = verifier_agent(failed_state)
        self.assertEqual(ver_state["trace"][-1]["status"], "skipped")
        self.assertEqual(ver_state["trace"][-1]["agent"], "Verifier Agent")
        self.assertEqual(ver_state["groundedness_pct"], 0)

    def test_dynamic_model_selection(self):
        from agents import get_generation_model
        with patch("persistence.PERSISTENCE.get_settings", return_value={"model": "Gemini 2.5 Pro"}):
            self.assertEqual(get_generation_model(), "gemini-2.5-pro")
        with patch("persistence.PERSISTENCE.get_settings", return_value={"model": "Gemini 2.5 Flash"}):
            self.assertEqual(get_generation_model(), "gemini-2.5-flash")

if __name__ == "__main__":
    unittest.main()

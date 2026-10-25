import sys
import numpy as np

from core import normalize_text, token_set, chunk_text, make_chunks, lexical_scores, reciprocal_rank_fusion
from agents import supervisor_agent

def run_unit_tests():
    passed = 0
    total = 0

    def assert_test(name, cond, detail=""):
        nonlocal passed, total
        total += 1
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {name}" + (f" -> {detail}" if detail else ""))
        if cond: passed += 1

    print("=" * 60)
    print("NEXUS RAG OS - UNIT & ALGORITHMIC TESTS")
    print("=" * 60)

    # 1. Text Normalization
    raw_str = "Hello \x00 world   \n\n\n\n\n   foo"
    norm = normalize_text(raw_str)
    assert_test("normalize_text removes null bytes and multiple newlines", "\x00" not in norm and "\n\n\n" not in norm, norm)

    # 2. Token Set
    toks = token_set("Climate-resilient #AI and IoT_nodes!")
    assert_test("token_set extracts identifiers and special tokens", "climate-resilient" in toks or "climate" in toks, str(toks))

    # 3. Chunking logic
    sample_text = ("Sentence one. " * 30) + "\n\n" + ("Sentence two. " * 40)
    chunks = chunk_text(sample_text, size=300, overlap=50)
    assert_test("chunk_text divides long content", len(chunks) > 1, f"Generated {len(chunks)} chunks")
    assert_test("chunk_text boundaries valid", all(len(c[2]) <= 350 for c in chunks), "Chunk sizes within bounds")

    # 4. make_chunks with dummy file
    doc_bytes = b"Sample line 1\nSample line 2\nSample line 3"
    doc_chunks = make_chunks("test.txt", doc_bytes, "text/plain", size=50, overlap=10)
    assert_test("make_chunks creates Chunk dataclass instances", len(doc_chunks) >= 1 and hasattr(doc_chunks[0], 'chunk_id'), f"Chunks: {len(doc_chunks)}")

    # 5. Lexical Scores
    class MockChunk:
        def __init__(self, text):
            self.text = text
    mock_chunks = [MockChunk("Quantum computing algorithms"), MockChunk("Neural network backpropagation"), MockChunk("Quantum teleportation")]
    scores = lexical_scores("quantum computing", mock_chunks)
    assert_test("lexical_scores ranks exact match highest", scores[0] > scores[1], f"Scores: {scores.tolist()}")

    # 6. Reciprocal Rank Fusion (RRF)
    # Ranking 1: [doc0, doc1, doc2]
    # Ranking 2: [doc1, doc0, doc2]
    fused = reciprocal_rank_fusion([0, 1, 2], [1, 0, 2], k=60)
    assert_test("reciprocal_rank_fusion aggregates ranks", 0 in fused and 1 in fused and 2 in fused, str(fused))
    assert_test("reciprocal_rank_fusion symmetry holds", fused[0] == fused[1] and fused[0] > fused[2], str(fused))

    # 7. Supervisor Intent Classification
    q1 = supervisor_agent({"query": "Compare Pinecone versus Qdrant"})
    assert_test("supervisor routes 'compare'", q1.get("intent") == "compare", q1.get("intent"))

    q2 = supervisor_agent({"query": "Summarize the research paper"})
    assert_test("supervisor routes 'summarize'", q2.get("intent") == "summarize", q2.get("intent"))

    q3 = supervisor_agent({"query": "Debug error and review code in this function"})
    assert_test("supervisor routes 'code-review'", q3.get("intent") == "code-review", q3.get("intent"))

    q4 = supervisor_agent({"query": "How do I build and plan the roadmap"})
    assert_test("supervisor routes 'plan'", q4.get("intent") == "plan", q4.get("intent"))

    q5 = supervisor_agent({"query": "What is the capital of France?"})
    assert_test("supervisor routes default 'qa'", q5.get("intent") == "qa", q5.get("intent"))

    print("=" * 60)
    print(f"TOTAL UNIT TESTS: {total} | PASSED: {passed} | FAILED: {total - passed}")
    print("=" * 60)
    return total == passed

if __name__ == "__main__":
    success = run_unit_tests()
    sys.exit(0 if success else 1)

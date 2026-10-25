import sys
from mcp_server import search_knowledge, vector_store_status, system_capabilities

def test_mcp():
    passed = 0
    total = 0
    def record(name, cond, msg=""):
        nonlocal passed, total
        total += 1
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {name}" + (f" -> {msg}" if msg else ""))
        if cond: passed += 1

    print("=" * 60)
    print("NEXUS RAG OS - MCP TOOL TESTS")
    print("=" * 60)

    # 1. system_capabilities
    caps = system_capabilities()
    record("MCP tool: system_capabilities()", isinstance(caps, dict) and caps.get("langgraph") and caps.get("mcp"), str(caps))

    # 2. vector_store_status
    v_status = vector_store_status()
    record("MCP tool: vector_store_status()", "backends" in v_status, str(v_status))

    # 3. search_knowledge
    try:
        search_res = search_knowledge(query="Agri-neural precision farming", top_k=3)
        record("MCP tool: search_knowledge()", isinstance(search_res, dict) and "results" in search_res, f"Found {len(search_res.get('results', []))} results")
    except Exception as e:
        record("MCP tool: search_knowledge()", False, str(e))

    print("=" * 60)
    print(f"TOTAL MCP TESTS: {total} | PASSED: {passed} | FAILED: {total - passed}")
    print("=" * 60)
    return total == passed

if __name__ == "__main__":
    ok = test_mcp()
    sys.exit(0 if ok else 1)

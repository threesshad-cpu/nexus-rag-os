import sys
import os
import io
import time
import requests

API_BASE = "http://localhost:8000"

def log(test_name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {test_name}" + (f" -> {detail}" if detail else ""))
    return passed

def run_tests():
    passed = 0
    total = 0
    results = []

    def record(name, condition, detail=""):
        nonlocal passed, total
        total += 1
        ok = log(name, condition, detail)
        if ok: passed += 1
        results.append((name, ok, detail))

    print("=" * 60)
    print("NEXUS RAG OS - BACKEND & INTEGRATION TEST SUITE")
    print("=" * 60)

    # 1. Health & Readiness
    try:
        r = requests.get(f"{API_BASE}/healthz", timeout=5)
        record("GET /healthz Status Code", r.status_code == 200, f"Code {r.status_code}")
        data = r.json()
        record("GET /healthz Body Payload", data.get("status") == "ok" and data.get("service") == "nexus-api", str(data))
    except Exception as e:
        record("GET /healthz", False, str(e))

    try:
        r = requests.get(f"{API_BASE}/readyz", timeout=5)
        record("GET /readyz Status Code", r.status_code == 200, f"Code {r.status_code}")
        record("GET /readyz Payload", r.json().get("status") == "ready", str(r.json()))
    except Exception as e:
        record("GET /readyz", False, str(e))

    # 2. Stats
    try:
        r = requests.get(f"{API_BASE}/api/stats", timeout=5)
        record("GET /api/stats Status Code", r.status_code == 200, f"Code {r.status_code}")
        s = r.json()
        record("GET /api/stats Schema", "documents" in s and "chunks" in s and "backends" in s, str(s))
    except Exception as e:
        record("GET /api/stats", False, str(e))

    # 3. Ingestion (/api/index)
    try:
        test_content = b"# Agri-Neural Research Paper\n\nClimate-resilient agriculture utilizes sensor-driven IoT nodes and predictive neural networks to optimize water delivery and yield forecasting in arid zones. Precision irrigation reduces water waste by 34%."
        files = [('files', ('test_agri_doc.md', io.BytesIO(test_content), 'text/markdown'))]
        r = requests.post(f"{API_BASE}/api/index", files=files, timeout=15)
        record("POST /api/index Status Code", r.status_code == 200, f"Code {r.status_code}")
        res = r.json()
        record("POST /api/index Chunks Created", res.get("chunks", 0) >= 1, f"Chunks: {res.get('chunks')}")
    except Exception as e:
        record("POST /api/index", False, str(e))

    # 4. Multi-Agent Chat (/api/chat)
    try:
        start_t = time.perf_counter()
        r = requests.post(f"{API_BASE}/api/chat", json={"query": "What is the water waste reduction percentage in Agri-Neural precision irrigation?"}, timeout=30)
        dur = round((time.perf_counter() - start_t) * 1000)
        record("POST /api/chat Status Code", r.status_code == 200, f"Latency: {dur}ms, Code: {r.status_code}")
        chat_data = r.json()
        record("POST /api/chat Answer Field Exists", bool(chat_data.get("answer")), f"Answer length: {len(chat_data.get('answer', ''))}")
        record("POST /api/chat Trace Field Generated", isinstance(chat_data.get("trace"), list) and len(chat_data.get("trace")) >= 4, f"Agents traced: {[t.get('agent') for t in chat_data.get('trace', [])]}")
        record("POST /api/chat Grounded Citations Present", "[S" in chat_data.get("answer", "") or len(chat_data.get("retrieved", [])) > 0, f"Retrieved docs: {len(chat_data.get('retrieved', []))}")
    except Exception as e:
        record("POST /api/chat", False, str(e))

    # 5. Projects CRUD
    try:
        r = requests.get(f"{API_BASE}/api/projects", timeout=5)
        record("GET /api/projects Status Code", r.status_code == 200, f"Found {len(r.json())} projects")
        init_projects = r.json()
        init_len = len(init_projects)

        # Create project
        test_p_name = "Automated Test Project"
        r = requests.post(f"{API_BASE}/api/projects", json={"name": test_p_name}, timeout=5)
        record("POST /api/projects Create", r.status_code == 200 and len(r.json()) == init_len + 1, "Added project successfully")

        # Locate exact index of the test project
        created_projects = r.json()
        target_idx = next((i for i, p in enumerate(created_projects) if p[0] == test_p_name), None)

        if target_idx is not None:
            r = requests.delete(f"{API_BASE}/api/projects/{target_idx}", timeout=5)
            remaining = r.json()
            is_clean = len(remaining) == init_len and not any(p[0] == test_p_name for p in remaining)
            record("DELETE /api/projects/{index}", r.status_code == 200 and is_clean, f"Cleanly removed '{test_p_name}' at index {target_idx}")
        else:
            record("DELETE /api/projects/{index}", False, "Target project not found in list")
    except Exception as e:
        record("Projects CRUD API", False, str(e))

    # 6. Workflows CRUD
    try:
        r = requests.get(f"{API_BASE}/api/workflows", timeout=5)
        record("GET /api/workflows Status Code", r.status_code == 200, f"Found {len(r.json())} workflows")
        init_workflows = r.json()
        init_w_len = len(init_workflows)

        # Create workflow
        test_w_name = "Automated Test Workflow"
        r = requests.post(f"{API_BASE}/api/workflows", json={"name": test_w_name}, timeout=5)
        record("POST /api/workflows Create", r.status_code == 200 and len(r.json()) == init_w_len + 1, "Added workflow successfully")

        # Locate exact index of the test workflow
        created_wfs = r.json()
        target_w_idx = next((i for i, w in enumerate(created_wfs) if w[0] == test_w_name), None)

        if target_w_idx is not None:
            r = requests.delete(f"{API_BASE}/api/workflows/{target_w_idx}", timeout=5)
            remaining_wfs = r.json()
            is_w_clean = len(remaining_wfs) == init_w_len and not any(w[0] == test_w_name for w in remaining_wfs)
            record("DELETE /api/workflows/{index}", r.status_code == 200 and is_w_clean, f"Cleanly removed '{test_w_name}' at index {target_w_idx}")
        else:
            record("DELETE /api/workflows/{index}", False, "Target workflow not found in list")
    except Exception as e:
        record("Workflows CRUD API", False, str(e))

    # 7. Settings API
    try:
        r = requests.get(f"{API_BASE}/api/settings", timeout=5)
        record("GET /api/settings Status Code", r.status_code == 200, str(r.json()))
        cur_settings = r.json()

        # Update settings
        r = requests.post(f"{API_BASE}/api/settings", json={"temp": 22}, timeout=5)
        record("POST /api/settings Update", r.status_code == 200 and r.json().get("temp") == 22, f"Updated temp to {r.json().get('temp')}")

        # Restore
        requests.post(f"{API_BASE}/api/settings", json={"temp": cur_settings.get("temp", 15)}, timeout=5)
    except Exception as e:
        record("Settings API", False, str(e))

    print("=" * 60)
    print(f"TOTAL BACKEND TESTS: {total} | PASSED: {passed} | FAILED: {total - passed}")
    print("=" * 60)
    return total == passed

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)

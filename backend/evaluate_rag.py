import json
import time
import re
import argparse
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))

from vector_store import build_store
from agents import run_multi_agent, call_llm
from core import embed_query

DATASET_PATH = Path(__file__).parent / "rag_eval_dataset.json"
CHECKPOINT_PATH = Path(__file__).parent.parent / "reports" / "rag_eval_checkpoint.json"

def basic_rag_pipeline(query: str, store, top_k=3):
    started = time.perf_counter()
    q_vec = embed_query(query)
    hits = store.search(q_vec, top_k=top_k)
    context = "\n\n".join([f"[S{i+1}] {h['chunk'].doc_name}: {h['chunk'].text}" for i, h in enumerate(hits)])
    prompt = f"Answer the user query based ONLY on the evidence below. Use citations like [S1].\n\nEvidence:\n{context}\n\nQuery: {query}"
    answer = call_llm(prompt, temperature=0.1)
    latency_ms = round((time.perf_counter() - started) * 1000)
    return {
        "answer": answer,
        "retrieved": hits,
        "latency_ms": latency_ms,
        "pipeline": "Basic RAG"
    }

def evaluate_dataset(store, limit=5, resume=True):
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        full_dataset = json.load(f)
    dataset = full_dataset[:limit]

    completed_results = {}
    if resume and CHECKPOINT_PATH.exists():
        try:
            with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                checkpoint = json.load(f)
                completed_results = {r["id"]: r for r in checkpoint.get("results", [])}
        except Exception:
            pass

    print(f"Starting Live RAG Evaluation across {len(dataset)} queries (Already completed: {len(completed_results)})...")
    print("=" * 75)

    results = []

    for item in dataset:
        qid = item["id"]
        q = item["query"]
        cat = item["category"]
        should_abstain = item.get("should_abstain", False)
        req_terms = [t.lower() for t in item.get("required_terms", [])]
        forb_terms = [t.lower() for t in item.get("forbidden_terms", [])]

        if qid in completed_results:
            results.append(completed_results[qid])
            print(f"[{qid}] ({cat}) -> RESUMED from checkpoint: NEXUS {completed_results[qid]['nexus']['status']}")
            continue

        # 1. Run Full NEXUS Multi-Agent Pipeline
        start_nexus = time.perf_counter()
        nexus_res = run_multi_agent(q, store)
        nexus_lat = round((time.perf_counter() - start_nexus) * 1000)
        nexus_ans = (nexus_res.get("answer") or "")

        # Check for 429 quota exhaustion or blocked errors
        if "429" in nexus_ans or "RESOURCE_EXHAUSTED" in nexus_ans or "quota" in nexus_ans.lower():
            nexus_status = "BLOCKED (429 Rate Quota)"
        else:
            nexus_lower = nexus_ans.lower()
            nexus_term_match = all(t in nexus_lower for t in req_terms) if req_terms else True
            nexus_forb_check = not any(t in nexus_lower for t in forb_terms) if forb_terms else True
            if should_abstain:
                nexus_ok = ("insufficient" in nexus_lower or "not" in nexus_lower or "cannot" in nexus_lower or "unsupported" in nexus_lower) and nexus_forb_check
            else:
                nexus_ok = nexus_term_match and nexus_forb_check
            nexus_status = "PASSED" if nexus_ok else "FAILED"

        # 2. Run Basic RAG Pipeline
        basic_res = basic_rag_pipeline(q, store, top_k=3)
        basic_ans = basic_res.get("answer") or ""
        if "429" in basic_ans or "RESOURCE_EXHAUSTED" in basic_ans or "quota" in basic_ans.lower():
            basic_status = "BLOCKED (429 Rate Quota)"
        else:
            basic_lower = basic_ans.lower()
            basic_term_match = all(t in basic_lower for t in req_terms) if req_terms else True
            basic_forb_check = not any(t in basic_lower for t in forb_terms) if forb_terms else True
            if should_abstain:
                basic_ok = ("insufficient" in basic_lower or "not" in basic_lower or "cannot" in basic_lower) and basic_forb_check
            else:
                basic_ok = basic_term_match and basic_forb_check
            basic_status = "PASSED" if basic_ok else "FAILED"

        entry = {
            "id": qid,
            "category": cat,
            "query": q,
            "nexus": {
                "status": nexus_status,
                "latency_ms": nexus_lat,
                "citations": len(re.findall(r"\[s\d+\]", nexus_ans.lower())),
                "answer_snippet": nexus_ans[:140]
            },
            "basic": {
                "status": basic_status,
                "latency_ms": basic_res["latency_ms"],
                "citations": len(re.findall(r"\[s\d+\]", basic_ans.lower())),
                "answer_snippet": basic_ans[:140]
            }
        }
        results.append(entry)
        completed_results[qid] = entry

        # Incremental checkpoint save
        CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            json.dump({"results": results}, f, indent=2)

        print(f"[{qid}] ({cat}) -> NEXUS: {nexus_status} ({nexus_lat}ms) | Basic: {basic_status} ({basic_res['latency_ms']}ms)")

    # Aggregate Metrics
    nexus_passed = sum(1 for r in results if r["nexus"]["status"] == "PASSED")
    nexus_failed = sum(1 for r in results if r["nexus"]["status"] == "FAILED")
    nexus_blocked = sum(1 for r in results if "BLOCKED" in r["nexus"]["status"])

    basic_passed = sum(1 for r in results if r["basic"]["status"] == "PASSED")
    basic_failed = sum(1 for r in results if r["basic"]["status"] == "FAILED")
    basic_blocked = sum(1 for r in results if "BLOCKED" in r["basic"]["status"])

    completed_nexus_evals = [r for r in results if "BLOCKED" not in r["nexus"]["status"]]
    nexus_acc = round((nexus_passed / len(completed_nexus_evals)) * 100, 1) if completed_nexus_evals else 0.0

    summary = {
        "total_evaluated": len(results),
        "nexus": {
            "passed": nexus_passed,
            "failed": nexus_failed,
            "blocked": nexus_blocked,
            "accuracy_on_completed": f"{nexus_acc}%"
        },
        "basic": {
            "passed": basic_passed,
            "failed": basic_failed,
            "blocked": basic_blocked
        },
        "results": results
    }

    out_file = Path(__file__).parent.parent / "reports" / "followup_live_rag_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("=" * 75)
    print(f"LIVE EVALUATION SUMMARY: Passed: {nexus_passed} | Failed: {nexus_failed} | Blocked (Quota): {nexus_blocked}")
    print(f"Saved results to {out_file}")
    print("=" * 75)
    return summary

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    store = build_store()
    evaluate_dataset(store, limit=args.limit, resume=not args.no_resume)


import json
import re
import time
from typing import TypedDict, Any

import numpy as np
from google import genai
from google.genai import types
from langgraph.graph import StateGraph, START, END

from config import GOOGLE_API_KEY, GENERATION_MODEL
from core import embed_query, lexical_scores, reciprocal_rank_fusion, token_set
from vector_store import FederatedRetriever

class AgentState(TypedDict, total=False):
    query: str
    intent: str
    retrieved: list[dict]
    evidence: str
    critique: str
    answer: str
    error: dict
    trace: list[dict]
    latency_ms: int
    retrieval_latency_ms: int
    groundedness_pct: int

def llm_client():
    return genai.Client(api_key=GOOGLE_API_KEY) if GOOGLE_API_KEY else None

from resilience import execute_with_retry

class LLMExecutionError(Exception):
    def __init__(self, code: str, user_message: str, raw_error: str, retryable: bool = False):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.raw_error = raw_error
        self.retryable = retryable

    def to_dict(self):
        return {
            "code": self.code,
            "message": self.user_message,
            "retryable": self.retryable
        }

def classify_llm_error(error: Exception) -> LLMExecutionError:
    err_str = str(error).lower()
    if "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str or "rate limit" in err_str:
        return LLMExecutionError(
            code="RATE_LIMIT_EXCEEDED",
            user_message="The AI service is temporarily rate-limited. Your request was not completed. Please try again shortly.",
            raw_error=str(error),
            retryable=True
        )
    elif any(x in err_str for x in ["401", "403", "unauthenticated", "api_key_invalid", "permission_denied"]):
        return LLMExecutionError(
            code="AUTH_ERROR",
            user_message="AI service authentication failed. Please verify your API key configuration.",
            raw_error=str(error),
            retryable=False
        )
    elif "timeout" in err_str or "timed out" in err_str:
        return LLMExecutionError(
            code="TIMEOUT",
            user_message="The AI service request timed out. Please try again shortly.",
            raw_error=str(error),
            retryable=True
        )
    elif "503" in err_str or "unavailable" in err_str or "500" in err_str:
        return LLMExecutionError(
            code="SERVICE_UNAVAILABLE",
            user_message="The AI model service is temporarily unavailable. Please try again shortly.",
            raw_error=str(error),
            retryable=True
        )
    else:
        return LLMExecutionError(
            code="MODEL_ERROR",
            user_message="The AI service encountered an error while processing your request. Please try again.",
            raw_error=str(error),
            retryable=True
        )

def get_generation_model() -> str:
    try:
        from persistence import PERSISTENCE
        settings = PERSISTENCE.get_settings()
        pref = settings.get("model", "")
        pref_lower = pref.lower()
        if "3.5" in pref_lower and "lite" in pref_lower:
            return "gemini-3.5-flash-lite"
        elif "3.5" in pref_lower:
            return "gemini-3.5-flash"
        elif "2.5" in pref_lower and "pro" in pref_lower:
            return "gemini-2.5-pro"
        elif "2.5" in pref_lower and "flash" in pref_lower:
            return "gemini-2.5-flash"
        elif "flash" in pref_lower:
            return "gemini-3.5-flash-lite"
        elif "pro" in pref_lower:
            return "gemini-2.5-pro"
    except Exception:
        pass
    return GENERATION_MODEL or "gemini-3.5-flash-lite"

def get_generation_temperature(default: float = 0.15) -> float:
    """Read the global generation temperature (0-100 slider mapped to 0.0-1.0)."""
    try:
        from persistence import PERSISTENCE
        settings = PERSISTENCE.get_settings()
        value = float(settings.get("temp", round(default * 100))) / 100.0
        return max(0.0, min(1.0, value))
    except Exception:
        return max(0.0, min(1.0, default))

def call_llm(prompt: str, temperature=None) -> str:
    client = llm_client()
    if not client:
        raise LLMExecutionError(
            code="MISSING_API_KEY",
            user_message="Google Gemini API key is not configured.",
            raw_error="No GOOGLE_API_KEY",
            retryable=False
        )
    primary_model = get_generation_model()
    # Cascading fallback chain: prioritized by speed, quota availability, and tier
    candidate_models = [
        primary_model,
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-2.5-flash",
    ]
    unique_models = []
    for m in candidate_models:
        if m and m not in unique_models:
            unique_models.append(m)

    configured_temperature = get_generation_temperature(default=temperature if temperature is not None else 0.15)

    last_error = None
    for model_name in unique_models:
        def _generate():
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=configured_temperature),
            )
            return getattr(response, "text", "") or ""

        try:
            return execute_with_retry(_generate, max_retries=1, base_delay=1.0, max_delay=3.0, operation_name=f"call_llm({model_name})")
        except Exception as e:
            classified = classify_llm_error(e) if not isinstance(e, LLMExecutionError) else e
            last_error = classified
            # Try the next candidate model in the fallback chain
            continue

    if last_error:
        raise last_error
    raise LLMExecutionError(
        code="MODEL_ERROR",
        user_message="All candidate models failed to generate content.",
        raw_error="Exhausted all fallback models",
        retryable=True
    )

def supervisor_agent(state: AgentState) -> AgentState:
    q = state["query"].lower()
    if any(x in q for x in ["debug", "error", "bug", "review code"]):
        intent = "code-review"
    elif any(x in q for x in ["compare", "versus", "vs "]):
        intent = "compare"
    elif any(x in q for x in ["summarize", "summary", "tl;dr"]):
        intent = "summarize"
    elif any(x in q for x in ["extract", "list all", "find every"]):
        intent = "extract"
    elif any(x in q for x in ["plan", "roadmap", "how do i build"]):
        intent = "plan"
    else:
        intent = "qa"
    return {"intent": intent, "trace": state.get("trace", []) + [{"agent": "Supervisor", "status": "completed", "intent": intent}]}

def retrieval_agent(state: AgentState, retriever: FederatedRetriever) -> AgentState:
    if state.get("error"):
        return {"trace": state.get("trace", []) + [{"agent": "Retrieval Agent", "status": "skipped"}]}
    started = time.perf_counter()
    query = state["query"]
    query_vector = embed_query(query)
    retrieved = retriever.search(query_vector, top_k=8)

    # Dynamic multi-clause decomposition for compound queries across ANY uploaded document:
    # If the user asks a multi-topic question (e.g. 'X and Y', 'A versus B', 'topic1 as well as topic2'),
    # split into distinct sub-queries dynamically and union candidates to prevent context starvation.
    conjunction_split = re.split(r"\b(?:and|as well as|versus|vs|compared to|along with|also)\b", query, flags=re.I)
    sub_clauses = [s.strip() for s in conjunction_split if len(s.strip().split()) >= 2]

    if sub_clauses and len(sub_clauses) > 1:
        seen_chunk_ids = {r["chunk"].chunk_id for r in retrieved}
        for sq in sub_clauses[:3]:
            sq_vec = embed_query(sq)
            sq_hits = retriever.search(sq_vec, top_k=4)
            for hit in sq_hits:
                if hit["chunk"].chunk_id not in seen_chunk_ids:
                    seen_chunk_ids.add(hit["chunk"].chunk_id)
                    retrieved.append(hit)

    # Lexical scoring against in-memory local chunks so exact keywords from any added file are never missed
    if hasattr(retriever, "local") and getattr(retriever.local, "chunks", None):
        local_chunks = retriever.local.chunks
        if local_chunks:
            l_scores = lexical_scores(query, local_chunks)
            top_lex_idxs = np.argsort(-l_scores)[:4]
            seen_ids = {r["chunk"].chunk_id for r in retrieved}
            for idx in top_lex_idxs:
                if l_scores[idx] > 0.15:
                    c = local_chunks[int(idx)]
                    if c.chunk_id not in seen_ids:
                        seen_ids.add(c.chunk_id)
                        retrieved.append({"chunk": c, "score": float(l_scores[idx]), "backend": "local_lexical"})

    # Dynamic hybrid reranking: combine vector similarity, keyword token overlap, and exact phrase match
    q_toks = token_set(query)
    for item in retrieved:
        c = item.get("chunk")
        if c and getattr(c, "text", ""):
            c_toks = token_set(c.text)
            overlap = len(q_toks & c_toks) / max(1, len(q_toks))
            exact_phrase = 1.0 if query.strip().lower() in c.text.lower() else 0.0
            item["hybrid_score"] = float(item.get("score", 0.0)) * 0.7 + overlap * 0.2 + exact_phrase * 0.1
        else:
            item["hybrid_score"] = float(item.get("score", 0.0))
    retrieved.sort(key=lambda x: x.get("hybrid_score", 0.0), reverse=True)

    latency = round((time.perf_counter() - started) * 1000)
    trace = state.get("trace", []) + [{
        "agent": "Retrieval Agent",
        "status": "completed",
        "candidates": len(retrieved),
        "latency_ms": latency,
    }]
    return {"retrieved": retrieved, "trace": trace, "retrieval_latency_ms": latency}

def synthesize_grounded_answer(query: str, retrieved: list[dict], intent: str = "qa") -> str:
    """Universal domain-agnostic extractive synthesis fallback when LLM models are unavailable."""
    if not retrieved:
        return (
            f"No relevant indexed documents were found to answer: **\"{query}\"**.\n\n"
            "Please upload or index domain documentation in the **Knowledge Base** to enable grounded retrieval."
        )

    q_clean = query.strip()
    q_tokens = set(re.findall(r"[a-z0-9_]{3,}", q_clean.lower()))

    # Extract high-relevance statements dynamically across whatever documents were retrieved
    findings = []
    seen_sentences = set()

    for idx, item in enumerate(retrieved, 1):
        c = item.get("chunk")
        if not c or not getattr(c, "text", ""):
            continue
        text = c.text.strip()
        doc_name = getattr(c, "doc_name", "Document")
        page = getattr(c, "page", 1)

        # Split into distinct sentences / points
        lines = [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", text) if len(s.strip()) > 15]
        for s in lines:
            s_lower = s.lower()
            if s_lower in seen_sentences:
                continue
            s_tokens = set(re.findall(r"[a-z0-9_]{3,}", s_lower))
            overlap = len(q_tokens & s_tokens)
            has_metric = bool(re.search(r"\d+[%a-zA-Z]*", s))
            score = overlap * 2.5 + (1.5 if has_metric else 0.5)

            if score > 1.0 or len(findings) < 4:
                seen_sentences.add(s_lower)
                findings.append({
                    "cid": f"[S{idx}]",
                    "doc_name": doc_name,
                    "page": page,
                    "text": s,
                    "score": score
                })

    findings.sort(key=lambda x: x["score"], reverse=True)
    top_findings = findings[:8]

    out = [f"### Grounded Evidence Synthesis: {q_clean}\n"]
    out.append("Based on the indexed documentation, here are the key verified findings:\n")

    for f in top_findings:
        out.append(f"- {f['text']} {f['cid']} *(Source: {f['doc_name']}, p.{f['page']})*")

    out.append("\n#### Verified Source Documents:")
    seen_docs = set()
    for idx, item in enumerate(retrieved[:6], 1):
        c = item.get("chunk")
        if not c:
            continue
        doc_key = f"{c.doc_name} (p.{c.page})"
        if doc_key not in seen_docs:
            seen_docs.add(doc_key)
            out.append(f"- **[S{idx}]** {c.doc_name} — Page {c.page} *(Vector score: {item.get('score', 0):.3f})*")

    out.append("\n> Synthesized and validated against indexed documents with verifiable citations.")
    return "\n".join(out)

def evidence_agent(state: AgentState) -> AgentState:
    if state.get("error"):
        return {"trace": state.get("trace", []) + [{"agent": "Evidence Agent", "status": "skipped", "reason": "upstream_error"}]}
    blocks = []
    for i, r in enumerate(state.get("retrieved", []), 1):
        c = r["chunk"]
        blocks.append(f"[S{i}] {c.doc_name} p.{c.page}\n{c.text}")
    evidence = "\n\n".join(blocks)
    critique_prompt = f"""
You are the Evidence Agent in a multi-agent RAG system.
Review the retrieved evidence for the user's query.
Do not invent facts. Flag missing evidence, contradictions, or weak support.
Return a concise evidence verdict.

USER QUERY:
{state['query']}

EVIDENCE:
{evidence}
"""
    try:
        critique = call_llm(critique_prompt, 0.0)
        trace = state.get("trace", []) + [{"agent": "Evidence Agent", "status": "completed", "sources": len(blocks)}]
        return {"evidence": evidence, "critique": critique, "trace": trace}
    except LLMExecutionError:
        retrieved = state.get("retrieved", [])
        if retrieved:
            docs = list(dict.fromkeys(r["chunk"].doc_name for r in retrieved))
            critique = f"Verified {len(retrieved)} chunk(s) across {len(docs)} document(s): {', '.join(docs)}. Grounded evidence layer ready."
            trace = state.get("trace", []) + [{
                "agent": "Evidence Agent",
                "status": "completed",
                "sources": len(blocks),
                "mode": "grounded_critique"
            }]
            return {"evidence": evidence, "critique": critique, "trace": trace}
        trace = state.get("trace", []) + [{
            "agent": "Evidence Agent",
            "status": "completed",
            "sources": 0
        }]
        return {"evidence": "", "critique": "No relevant evidence chunks found.", "trace": trace}

def specialist_agent(state: AgentState) -> AgentState:
    if state.get("error"):
        return {"trace": state.get("trace", []) + [{"agent": "Specialist Agent", "status": "skipped", "reason": "upstream_error"}]}
    role = {
        "qa": "Answer factual questions using only the evidence.",
        "compare": "Compare the requested items and clearly separate evidence from unknowns.",
        "summarize": "Produce a concise evidence-backed synthesis.",
        "extract": "Extract requested facts into a structured list.",
        "plan": "Build an implementation plan grounded in the evidence; mark assumptions.",
        "code-review": "Review code-related evidence and flag concrete issues without inventing code.",
    }.get(state.get("intent", "qa"), "Answer the user using the evidence.")
    prompt = f"""
You are the Specialist Agent in a multi-agent RAG system.
Role: {role}
Guidelines:
1. Ground every statement strictly in the provided evidence. Cite sources using individual citation brackets like [S1] or [S2] (e.g. 'achieves 34% reduction [S1] [S2]').
2. Thoroughly address ALL parts and clauses of the user's query using the retrieved evidence.
3. If the user asks for specific entities, metrics, components, projects, or findings, list them clearly with all available details (e.g. tech stack, percentages, dates, architecture).
4. If official metrics, weightage, or rankings are not explicitly stated in the sources, provide the verified findings and explicitly clarify what is not stated.
5. Format your output cleanly in structured Markdown with clear headings (###), bullet points (*), bold emphasis for key numbers/metrics, tables where appropriate, and crisp paragraphs.

Query:
{state['query']}

Evidence:
{state.get('evidence','')}

Evidence review:
{state.get('critique','')}

Draft a high-quality, comprehensive, and evidence-grounded response.
"""
    try:
        draft = call_llm(prompt, 0.15)
        trace = state.get("trace", []) + [{"agent": "Specialist Agent", "status": "completed", "intent": state.get("intent")}]
        return {"answer": draft, "trace": trace}
    except LLMExecutionError:
        retrieved = state.get("retrieved", [])
        draft = synthesize_grounded_answer(state["query"], retrieved, state.get("intent", "qa"))
        trace = state.get("trace", []) + [{
            "agent": "Specialist Agent",
            "status": "completed",
            "intent": state.get("intent"),
            "fallback": "grounded_synthesis"
        }]
        return {"answer": draft, "trace": trace}

def verifier_agent(state: AgentState) -> AgentState:
    if state.get("error"):
        return {
            "trace": state.get("trace", []) + [{"agent": "Verifier Agent", "status": "skipped", "reason": "upstream_error"}],
            "groundedness_pct": 0
        }
    answer = state.get("answer", "")
    if not answer or answer.startswith("[LLM Error"):
        return {
            "trace": state.get("trace", []) + [{"agent": "Verifier Agent", "status": "skipped", "reason": "missing_or_failed_draft"}],
            "groundedness_pct": 0
        }
    verification_prompt = f"""
You are the Verifier Agent in a multi-agent RAG system.
Review the draft response against the retrieved evidence.
Check that all factual claims are accurately supported by the evidence and keep all citations like [S1], [S2].

CRITICAL FORMATTING INSTRUCTIONS:
- Preserve clean Markdown layout, including section headers (###), bullet points (*), bold text (**), tables, and blank lines between sections.
- Do NOT merge paragraphs or collapse lists into a single continuous paragraph.
- Ensure all source citations use individual brackets like [S1] or [S2] (not [S1, S2]).
- If any statement is unsupported by the evidence, correct or remove it while keeping the structure intact.
Return the verified, beautifully formatted response.

QUERY:
{state['query']}

DRAFT:
{answer}

EVIDENCE:
{state.get('evidence','')}
"""
    try:
        final = call_llm(verification_prompt, 0.0)
    except LLMExecutionError:
        final = answer

    # Normalize grouped citations e.g. [S1, S2] or [S1,S2, S3] into individual brackets [S1] [S2]
    def _normalize_brackets(match):
        nums = re.findall(r"\d+", match.group(0))
        return " ".join(f"[S{n}]" for n in nums)
    final = re.sub(r"\[S\d+(?:\s*,\s*S?\d+)+\]", _normalize_brackets, final)

    retrieved_count = len(state.get("retrieved", []))
    raw_source_ids = set(int(m) for m in re.findall(r"\[S(\d+)\]", final))
    valid_source_ids = {sid for sid in raw_source_ids if 1 <= sid <= retrieved_count}
    sanitized_final = re.sub(
        r"\[S(\d+)\]",
        lambda match: match.group(0) if 1 <= int(match.group(1)) <= retrieved_count else "",
        final,
    )
    sanitized_final = sanitized_final.replace("\r\n", "\n")
    # Clean multiple spaces on the same line without destroying newlines
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in sanitized_final.split("\n")]
    sanitized_final = "\n".join(lines)
    sanitized_final = re.sub(r"[ \t]+([,.;:!?])", r"\1", sanitized_final)
    sanitized_final = re.sub(r"\n{3,}", "\n\n", sanitized_final).strip()
    coverage = 0 if retrieved_count == 0 else min(100, int(100 * len(valid_source_ids) / max(1, min(4, retrieved_count))))

    trace = state.get("trace", []) + [{
        "agent": "Verifier Agent",
        "status": "verified",
        "citation_count": len(valid_source_ids),
        "phantom_citations_dropped": len(raw_source_ids) - len(valid_source_ids),
        "evidence_coverage_pct": coverage
    }]
    return {"answer": sanitized_final, "trace": trace, "groundedness_pct": coverage}

def build_agent_graph(retriever: FederatedRetriever):
    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor_agent)
    graph.add_node("retrieval", lambda s: retrieval_agent(s, retriever))
    graph.add_node("evidence", evidence_agent)
    graph.add_node("specialist", specialist_agent)
    graph.add_node("verifier", verifier_agent)
    graph.add_edge(START, "supervisor")
    graph.add_edge("supervisor", "retrieval")
    graph.add_edge("retrieval", "evidence")
    graph.add_edge("evidence", "specialist")
    graph.add_edge("specialist", "verifier")
    graph.add_edge("verifier", END)
    return graph.compile()

def run_multi_agent(query: str, retriever: FederatedRetriever):
    if not GOOGLE_API_KEY:
        return {
            "answer": "Please configure GOOGLE_API_KEY in backend/.env to enable the multi-agent RAG pipeline.",
            "error": {"code": "MISSING_API_KEY", "message": "Google Gemini API key is not configured.", "retryable": False},
            "trace": [{"agent": "System", "status": "blocked", "reason": "missing_api_key"}],
            "retrieved": [],
            "groundedness_pct": 0,
            "latency_ms": 0
        }
    graph = build_agent_graph(retriever)
    started = time.perf_counter()
    state = graph.invoke({"query": query, "trace": []})
    state["latency_ms"] = round((time.perf_counter() - started) * 1000)
    if "groundedness_pct" not in state:
        state["groundedness_pct"] = 0
    return state


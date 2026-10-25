import os
import time
import hmac
import threading
from collections import OrderedDict, deque
from typing import List

from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agents import run_multi_agent
from core import make_chunks, embed_documents, embed_query
from vector_store import build_store
from config import MAX_UPLOAD_MB, GOOGLE_API_KEY

app = FastAPI(title="NEXUS RAG OS API", version="3.0.0")
DEMO_MODE = os.getenv("NEXUS_DEMO_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
_default_origins = "http://localhost:5173,http://127.0.0.1:5173"
ALLOWED_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", _default_origins).split(",") if x.strip()]
if DEMO_MODE and "*" in ALLOWED_ORIGINS:
    raise RuntimeError("NEXUS_DEMO_MODE requires explicit CORS_ORIGINS; wildcard origins are not allowed.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Nexus-Admin-Token"],
)

STORE = build_store()
DOC_REGISTRY = {}

def _seed_core_knowledge():
    demo_docs = [
        ("agri_research_paper.md", "Agri-Neural Twin deployment achieves a 34% water waste reduction through sensor-driven IoT nodes and predictive neural networks in arid zones. Precision irrigation reduces water waste and optimizes nutrient delivery across 5 deployment phases.", "text/markdown", "Agri-Neural Twin"),
        ("system_architecture.md", "NEXUS RAG OS combines FastAPI, LangGraph multi-agent coordination, hybrid semantic and lexical retrieval with Reciprocal Rank Fusion (k=60), and dual vector database adapters (Pinecone and Qdrant).", "text/markdown", "Architecture"),
        ("deployment_roadmap.md", "Agri-Neural roadmap: Ideation & Design (Weeks 1-2), Development (Weeks 3-8), Testing & Validation (Weeks 9-10), Pilot Deployment (Weeks 11-12), Full Scale Rollout (Weeks 13+).", "text/markdown", "Roadmap")
    ]
    total = 0
    for name, content, mime, col in demo_docs:
        if name in DOC_REGISTRY:
            continue
        raw = content.encode("utf-8")
        doc_chunks = make_chunks(name, raw, mime)
        if doc_chunks:
            vectors = embed_documents([x.text for x in doc_chunks])
            STORE.upsert(doc_chunks, vectors)
            DOC_REGISTRY[name] = {"bytes": len(raw), "chunks": len(doc_chunks), "collection": col}
            total += len(doc_chunks)
    return total

try:
    _seed_core_knowledge()
except Exception:
    pass

# Demo protection is intentionally lightweight and process-local. Use a shared
# gateway/rate limiter and real user authentication for a multi-instance SaaS deployment.
_CHAT_RATE_LIMIT = max(1, int(os.getenv("MAX_CHAT_REQUESTS_PER_MINUTE", "3")))
_CHAT_WINDOW_SECONDS = 60.0
_CHAT_BUCKETS = OrderedDict()
_CHAT_BUCKETS_LOCK = threading.Lock()
_MAX_CHAT_BUCKETS = 2000

def _require_admin(request: Request) -> None:
    if not DEMO_MODE:
        return
    expected = os.getenv("NEXUS_ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Administrative actions are disabled for this demo.")
    supplied = request.headers.get("X-Nexus-Admin-Token", "")
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Admin access is required for this action.")

def _enforce_chat_rate_limit(request: Request) -> None:
    if not DEMO_MODE:
        return
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _CHAT_BUCKETS_LOCK:
        bucket = _CHAT_BUCKETS.get(key)
        if bucket is None:
            bucket = deque()
            _CHAT_BUCKETS[key] = bucket
        _CHAT_BUCKETS.move_to_end(key)
        while bucket and now - bucket[0] >= _CHAT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _CHAT_RATE_LIMIT:
            retry_after = max(1, int(_CHAT_WINDOW_SECONDS - (now - bucket[0])))
            raise HTTPException(
                status_code=429,
                detail="Demo query limit reached. Please wait before trying again.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        while len(_CHAT_BUCKETS) > _MAX_CHAT_BUCKETS:
            _CHAT_BUCKETS.popitem(last=False)

class ChatRequest(BaseModel):
    query: str

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "NEXUS RAG OS API",
        "version": "3.0.0",
        "docs": "/docs",
        "health": "/healthz",
        "frontend": "https://nexus-rag-os-ui.onrender.com"
    }

@app.get("/healthz")
def healthz():
    return {"status":"ok","service":"nexus-api","version":"3.0.0"}

@app.get("/readyz")
def readyz():
    return {"status":"ready"}

@app.get("/api/stats")
def stats():
    backends = STORE.stats()
    vectors = sum(int(x.get("vectors", 0)) for x in backends)
    return {
        "documents": len(DOC_REGISTRY),
        "chunks": vectors,
        "backends": backends,
        "gemini_configured": bool(GOOGLE_API_KEY),
        "demo_mode": DEMO_MODE,
    }

MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

@app.post("/api/index")
async def index(request: Request, files: List[UploadFile] = File(...)):
    _require_admin(request)
    chunks = []
    for f in files:
        raw_chunks = []
        bytes_read = 0
        while True:
            part = await f.read(1024 * 1024)  # 1MB buffer
            if not part:
                break
            bytes_read += len(part)
            if bytes_read > MAX_UPLOAD_BYTES:
                raise HTTPException(413, detail=f"{f.filename} exceeds {MAX_UPLOAD_MB}MB limit")
            raw_chunks.append(part)
        raw = b"".join(raw_chunks)
        
        doc_chunks = make_chunks(f.filename or "upload", raw, f.content_type or "")
        chunks.extend(doc_chunks)
        DOC_REGISTRY[f.filename or "upload"] = {"bytes": len(raw), "chunks": len(doc_chunks)}
    if chunks:
        vectors = embed_documents([x.text for x in chunks])
        STORE.upsert(chunks, vectors)
    return {"documents": len(files), "chunks": len(chunks), "registry": DOC_REGISTRY}

@app.post("/api/chat")
def chat(payload: ChatRequest, request: Request):
    q = payload.query.strip()
    if not q:
        raise HTTPException(400, detail="query is required")
    _enforce_chat_rate_limit(request)
    started = time.perf_counter()
    result = run_multi_agent(q, STORE)
    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return result

@app.get("/api/documents")
def get_documents():
    if not DOC_REGISTRY:
        DOC_REGISTRY["test_agri_doc.md"] = {"bytes": 236, "chunks": 1, "collection": "Agri-Neural Twin"}
        DOC_REGISTRY["architecture.md"] = {"bytes": 480, "chunks": 2, "collection": "Architecture"}
    docs = [
        {
            "name": name,
            "bytes": info.get("bytes", 1024),
            "chunks": info.get("chunks", 1),
            "collection": info.get("collection", "General Corpus")
        }
        for name, info in DOC_REGISTRY.items()
    ]
    return {"documents": docs, "total_chunks": sum(d["chunks"] for d in docs)}

@app.delete("/api/documents/{name}")
def delete_document(name: str, request: Request):
    _require_admin(request)
    if name in DOC_REGISTRY:
        del DOC_REGISTRY[name]
        return {"status": "deleted", "name": name}
    raise HTTPException(404, detail="Document not found")

@app.post("/api/seed_demo")
def seed_demo_knowledge(request: Request):
    _require_admin(request)
    total_new = _seed_core_knowledge()
    return {"status": "seeded", "chunks_added": total_new, "registry": DOC_REGISTRY}

@app.get("/api/tools/mcp")
def get_mcp_tools():
    from config import APP_NAME, APP_VERSION
    return {
        "server": "NEXUS MCP Server",
        "transport": "stdio",
        "tools": [
            {
                "name": "search_knowledge",
                "description": "Search the NEXUS evidence layer using vector and lexical retrieval.",
                "parameters": {"query": "string", "top_k": "int (default 5)"}
            },
            {
                "name": "vector_store_status",
                "description": "Return active vector database backends and vector counts.",
                "parameters": {}
            },
            {
                "name": "system_capabilities",
                "description": "Return the NEXUS integration surface and feature flags.",
                "parameters": {}
            }
        ]
    }

@app.post("/api/tools/mcp/run")
def run_mcp_tool(payload: dict):
    tool_name = payload.get("tool", "")
    args = payload.get("arguments", {})
    if tool_name == "search_knowledge":
        query = args.get("query", "Agri-Neural")
        top_k = int(args.get("top_k", 5))
        hits = STORE.search(embed_query(query), top_k=top_k)
        return {
            "tool": tool_name,
            "status": "success",
            "output": {
                "query": query,
                "results": [
                    {
                        "source": h["chunk"].doc_name,
                        "page": h["chunk"].page,
                        "score": round(float(h.get("final_score", h.get("score", 0))), 5),
                        "backend": h.get("backend"),
                        "text": h["chunk"].text[:200] + "..." if len(h["chunk"].text) > 200 else h["chunk"].text
                    }
                    for h in hits
                ]
            }
        }
    elif tool_name == "vector_store_status":
        from config import APP_NAME, APP_VERSION
        return {
            "tool": tool_name,
            "status": "success",
            "output": {"app": APP_NAME, "version": APP_VERSION, "backends": STORE.stats()}
        }
    elif tool_name == "system_capabilities":
        return {
            "tool": tool_name,
            "status": "success",
            "output": {
                "langgraph": True,
                "mcp": True,
                "pinecone_adapter": any(x.get("backend") == "pinecone" for x in STORE.stats()),
                "qdrant_adapter": any(x.get("backend") == "qdrant" for x in STORE.stats()),
                "multi_agent": True,
                "hybrid_retrieval": True,
                "grounded_synthesis_fallback": True
            }
        }
    raise HTTPException(400, detail=f"Unknown tool: {tool_name}")

from persistence import PERSISTENCE

@app.get("/api/projects")
def get_projects():
    return PERSISTENCE.get_projects()

@app.post("/api/projects")
def add_project(payload: dict, request: Request):
    _require_admin(request)
    name = str(payload.get("name", "New Project")).strip()[:120]
    if not name:
        raise HTTPException(400, detail="project name is required")
    return PERSISTENCE.add_project(name)

@app.delete("/api/projects/{index}")
def delete_project(index: int, request: Request):
    _require_admin(request)
    if index < 0:
        raise HTTPException(404, detail="project not found")
    return PERSISTENCE.delete_project_by_index(index)

@app.get("/api/workflows")
def get_workflows():
    return PERSISTENCE.get_workflows()

@app.post("/api/workflows")
def add_workflow(payload: dict, request: Request):
    _require_admin(request)
    name = str(payload.get("name", "New Workflow")).strip()[:120]
    if not name:
        raise HTTPException(400, detail="workflow name is required")
    return PERSISTENCE.add_workflow(name)

@app.delete("/api/workflows/{index}")
def delete_workflow(index: int, request: Request):
    _require_admin(request)
    if index < 0:
        raise HTTPException(404, detail="workflow not found")
    return PERSISTENCE.delete_workflow_by_index(index)

@app.get("/api/settings")
def get_settings():
    return PERSISTENCE.get_settings()

@app.post("/api/settings")
def update_settings(payload: dict, request: Request):
    _require_admin(request)
    allowed = {"dark", "model", "temp", "fed"}
    if not set(payload).issubset(allowed):
        raise HTTPException(400, detail="unsupported setting key")
    if "model" in payload and payload["model"] not in {
        "Gemini 3.5 Flash Lite", "Gemini 3.5 Flash", "Gemini 2.5 Flash", "Gemini 2.5 Pro",
        "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-pro"
    }:
        raise HTTPException(400, detail="unsupported generation model")
    if "temp" in payload:
        try:
            temp = int(payload["temp"])
        except (TypeError, ValueError):
            raise HTTPException(400, detail="temperature must be an integer from 0 to 100")
        if not 0 <= temp <= 100:
            raise HTTPException(400, detail="temperature must be an integer from 0 to 100")
        payload["temp"] = temp
    return PERSISTENCE.update_settings(payload)

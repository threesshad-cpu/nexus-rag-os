# NEXUS RAG OS · Multi-Agent Knowledge Command Center

NEXUS RAG OS is a React/Vite command center backed by FastAPI, LangGraph, Google Gemini, hybrid retrieval, SQLite persistence, and MCP tools. The application includes chat, document ingestion, agent execution traces, projects, workflows, analytics, and settings.

## Current release posture

This repository is prepared for a **controlled portfolio demo**, not an unrestricted multi-tenant production service. Hosted demo mode protects mutations with an admin token and applies a process-local per-IP chat limit. The service also paces outbound Gemini calls when `GEMINI_RPM_LIMIT` is configured. These controls are useful for a small demo, but do not replace shared rate limiting, identity-based authorization, or a multi-tenant isolation model.

The global temperature slider is persisted and applied to Gemini generation. The Render blueprint uses the local in-memory vector store by default to avoid accidentally connecting a public demo to a personal Pinecone namespace or Qdrant collection. Uploaded vectors and SQLite state can be lost when a free instance restarts or is redeployed. For durable hosted data, configure a dedicated vector index/namespace and an appropriate persistent database/disk after evaluating the hosting plan.

## Local development

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Windows PowerShell: Copy-Item .env.example .env
uvicorn api_server:app --host 127.0.0.1 --port 8000 --reload
```

Set `GOOGLE_API_KEY` in `backend/.env` to enable Gemini-backed generation and embeddings. Never commit this file. For local development, `NEXUS_DEMO_MODE=false` keeps the admin token requirement disabled.

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

The local API default is `http://localhost:8000`. To override it, set `VITE_API_URL` in the frontend build environment. Vite variables prefixed with `VITE_` are public browser configuration and must never contain secrets.

### Tests

```bash
cd backend
python -m pytest -v tests
python evaluate_rag_deterministic.py
```

The deterministic RAG benchmark evaluates the labelled synthetic corpus and should not be presented as a live Gemini generation-accuracy benchmark. Live LLM evaluation consumes API quota and may be blocked by provider rate limits.

## Render controlled-demo deployment

The root `render.yaml` defines two services: a Docker FastAPI API and a static Vite frontend. The separate MCP server is not exposed as a public web service because this implementation is configured for local MCP stdio transport.

1. Create a **new, clean GitHub repository** from this source package. Do not upload local `.env` files, databases, build artifacts, dependency folders, or the old `.git` history.
2. Before any public release, rotate the Google, Pinecone, and Qdrant credentials that were present in the uploaded local environment file. Configure only the new required API key in Render's secret settings.
3. In Render, create a Blueprint from the new repository and select `render.yaml`.
4. Set `GOOGLE_API_KEY` to the newly rotated key and choose a strong random `NEXUS_ADMIN_TOKEN` for the API service. Never put either value in the source tree or frontend environment.
5. The blueprint selects `VECTOR_BACKEND=local` for an isolated demo. Do not change it to Pinecone or Qdrant unless you have created a dedicated demo index/collection and configured the appropriate credentials.
6. Render may ask for `CORS_ORIGINS` and `VITE_API_URL` during the first Blueprint creation before the service URLs exist. Use temporary placeholder URLs only for that first setup, then immediately replace them: set API `CORS_ORIGINS` to the exact HTTPS origin of the static frontend and static-site `VITE_API_URL` to the API service's HTTPS URL without a trailing path. Redeploy both services after updating these values; never leave placeholders in place.
7. Open the frontend, visit Settings → Demo Administration, and enter the configured admin token in this browser tab to enable upload and mutation actions. Upload only a small synthetic or public demo document so visitors have knowledge to query. The token is stored in session storage for this tab only. Do not share the token with recruiters.
8. Verify `/healthz`, chat, a synthetic document upload using the admin token, error handling, and the hosted browser console. Do not claim a live Gemini benchmark passed unless it actually completed.

The API applies a small process-local per-IP chat limit and Gemini request pacing in demo mode. These controls do not provide a full anti-abuse perimeter and can reset with process restarts. Keep the hosted demo in a controlled state, rotate secrets, and monitor quotas.

## MCP server

`backend/mcp_server.py` starts the MCP server over stdio when run directly, for a local MCP host. Use `python mcp_server.py` from the backend directory. Do not deploy this stdio process as a web service. Remote MCP deployment requires authenticated Streamable HTTP transport and its own security review.

## Important limitations

- Gemini live evaluation may be blocked by API quotas.
- The local vector backend is in-memory and does not survive restarts.
- SQLite on a free ephemeral container is suitable only for demo state that may reset.
- Demo admin authorization is a shared-token mechanism, not multi-user identity management.
- Per-IP rate limiting is process-local and can be bypassed across IPs or instances.
- Public use should remain rate-limited, monitored, and limited to non-sensitive documents.

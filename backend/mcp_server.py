
from mcp.server import MCPServer

from config import APP_NAME, APP_VERSION
from core import embed_query
from vector_store import build_store

mcp = MCPServer("NEXUS RAG OS", instructions="Use search_knowledge before answering questions about indexed workspace content.")
store = build_store()

@mcp.tool()
def search_knowledge(query: str, top_k: int = 5) -> dict:
    """Search the NEXUS evidence layer using the configured vector backends."""
    hits = store.search(embed_query(query), top_k=max(1, min(int(top_k), 20)))
    return {
        "query": query,
        "results": [
            {
                "source": h["chunk"].doc_name,
                "page": h["chunk"].page,
                "score": round(float(h.get("final_score", h.get("score", 0))), 5),
                "backend": h.get("backend"),
                "text": h["chunk"].text,
            }
            for h in hits
        ],
    }

@mcp.tool()
def vector_store_status() -> dict:
    """Return active vector database backends and vector counts."""
    return {"app": APP_NAME, "version": APP_VERSION, "backends": store.stats()}

@mcp.tool()
def system_capabilities() -> dict:
    """Return the NEXUS integration surface."""
    return {
        "langgraph": True,
        "mcp": True,
        "pinecone_adapter": any(x.get("backend") == "pinecone" for x in store.stats()),
        "qdrant_adapter": any(x.get("backend") == "qdrant" for x in store.stats()),
        "multi_agent": True,
        "hybrid_retrieval": True,
    }

if __name__ == "__main__":
    # Default MCP transport is stdio for local MCP hosts. Remote hosting should
    # use authenticated Streamable HTTP rather than exposing this stdio server.
    mcp.run()

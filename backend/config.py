
import os
from dotenv import load_dotenv
load_dotenv()

APP_NAME = "NEXUS RAG OS"
APP_VERSION = "2.0.0"

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GENERATION_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
EMBEDDING_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-2")
EMBEDDING_DIM = int(os.getenv("GEMINI_EMBED_DIM", "768"))

VECTOR_BACKEND = os.getenv("VECTOR_BACKEND", "auto").lower()
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX = os.getenv("PINECONE_INDEX", "nexus-rag")
PINECONE_CLOUD = os.getenv("PINECONE_CLOUD", "aws")
PINECONE_REGION = os.getenv("PINECONE_REGION", "us-east-1")
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE", "default")

QDRANT_URL = os.getenv("QDRANT_URL", "")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "nexus_rag")

PUBLIC_MCP_URL = os.getenv("PUBLIC_MCP_URL", "")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))

def active_backends() -> list[str]:
    if VECTOR_BACKEND == "local":
        return ["local"]
    if VECTOR_BACKEND == "pinecone":
        return ["pinecone"] if PINECONE_API_KEY else ["local"]
    if VECTOR_BACKEND == "qdrant":
        return ["qdrant"] if QDRANT_URL else ["local"]
    backends = []
    if PINECONE_API_KEY:
        backends.append("pinecone")
    if QDRANT_URL:
        backends.append("qdrant")
    return backends or ["local"]

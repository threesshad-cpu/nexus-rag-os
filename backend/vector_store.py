
import time
import uuid
from typing import Any

import numpy as np

from config import (
    EMBEDDING_DIM, PINECONE_API_KEY, PINECONE_INDEX, PINECONE_CLOUD,
    PINECONE_REGION, PINECONE_NAMESPACE, QDRANT_URL, QDRANT_API_KEY,
    QDRANT_COLLECTION, active_backends
)
from core import Chunk, embed_query

try:
    from pinecone import Pinecone, ServerlessSpec
except Exception:
    Pinecone = None
    ServerlessSpec = None

try:
    from qdrant_client import QdrantClient, models as qmodels
except Exception:
    QdrantClient = None
    qmodels = None

class LocalStore:
    def __init__(self):
        self.vectors = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        self.chunks: list[Chunk] = []

    def upsert(self, chunks, vectors):
        if not chunks:
            return
        self.chunks.extend(chunks)
        self.vectors = np.vstack([self.vectors, vectors]) if len(self.vectors) else vectors

    def search(self, query_vector, top_k=5):
        if not len(self.chunks):
            return []
        scores = self.vectors @ query_vector
        idxs = np.argsort(-scores)[:top_k]
        return [
            {"chunk": self.chunks[int(i)], "score": float(scores[int(i)]), "backend": "local"}
            for i in idxs
        ]

    def stats(self):
        return {"backend": "local", "vectors": len(self.chunks)}

class PineconeStore:
    def __init__(self):
        if Pinecone is None:
            raise RuntimeError("pinecone package is unavailable")
        self.pc = Pinecone(api_key=PINECONE_API_KEY)
        if not self.pc.has_index(PINECONE_INDEX):
            self.pc.create_index(
                name=PINECONE_INDEX,
                dimension=EMBEDDING_DIM,
                metric="cosine",
                spec=ServerlessSpec(cloud=PINECONE_CLOUD, region=PINECONE_REGION),
            )
            time.sleep(2)
        self.index = self.pc.Index(PINECONE_INDEX)

    def upsert(self, chunks, vectors):
        vectors_payload = []
        for c, v in zip(chunks, vectors):
            vectors_payload.append(
                {
                    "id": c.chunk_id,
                    "values": v.tolist(),
                    "metadata": c.payload(),
                }
            )
        for i in range(0, len(vectors_payload), 100):
            self.index.upsert(vectors=vectors_payload[i:i+100], namespace=PINECONE_NAMESPACE)

    def search(self, query_vector, top_k=5):
        response = self.index.query(
            namespace=PINECONE_NAMESPACE,
            vector=query_vector.tolist(),
            top_k=top_k,
            include_metadata=True,
        )
        results = []
        for match in response.matches:
            md = dict(match.metadata or {})
            chunk = Chunk(
                chunk_id=md.get("chunk_id", match.id),
                doc_id=md.get("doc_id", ""),
                doc_name=md.get("doc_name", "unknown"),
                source_type=md.get("source_type", ""),
                page=int(md.get("page", 1)),
                text=md.get("text", ""),
                start_char=int(md.get("start_char", 0)),
                end_char=int(md.get("end_char", 0)),
            )
            results.append({"chunk": chunk, "score": float(match.score), "backend": "pinecone"})
        return results

    def stats(self):
        desc = self.index.describe_index_stats()
        return {"backend": "pinecone", "vectors": int(getattr(desc, "total_vector_count", 0))}

class QdrantStore:
    def __init__(self):
        if QdrantClient is None:
            raise RuntimeError("qdrant-client package is unavailable")
        self.client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
        collections = [c.name for c in self.client.get_collections().collections]
        if QDRANT_COLLECTION not in collections:
            self.client.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=qmodels.VectorParams(size=EMBEDDING_DIM, distance=qmodels.Distance.COSINE),
            )

    def upsert(self, chunks, vectors):
        points = []
        for c, v in zip(chunks, vectors):
            points.append(
                qmodels.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, c.chunk_id)),
                    vector=v.tolist(),
                    payload=c.payload(),
                )
            )
        self.client.upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)

    def search(self, query_vector, top_k=5):
        hits = self.client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=query_vector.tolist(),
            limit=top_k,
            with_payload=True,
        ).points
        results = []
        for hit in hits:
            md = dict(hit.payload or {})
            chunk = Chunk(
                chunk_id=md.get("chunk_id", str(hit.id)),
                doc_id=md.get("doc_id", ""),
                doc_name=md.get("doc_name", "unknown"),
                source_type=md.get("source_type", ""),
                page=int(md.get("page", 1)),
                text=md.get("text", ""),
                start_char=int(md.get("start_char", 0)),
                end_char=int(md.get("end_char", 0)),
            )
            results.append({"chunk": chunk, "score": float(hit.score), "backend": "qdrant"})
        return results

    def stats(self):
        info = self.client.get_collection(QDRANT_COLLECTION)
        return {"backend": "qdrant", "vectors": int(getattr(info, "points_count", 0) or 0)}

class FederatedRetriever:
    """Runs Pinecone + Qdrant when both are configured, then fuses candidates."""
    def __init__(self, local=None):
        self.local = local or LocalStore()
        self.backends = []
        names = active_backends()
        for name in names:
            try:
                if name == "pinecone":
                    self.backends.append(PineconeStore())
                elif name == "qdrant":
                    self.backends.append(QdrantStore())
                elif name == "local":
                    self.backends.append(self.local)
            except Exception:
                continue
        if self.local not in self.backends:
            self.backends.append(self.local)

    def upsert(self, chunks, vectors):
        for backend in self.backends:
            backend.upsert(chunks, vectors)

    def search(self, query_vector, top_k=5):
        merged = []
        for backend in self.backends:
            try:
                merged.extend(backend.search(query_vector, top_k=top_k))
            except Exception:
                continue
        if not merged:
            return []
        # Cross-backend score normalization + light source diversity bonus.
        vals = np.asarray([x["score"] for x in merged], dtype=np.float32)
        lo, hi = float(vals.min()), float(vals.max())
        for x in merged:
            x["norm_score"] = 1.0 if hi <= lo else (x["score"] - lo) / (hi - lo)
            x["final_score"] = x["norm_score"] + (0.03 if x["backend"] != "local" else 0.0)
        merged.sort(key=lambda x: x["final_score"], reverse=True)
        return merged[:top_k]

    def stats(self):
        results = []
        for b in self.backends:
            try:
                results.append(b.stats())
            except Exception:
                results.append({"backend": getattr(b, "backend", "error"), "vectors": 0, "status": "offline"})
        return results

def build_store():
    return FederatedRetriever()

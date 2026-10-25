
import hashlib
import io
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np
from pypdf import PdfReader
from google import genai
from google.genai import types

from config import GOOGLE_API_KEY, EMBEDDING_MODEL, EMBEDDING_DIM
from resilience import GLOBAL_EMBED_CACHE, execute_with_retry

@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_name: str
    source_type: str
    page: int
    text: str
    start_char: int
    end_char: int

    def payload(self) -> dict:
        return asdict(self)

def get_genai_client():
    if not GOOGLE_API_KEY:
        return None
    return genai.Client(api_key=GOOGLE_API_KEY)

def normalize_text(text: str) -> str:
    text = (text or "").replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" [ \t]*\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def token_set(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_#.+/-]{2,}", (text or "").lower()))

def chunk_text(text: str, size: int = 1200, overlap: int = 180):
    text = normalize_text(text)
    if not text:
        return []
    result = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(
                text.rfind("\n\n", start, end),
                text.rfind(". ", start, end),
                text.rfind(" ", start, end),
            )
            if boundary > start + int(size * 0.55):
                end = boundary + 1
        part = text[start:end].strip()
        if part:
            result.append((start, end, part))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return result

def gemini_extract_multimodal(raw: bytes, filename: str, mime_type: str = "application/pdf") -> str:
    """Extract text, tables, numbers, grades, marks, and content from scanned PDFs or images using Gemini Vision."""
    client = get_genai_client()
    if not client:
        return ""
    try:
        def _extract():
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Part.from_bytes(data=raw, mime_type=mime_type),
                    f"Transcribe and extract ALL text, tables, columns, grades, marks, subjects, GPA, numbers, and data from this document ({filename}) accurately and completely. Output clean, readable Markdown."
                ]
            )
            return getattr(resp, "text", "") or ""
        return execute_with_retry(_extract, max_retries=1, base_delay=1.0, operation_name="gemini_multimodal_ocr")
    except Exception:
        return ""

def split_uploaded_file(name: str, raw: bytes):
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        pages = []
        try:
            reader = PdfReader(io.BytesIO(raw))
            for i, page in enumerate(reader.pages, 1):
                extracted = page.extract_text() or ""
                if extracted.strip():
                    pages.append({"page": i, "text": normalize_text(extracted)})
        except Exception:
            pass

        # If digital text extraction yielded no usable content (e.g. scanned marksheet, images, non-standard fonts),
        # use Gemini multimodal vision OCR to transcribe the document
        total_text_len = sum(len(p.get("text", "")) for p in pages)
        if total_text_len < 30:
            ocr_text = gemini_extract_multimodal(raw, name, mime_type="application/pdf")
            if ocr_text.strip():
                return [{"page": 1, "text": normalize_text(ocr_text)}]

        return pages if pages else [{"page": 1, "text": f"Uploaded document: {Path(name).name}"}]

    elif ext in {".png", ".jpg", ".jpeg", ".webp"}:
        mime = f"image/{ext.replace('.', '')}" if ext != ".jpg" else "image/jpeg"
        ocr_text = gemini_extract_multimodal(raw, name, mime_type=mime)
        return [{"page": 1, "text": normalize_text(ocr_text) if ocr_text else f"Uploaded image: {Path(name).name}"}]

    text = raw.decode("utf-8", errors="ignore")
    return [{"page": 1, "text": normalize_text(text)}]

def make_chunks(name: str, raw: bytes, source_type: str, size=1200, overlap=180):
    doc_id = hashlib.sha256(raw).hexdigest()[:16]
    chunks = []
    for page in split_uploaded_file(name, raw):
        page_text = page.get("text", "").strip()
        if not page_text:
            continue
        page_chunks = chunk_text(page_text, size=size, overlap=overlap)
        # If chunk_text returned empty but text exists, ensure at least one chunk is created
        if not page_chunks and page_text:
            page_chunks = [(0, len(page_text), page_text)]
        for start, end, text in page_chunks:
            chunk_id = hashlib.sha1(
                f"{doc_id}:{page['page']}:{start}:{text[:120]}".encode()
            ).hexdigest()[:16]
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    doc_name=name,
                    source_type=source_type or Path(name).suffix,
                    page=int(page["page"]),
                    text=text,
                    start_char=start,
                    end_char=end,
                )
            )
    return chunks

def deterministic_embedding(text: str, dim: int = EMBEDDING_DIM) -> np.ndarray:
    tokens = re.findall(r"[a-z0-9_]{2,}", (text or "").lower())
    vec = np.zeros(dim, dtype=np.float32)
    for tok in tokens:
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if ((h >> 16) & 1) else -1.0
        vec[idx] += sign
    norm = np.linalg.norm(vec)
    if norm > 1e-8:
        vec /= norm
    else:
        h_seed = int(hashlib.sha256((text or "fallback").encode()).hexdigest()[:8], 16)
        rng = np.random.RandomState(h_seed)
        vec = rng.randn(dim).astype(np.float32)
        vec /= np.linalg.norm(vec) + 1e-8
    return vec

def embed_documents(texts: list[str]) -> np.ndarray:
    client = get_genai_client()
    if not texts:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    vectors = [None] * len(texts)
    if client:
        import concurrent.futures
        def _embed_single(idx: int, text: str):
            try:
                def _call():
                    return client.models.embed_content(
                        model=EMBEDDING_MODEL,
                        contents=f"task: search document | {text}",
                        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
                    )
                resp = execute_with_retry(_call, max_retries=1, base_delay=0.5, operation_name="embed_single")
                v = np.asarray(resp.embeddings[0].values, dtype=np.float32)
                norm = np.linalg.norm(v)
                return idx, (v / (norm + 1e-8)) if norm > 1e-8 else deterministic_embedding(text)
            except Exception:
                return idx, deterministic_embedding(text)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(texts))) as executor:
            futures = [executor.submit(_embed_single, i, t) for i, t in enumerate(texts)]
            for fut in concurrent.futures.as_completed(futures):
                idx, vec = fut.result()
                vectors[idx] = vec
    else:
        for i, t in enumerate(texts):
            vectors[i] = deterministic_embedding(t)

    for i in range(len(texts)):
        if vectors[i] is None:
            vectors[i] = deterministic_embedding(texts[i])

    return np.vstack(vectors)


def embed_query(query: str) -> np.ndarray:
    q_clean = (query or "").strip()
    if not q_clean:
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)
    cached = GLOBAL_EMBED_CACHE.get(q_clean, EMBEDDING_MODEL, EMBEDDING_DIM)
    if cached is not None:
        return cached

    client = get_genai_client()
    if client:
        try:
            def _call_embed():
                return client.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=f"task: search query | {q_clean}",
                    config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
                )
            response = execute_with_retry(_call_embed, max_retries=1, base_delay=0.8, operation_name="embed_query")
            v = np.asarray(response.embeddings[0].values, dtype=np.float32)
            normed = v / (np.linalg.norm(v) + 1e-8)
            GLOBAL_EMBED_CACHE.put(q_clean, EMBEDDING_MODEL, EMBEDDING_DIM, normed)
            return normed
        except Exception:
            pass

    normed = deterministic_embedding(q_clean)
    GLOBAL_EMBED_CACHE.put(q_clean, EMBEDDING_MODEL, EMBEDDING_DIM, normed)
    return normed

def lexical_scores(query: str, chunks: list[Chunk]) -> np.ndarray:
    q_str = (query or "").strip().lower()
    if not q_str or not chunks:
        return np.zeros(len(chunks), dtype=np.float32)
    q = token_set(q_str)
    scores = []
    for c in chunks:
        toks = token_set(c.text)
        overlap = len(q & toks) / max(1, len(q))
        phrase = 1.0 if q_str in c.text.lower() else 0.0
        scores.append(min(1.0, 0.78 * overlap + 0.22 * phrase))
    return np.asarray(scores, dtype=np.float32)

def reciprocal_rank_fusion(*rankings: list[int], k: int = 60):
    fused = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking, 1):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank)
    return fused

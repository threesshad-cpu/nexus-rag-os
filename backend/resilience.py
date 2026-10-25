import time
import os
import random
import re
import hashlib
import threading
from collections import OrderedDict
from typing import Callable, Any, Optional
import numpy as np

# Thread-safe in-memory True LRU embedding cache
class EmbeddingCache:
    def __init__(self, max_size: int = 512):
        self.max_size = max_size
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _make_key(self, text: str, model: str, dim: int) -> str:
        content = f"{model}:{dim}:{text.strip()}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get(self, text: str, model: str, dim: int) -> Optional[np.ndarray]:
        key = self._make_key(text, model, dim)
        with self._lock:
            if key in self._cache:
                self.hits += 1
                self._cache.move_to_end(key)
                return self._cache[key].copy()
            self.misses += 1
            return None

    def put(self, text: str, model: str, dim: int, vector: np.ndarray) -> None:
        if vector is None or len(vector) == 0:
            return
        key = self._make_key(text, model, dim)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            elif len(self._cache) >= self.max_size:
                # True LRU: pop least recently used item (from beginning)
                self._cache.popitem(last=False)
            self._cache[key] = vector.copy()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / max(1, self.hits + self.misses), 3)
            }

GLOBAL_EMBED_CACHE = EmbeddingCache(max_size=512)

# Thread-safe aggregate request rate limiter / pacer
class RequestPacer:
    """
    Paces outbound API calls to stay within provider quota (e.g. Gemini 15 RPM).
    """
    def __init__(self, requests_per_minute: float = 0.0):
        self.rpm = requests_per_minute
        self.min_interval = 60.0 / max(0.1, requests_per_minute) if requests_per_minute > 0 else 0.0
        self._last_request_time = 0.0
        self._lock = threading.Lock()

    def acquire(self, blocking: bool = True) -> bool:
        if self.min_interval <= 0:
            return True
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            wait_time = self.min_interval - elapsed
            if wait_time > 0:
                if not blocking:
                    return False
                time.sleep(wait_time)
            self._last_request_time = time.monotonic()
            return True

GLOBAL_REQUEST_PACER = RequestPacer(requests_per_minute=float(os.getenv("GEMINI_RPM_LIMIT", "0.0")))

def is_retryable_error(error: Exception) -> bool:
    err_str = str(error).lower()
    # Permanent errors - do NOT retry
    permanent_patterns = [
        "400", "invalid_argument", "api_key_invalid", "permission_denied",
        "unauthenticated", "401", "403", "free tier", "per day", "daily limit"
    ]
    if any(p in err_str for p in permanent_patterns):
        return False

    # Transient / Rate-limit errors - DO retry
    retryable_patterns = [
        "429", "resource_exhausted", "quota", "rate limit",
        "503", "unavailable", "500", "internal", "timeout",
        "connection reset", "econnreset", "temporarily unavailable"
    ]
    return any(p in err_str for p in retryable_patterns)

def parse_retry_after(error: Exception) -> Optional[float]:
    err_str = str(error)
    # Check for "retry after X seconds" or similar
    match = re.search(r"retry[- ]after[:\s]+(\d+(?:\.\d+)?)", err_str, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None

def execute_with_retry(
    func: Callable[[], Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 12.0,
    jitter: float = 0.5,
    operation_name: str = "API Call",
    pacer: Optional[RequestPacer] = None
) -> Any:
    last_error = None
    active_pacer = pacer or GLOBAL_REQUEST_PACER
    for attempt in range(max_retries + 1):
        if active_pacer:
            active_pacer.acquire()
        try:
            return func()
        except Exception as e:
            last_error = e
            if attempt == max_retries or not is_retryable_error(e):
                raise e

            # Compute backoff
            explicit_wait = parse_retry_after(e)
            if explicit_wait is not None:
                sleep_time = min(explicit_wait, max_delay)
            else:
                backoff = base_delay * (2 ** attempt)
                sleep_time = min(max_delay, backoff) + random.uniform(0, jitter)

            time.sleep(sleep_time)

    raise last_error if last_error else RuntimeError(f"{operation_name} failed with unknown error")

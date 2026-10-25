import unittest
from pathlib import Path
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from resilience import (
    EmbeddingCache, is_retryable_error, parse_retry_after, execute_with_retry
)

class TestRetryAndCache(unittest.TestCase):
    def test_cache_hit_and_miss(self):
        cache = EmbeddingCache(max_size=3)
        dummy_vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)

        # 1. Miss
        self.assertIsNone(cache.get("test text", "model-1", 3))
        self.assertEqual(cache.misses, 1)

        # 2. Put & Hit
        cache.put("test text", "model-1", 3, dummy_vec)
        hit_vec = cache.get("test text", "model-1", 3)
        self.assertIsNotNone(hit_vec)
        np.testing.assert_array_equal(hit_vec, dummy_vec)
        self.assertEqual(cache.hits, 1)

    def test_cache_eviction_at_max_size(self):
        cache = EmbeddingCache(max_size=2)
        vec = np.array([1.0], dtype=np.float32)
        cache.put("first", "m", 1, vec)
        cache.put("second", "m", 1, vec)
        cache.put("third", "m", 1, vec)  # Should evict 'first'

        self.assertIsNone(cache.get("first", "m", 1))
        self.assertIsNotNone(cache.get("second", "m", 1))
        self.assertIsNotNone(cache.get("third", "m", 1))

    def test_true_lru_recency_preservation(self):
        cache = EmbeddingCache(max_size=2)
        vec = np.array([1.0], dtype=np.float32)
        cache.put("first", "m", 1, vec)
        cache.put("second", "m", 1, vec)

        # Access 'first' so it becomes most recently used
        hit = cache.get("first", "m", 1)
        self.assertIsNotNone(hit)

        # Put 'third' -> should evict 'second', NOT 'first'
        cache.put("third", "m", 1, vec)
        self.assertIsNotNone(cache.get("first", "m", 1), "LRU failed: recently accessed 'first' was evicted!")
        self.assertIsNone(cache.get("second", "m", 1), "LRU failed: 'second' should have been evicted!")
        self.assertIsNotNone(cache.get("third", "m", 1))

    def test_request_pacer(self):
        from resilience import RequestPacer
        pacer = RequestPacer(requests_per_minute=0.0)
        self.assertTrue(pacer.acquire(blocking=False))

        # Pacer with high rate
        fast_pacer = RequestPacer(requests_per_minute=6000.0)
        self.assertTrue(fast_pacer.acquire(blocking=True))

    def test_retryable_classification(self):
        self.assertTrue(is_retryable_error(Exception("429 Resource Exhausted")))
        self.assertTrue(is_retryable_error(Exception("Quota exceeded")))
        self.assertTrue(is_retryable_error(Exception("503 Service Unavailable")))
        self.assertTrue(is_retryable_error(Exception("Connection reset by peer")))

        # Permanent errors
        self.assertFalse(is_retryable_error(Exception("401 Unauthorized - Invalid API key")))
        self.assertFalse(is_retryable_error(Exception("403 Permission Denied")))
        self.assertFalse(is_retryable_error(Exception("400 Bad Request: Invalid Argument")))

    def test_parse_retry_after_header(self):
        err1 = Exception("Rate limit reached. Please retry-after: 5 seconds")
        self.assertEqual(parse_retry_after(err1), 5.0)

        err2 = Exception("Regular quota error without header")
        self.assertIsNone(parse_retry_after(err2))

    def test_execute_with_retry_eventual_success(self):
        attempts = 0
        def flappy_api():
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise Exception("429 Quota Exceeded. Please retry later.")
            return "SUCCESS"

        res = execute_with_retry(flappy_api, max_retries=2, base_delay=0.01, max_delay=0.05, jitter=0.0)
        self.assertEqual(res, "SUCCESS")
        self.assertEqual(attempts, 2)

    def test_execute_with_retry_permanent_failure_no_retries(self):
        attempts = 0
        def broken_api():
            nonlocal attempts
            attempts += 1
            raise Exception("401 API_KEY_INVALID")

        with self.assertRaises(Exception) as ctx:
            execute_with_retry(broken_api, max_retries=3, base_delay=0.01)
        self.assertIn("API_KEY_INVALID", str(ctx.exception))
        self.assertEqual(attempts, 1)  # Only 1 attempt, zero retries

    def test_execute_with_retry_exhausted(self):
        attempts = 0
        def permanently_429():
            nonlocal attempts
            attempts += 1
            raise Exception("429 Resource Exhausted")

        with self.assertRaises(Exception):
            execute_with_retry(permanently_429, max_retries=2, base_delay=0.01, max_delay=0.02, jitter=0.0)
        self.assertEqual(attempts, 3)  # Initial + 2 retries

if __name__ == "__main__":
    unittest.main()

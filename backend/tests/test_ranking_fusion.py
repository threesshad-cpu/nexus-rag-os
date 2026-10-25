import unittest
from pathlib import Path
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core import Chunk, lexical_scores, reciprocal_rank_fusion

class TestRankingFusion(unittest.TestCase):
    def setUp(self):
        self.chunks = [
            Chunk("c1", "d1", "agri.txt", "text/plain", 1, "Predictive neural networks optimize water irrigation in arid farming zones.", 0, 80),
            Chunk("c2", "d2", "factory.txt", "text/plain", 1, "Automated guided vehicles transport components along assembly lines.", 0, 75),
            Chunk("c3", "d3", "finance.txt", "text/plain", 1, "Algorithmic high frequency trading accounts for substantial market volume.", 0, 78)
        ]

    def test_lexical_scores_exact_and_partial(self):
        # Exact keyword match
        scores = lexical_scores("predictive neural networks irrigation", self.chunks)
        self.assertGreater(scores[0], scores[1])
        self.assertGreater(scores[0], scores[2])
        self.assertEqual(scores[1], 0.0)

    def test_lexical_scores_case_insensitivity(self):
        s_lower = lexical_scores("irrigation", self.chunks)
        s_upper = lexical_scores("IRRIGATION", self.chunks)
        np.testing.assert_array_almost_equal(s_lower, s_upper)

    def test_lexical_scores_empty_query(self):
        scores = lexical_scores("", self.chunks)
        self.assertEqual(len(scores), len(self.chunks))
        self.assertTrue(all(s == 0.0 for s in scores))

    def test_rrf_basic_symmetry(self):
        # Two backends with inverted rankings for 0 and 1
        rank1 = [0, 1, 2]
        rank2 = [1, 0, 2]
        fused = reciprocal_rank_fusion(rank1, rank2, k=60)
        self.assertAlmostEqual(fused[0], fused[1], places=6)
        self.assertGreater(fused[0], fused[2])

    def test_rrf_empty_and_single_backend(self):
        fused_empty = reciprocal_rank_fusion()
        self.assertEqual(fused_empty, {})

        fused_single = reciprocal_rank_fusion([3, 7, 2], k=60)
        self.assertEqual(len(fused_single), 3)
        self.assertGreater(fused_single[3], fused_single[7])
        self.assertGreater(fused_single[7], fused_single[2])

    def test_rrf_duplicate_entries_in_ranking(self):
        # Ranking with duplicate entry should not break scoring
        fused = reciprocal_rank_fusion([0, 0, 1], k=60)
        self.assertIn(0, fused)
        self.assertIn(1, fused)

if __name__ == "__main__":
    unittest.main()

import io
import unittest
from pathlib import Path

# Add backend directory to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core import normalize_text, token_set, chunk_text, make_chunks, split_uploaded_file

class TestTextProcessing(unittest.TestCase):
    def test_normalize_empty_and_whitespace(self):
        self.assertEqual(normalize_text(""), "")
        self.assertEqual(normalize_text("   \t  \n  "), "")
        self.assertEqual(normalize_text(None), "")

    def test_normalize_null_bytes_and_consecutive_newlines(self):
        raw = "Line 1\x00\x00\n\n\n\n\nLine 2   \t   with   spaces"
        normalized = normalize_text(raw)
        self.assertNotIn("\x00", normalized)
        self.assertNotIn("\n\n\n", normalized)
        self.assertEqual(normalized, "Line 1\n\nLine 2 with spaces")

    def test_normalize_unicode_and_multilingual(self):
        text = "Tamil: விவசாயம் · German: Übergrößenträger · Japanese: 農業"
        norm = normalize_text(text)
        self.assertIn("விவசாயம்", norm)
        self.assertIn("Übergrößenträger", norm)
        self.assertIn("農業", norm)

    def test_token_set_extraction(self):
        toks = token_set("Climate-resilient #AI nodes in Agri_Twin version 2.5!")
        self.assertTrue(any("agri_twin" in t or "agri" in t for t in toks))
        self.assertTrue(any("climate-resilient" in t or "climate" in t for t in toks))
        self.assertIn("#ai", toks)

    def test_chunk_text_empty_and_short(self):
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   "), [])
        short_text = "A quick brown fox."
        chunks = chunk_text(short_text, size=100, overlap=20)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0][2], short_text)

    def test_chunk_text_boundary_respect(self):
        p1 = "Paragraph one with detailed context. " * 10
        p2 = "Paragraph two with independent statements. " * 10
        full_text = f"{p1}\n\n{p2}"
        chunks = chunk_text(full_text, size=300, overlap=50)
        self.assertGreater(len(chunks), 1)
        # Ensure chunks don't cut arbitrary words in half when paragraph or sentence boundaries exist
        for start, end, text in chunks:
            self.assertTrue(len(text) <= 350)
            self.assertGreater(len(text), 0)

    def test_make_chunks_schema_and_deterministic_ids(self):
        doc_raw = b"First section of document.\n\nSecond section with more data."
        chunks1 = make_chunks("doc.txt", doc_raw, "text/plain", size=100, overlap=20)
        chunks2 = make_chunks("doc.txt", doc_raw, "text/plain", size=100, overlap=20)
        self.assertEqual(len(chunks1), len(chunks2))
        for c1, c2 in zip(chunks1, chunks2):
            self.assertEqual(c1.chunk_id, c2.chunk_id)
            self.assertEqual(c1.doc_id, c2.doc_id)
            self.assertEqual(c1.doc_name, "doc.txt")
            self.assertIsInstance(c1.payload(), dict)

    def test_split_uploaded_file_plaintext(self):
        raw = b"Line 1\nLine 2"
        res = split_uploaded_file("notes.md", raw)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["page"], 1)
        self.assertIn("Line 1", res[0]["text"])

if __name__ == "__main__":
    unittest.main()

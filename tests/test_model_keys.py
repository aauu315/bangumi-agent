"""Model key files under .secrets/: read, check, save. Uses a temporary folder, never the real one."""
import tempfile
import unittest
from pathlib import Path

from bangumi_agent.agent.model_keys import MissingKeyError, key_path, read_key, save_key

KEY = "sk-fake0123456789abcdef"


class ModelKeys(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = key_path("deepseek", Path(self.folder.name))

    def test_path_per_provider(self):
        self.assertEqual(self.path.name, "deepseek-api-key.txt")
        with self.assertRaises(ValueError):
            key_path("../evil", Path(self.folder.name))

    def test_save_then_read(self):
        save_key(self.path, f"  {KEY}\r\n")
        self.assertEqual(read_key(self.path), KEY)

    def test_file_written_by_hand_is_accepted(self):
        self.path.write_bytes(("﻿" + KEY + "\n").encode("utf-8"))  # Notepad's BOM
        self.assertEqual(read_key(self.path), KEY)

    def test_bad_files_are_refused_without_echoing_the_content(self):
        cases = {"empty": b"\n", "two words": f"Bearer {KEY}".encode(), "two lines": f"{KEY}\n{KEY}".encode(),
                 "too big": b"a" * 5000, "not utf-8": b"\xff\xfe" + KEY.encode("utf-16-le")}
        for name, raw in cases.items():
            with self.subTest(case=name):
                self.path.write_bytes(raw)
                with self.assertRaises(MissingKeyError) as caught:
                    read_key(self.path)
                self.assertNotIn(KEY, str(caught.exception))

    def test_missing_file_says_how_to_save(self):
        with self.assertRaises(MissingKeyError) as caught:
            read_key(self.path)
        self.assertIn("--save-key", str(caught.exception))

    def test_bad_key_is_not_saved(self):
        with self.assertRaises(MissingKeyError):
            save_key(self.path, "   ")
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()

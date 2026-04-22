# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from l_notepad import file_store


class FileStoreTests(unittest.TestCase):
    def test_normalize_rejects_traversal(self) -> None:
        with self.assertRaises(ValueError):
            file_store.normalize_rel_posix_path("../a.txt")
        with self.assertRaises(ValueError):
            file_store.normalize_rel_posix_path("a/../../b.txt")

    def test_crud_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            note = file_store.create_note(root, title="分类/不会作为路径", content="hello", category_dir="中文/层级")
            self.assertTrue(note.path.startswith("中文/层级/"))

            got = file_store.get_note(root, note.path)
            self.assertIsNotNone(got)
            assert got is not None
            self.assertEqual(got.content, "hello")

            updated = file_store.update_note(root, note.path, new_title="新标题.md", new_content="# hi")
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertTrue(updated.title.endswith(".md"))
            self.assertIn("# hi", updated.content)

            notes = file_store.list_notes(root, limit=50)
            self.assertGreaterEqual(len(notes), 1)

            ok = file_store.delete_note(root, updated.path)
            self.assertTrue(ok)
            self.assertIsNone(file_store.get_note(root, updated.path))


if __name__ == "__main__":
    unittest.main()


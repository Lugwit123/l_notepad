# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from l_notepad.backend_server import create_app


class ApiNotesTests(unittest.TestCase):
    def test_api_crud_by_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db_path = tmp / "data" / "notepad.sqlite3"
            app = create_app(db_path)
            # override notes root for test isolation
            app.state.notes_root = tmp / "notepad_list"

            client = TestClient(app)
            r = client.get("/api/health")
            self.assertEqual(r.status_code, 200)

            r = client.post("/api/notes", json={"title": "中文.md", "content": "# ok", "category": "分类/一级"})
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertIn("path", body)
            path = body["path"]

            r = client.get(f"/api/notes/{path}")
            self.assertEqual(r.status_code, 200)

            r = client.put(f"/api/notes/{path}", json={"title": "改名.mdc", "content": "x"})
            self.assertEqual(r.status_code, 200)
            new_path = r.json()["path"]

            r = client.delete(f"/api/notes/{new_path}")
            self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()


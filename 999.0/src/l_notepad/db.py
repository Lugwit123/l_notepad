# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_updated_at ON notes(updated_at DESC);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_db_path() -> Path:
    root = os.environ.get("L_NOTEPAD_ROOT")
    if root:
        return Path(root) / "data" / "notepad.sqlite3"
    return Path.cwd() / "notepad.sqlite3"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


@dataclass(frozen=True)
class Note:
    id: int
    title: str
    content: str
    created_at: str
    updated_at: str


def list_notes(conn: sqlite3.Connection, limit: int = 200) -> list[Note]:
    cur = conn.execute(
        "SELECT id, title, content, created_at, updated_at FROM notes ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    )
    return [Note(**dict(r)) for r in cur.fetchall()]


def get_note(conn: sqlite3.Connection, note_id: int) -> Note | None:
    cur = conn.execute(
        "SELECT id, title, content, created_at, updated_at FROM notes WHERE id = ?",
        (note_id,),
    )
    row = cur.fetchone()
    return Note(**dict(row)) if row else None


def create_note(conn: sqlite3.Connection, title: str, content: str) -> Note:
    now = _now_iso()
    cur = conn.execute(
        "INSERT INTO notes(title, content, created_at, updated_at) VALUES(?,?,?,?)",
        (title.strip() or "未命名", content, now, now),
    )
    conn.commit()
    note_id = int(cur.lastrowid)
    note = get_note(conn, note_id)
    if not note:
        raise RuntimeError("Create note failed")
    return note


def update_note(conn: sqlite3.Connection, note_id: int, title: str, content: str) -> Note | None:
    now = _now_iso()
    conn.execute(
        "UPDATE notes SET title = ?, content = ?, updated_at = ? WHERE id = ?",
        (title.strip() or "未命名", content, now, note_id),
    )
    conn.commit()
    return get_note(conn, note_id)


def delete_note(conn: sqlite3.Connection, note_id: int) -> bool:
    cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    return cur.rowcount > 0


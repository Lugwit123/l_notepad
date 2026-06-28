# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Iterable


@dataclass(frozen=True)
class FileNoteMeta:
    """List view metadata without reading full file content."""

    path: str
    title: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FileNote:
    path: str  # posix-like relative path under notepad_list
    title: str  # file name
    content: str
    created_at: str
    updated_at: str

    @property
    def is_markdown(self) -> bool:
        v = (self.title or "").strip().lower()
        return v.endswith(".md") or v.endswith(".mdc")

    def content_snippet(self, max_len: int = 220) -> str:
        s = (self.content or "").replace("\r\n", "\n").strip()
        if len(s) <= max_len:
            return s
        return s[: max_len - 1] + "…"


_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]+')
_CONTROL_CHARS = re.compile(r"[\x00-\x1f]+")


def default_root_dir() -> Path:
    return Path(__file__).resolve().parent / "notepad_list"


def ensure_root(root_dir: Path) -> None:
    root_dir.mkdir(parents=True, exist_ok=True)


def _iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def sanitize_title_to_filename(title: str) -> str:
    """
    Windows-safe filename. Keep Chinese and most Unicode; remove control chars; replace reserved characters.
    Automatically adds .md extension if not present.
    """
    v = (title or "").strip()
    if not v:
        v = "未命名"
    v = _CONTROL_CHARS.sub("", v)
    v = _INVALID_FILENAME_CHARS.sub("_", v)
    v = v.strip(" .")
    v = v or "未命名"
    # 自动添加 .md 后缀（如果没有后缀）
    if not v.lower().endswith(('.md', '.mdc', '.txt', '.py', '.json', '.log')):
        v += ".md"
    return v


def normalize_rel_posix_path(rel_path: str) -> str:
    """
    Normalize a relative posix-like path and prevent path traversal.
    """
    rel = (rel_path or "").strip().lstrip("/").replace("\\", "/")
    p = PurePosixPath(rel)
    parts: list[str] = []
    for part in p.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError("invalid path traversal")
        parts.append(part)
    if not parts:
        raise ValueError("empty path")
    return "/".join(parts)


def resolve_note_path(root_dir: Path, rel_posix_path: str) -> Path:
    rel = normalize_rel_posix_path(rel_posix_path)
    p = root_dir.joinpath(*rel.split("/")).resolve()
    root_real = root_dir.resolve()
    if root_real not in p.parents and p != root_real:
        raise ValueError("path escapes root")
    return p


def iter_note_files(root_dir: Path) -> Iterable[Path]:
    if not root_dir.exists():
        return []
    return (p for p in root_dir.rglob("*") if p.is_file())


# 列表场景只需读文件头即可生成摘要，避免把整篇大文件读进内存。
_BRIEF_HEAD_CHARS = 8192


def _scan_files_sorted(root_dir: Path, limit: int) -> list[tuple[Path, os.stat_result]]:
    """扫描一次目录，每个文件只 stat 一次，按修改时间倒序返回受限列表。"""
    ensure_root(root_dir)
    entries: list[tuple[Path, os.stat_result]] = []
    for p in iter_note_files(root_dir):
        try:
            st = p.stat()
        except OSError:
            continue
        entries.append((p, st))
    entries.sort(key=lambda e: e[1].st_mtime, reverse=True)
    return entries[: max(1, limit)]


def list_notes_meta(root_dir: Path, limit: int = 500) -> list[FileNoteMeta]:
    out: list[FileNoteMeta] = []
    for p, st in _scan_files_sorted(root_dir, limit):
        try:
            rel = p.relative_to(root_dir).as_posix()
        except Exception:
            continue
        out.append(
            FileNoteMeta(
                path=rel,
                title=rel,
                created_at=_iso_from_ts(st.st_ctime),
                updated_at=_iso_from_ts(st.st_mtime),
            )
        )
    return out


def list_notes_brief(root_dir: Path, limit: int = 500, *, head_chars: int = _BRIEF_HEAD_CHARS) -> list[FileNote]:
    """列出笔记，每个文件只读开头 head_chars 个字符用于生成摘要。

    适用于只展示标题/时间/摘要、不需要全文的列表场景。
    """
    out: list[FileNote] = []
    for p, st in _scan_files_sorted(root_dir, limit):
        try:
            rel = p.relative_to(root_dir).as_posix()
        except Exception:
            continue
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                content = f.read(head_chars)
        except Exception:
            content = ""
        out.append(
            FileNote(
                path=rel,
                title=rel,
                content=content,
                created_at=_iso_from_ts(st.st_ctime),
                updated_at=_iso_from_ts(st.st_mtime),
            )
        )
    return out


def list_notes(root_dir: Path, limit: int = 500) -> list[FileNote]:
    out: list[FileNote] = []
    for p, st in _scan_files_sorted(root_dir, limit):
        try:
            rel = p.relative_to(root_dir).as_posix()
        except Exception:
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except Exception:
            content = ""
        out.append(
            FileNote(
                path=rel,
                title=rel,
                content=content,
                created_at=_iso_from_ts(st.st_ctime),
                updated_at=_iso_from_ts(st.st_mtime),
            )
        )
    return out


def get_note(root_dir: Path, rel_posix_path: str) -> FileNote | None:
    ensure_root(root_dir)
    try:
        p = resolve_note_path(root_dir, rel_posix_path)
    except ValueError:
        return None
    if not p.exists() or not p.is_file():
        return None
    st = p.stat()
    try:
        content = p.read_text(encoding="utf-8")
    except Exception:
        content = ""
    rel = p.relative_to(root_dir).as_posix()
    return FileNote(
        path=rel,
        title=p.name,
        content=content,
        created_at=_iso_from_ts(st.st_ctime),
        updated_at=_iso_from_ts(st.st_mtime),
    )


def _unique_path(root_dir: Path, target: Path) -> Path:
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    for i in range(2, 10_000):
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
    raise RuntimeError("cannot find unique filename")


def _ensure_extension(filename: str, ext: str = ".md") -> str:
    """确保文件名有指定扩展名。"""
    if not filename.lower().endswith(('.md', '.mdc', '.txt', '.py', '.json', '.log')):
        return filename + ext
    return filename


def create_note(root_dir: Path, title: str, content: str, category_dir: str = "") -> FileNote:
    ensure_root(root_dir)
    filename = sanitize_title_to_filename(title)
    rel_dir = (category_dir or "").strip().lstrip("/").replace("\\", "/")
    rel_dir = str(PurePosixPath(rel_dir)) if rel_dir not in {"", "."} else ""
    if rel_dir.startswith(".."):
        raise ValueError("invalid category")
    base = root_dir.joinpath(
        *([p for p in rel_dir.split("/") if p] if rel_dir else []))
    base.mkdir(parents=True, exist_ok=True)
    target = _unique_path(root_dir, (base / filename))
    target.write_text(content or "", encoding="utf-8")
    rel = target.relative_to(root_dir).as_posix()
    note = get_note(root_dir, rel)
    if not note:
        raise RuntimeError("failed to create note")
    return note


def update_note(
    root_dir: Path,
    rel_posix_path: str,
    *,
    new_title: str | None = None,
    new_content: str | None = None,
) -> FileNote | None:
    ensure_root(root_dir)
    note = get_note(root_dir, rel_posix_path)
    if not note:
        return None
    p = resolve_note_path(root_dir, note.path)
    if new_content is not None:
        p.write_text(new_content, encoding="utf-8")
    if new_title is not None:
        # 只使用文件名重命名，避免用户输入包含 '/' 的路径破坏文件名
        new_name_raw = new_title.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        new_name = sanitize_title_to_filename(new_name_raw)
        if new_name and new_name != p.name:
            new_p = _unique_path(root_dir, p.with_name(new_name))
            p.rename(new_p)
            p = new_p
    rel = p.relative_to(root_dir).as_posix()
    return get_note(root_dir, rel)


def move_note(root_dir: Path, rel_posix_path: str, dst_dir: str) -> FileNote | None:
    """把笔记文件移动到 dst_dir 目录下（保持原文件名），返回新位置的笔记。"""
    ensure_root(root_dir)
    note = get_note(root_dir, rel_posix_path)
    if not note:
        return None
    src = resolve_note_path(root_dir, note.path)
    rel_dir = (dst_dir or "").strip().lstrip("/").replace("\\", "/")
    rel_dir = str(PurePosixPath(rel_dir)) if rel_dir not in {"", "."} else ""
    if rel_dir.startswith(".."):
        raise ValueError("invalid dest dir")
    base = (
        root_dir.joinpath(*[p for p in rel_dir.split("/") if p])
        if rel_dir
        else root_dir
    )
    base.mkdir(parents=True, exist_ok=True)
    target = base / src.name
    if target.resolve() == src.resolve():
        return get_note(root_dir, src.relative_to(root_dir).as_posix())
    target = _unique_path(root_dir, target)
    src.rename(target)
    return get_note(root_dir, target.relative_to(root_dir).as_posix())


def delete_note(root_dir: Path, rel_posix_path: str) -> bool:
    ensure_root(root_dir)
    try:
        p = resolve_note_path(root_dir, rel_posix_path)
    except ValueError:
        return False
    if not p.exists() or not p.is_file():
        return False
    p.unlink()
    # cleanup empty parents up to root
    cur = p.parent
    root_real = root_dir.resolve()
    while cur != root_real:
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent
    return True

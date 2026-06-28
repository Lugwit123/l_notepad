# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import uvicorn

from . import db as dbmod
from . import file_store


def _root_base(request: Request) -> str:
    return (request.scope.get("root_path") or "").rstrip("/")


def _web_base(request: Request) -> str:
    root_base = _root_base(request)
    return f"{root_base}/web" if root_base else "/web"


def _mounted_url(request: Request, path: str) -> str:
    path = path.lstrip("/")
    root_base = _root_base(request)
    return f"{root_base}/{path}" if root_base else f"/{path}"


def _parse_db_path(value: str | None) -> Path:
    if value:
        return Path(value)
    env_path = os.environ.get("L_NOTEPAD_DB")
    if env_path:
        return Path(env_path)
    return dbmod.default_db_path()


class NoteCreate(BaseModel):
    title: str = Field(default="未命名", max_length=200)
    content: str = Field(default="")
    category: str = Field(default="", description="optional directory path under notepad_list")


class NoteUpdate(BaseModel):
    title: str = Field(default="未命名", max_length=200)
    content: str = Field(default="")


class LogEntryOut(BaseModel):
    path: str
    size: int
    mtime: str


SERVER_LOG_DIR = Path(os.environ.get("L_NOTEPAD_LOG_DIR", r"D:\Temp\Log"))


class NoteOut(BaseModel):
    path: str
    title: str
    content: str
    created_at: str
    updated_at: str
    is_md: bool = False

    @staticmethod
    def from_file_note(note: file_store.FileNote, *, include_content: bool = True) -> "NoteOut":
        return NoteOut(
            path=note.path,
            title=note.title,
            content=note.content if include_content else note.content_snippet(),
            created_at=note.created_at,
            updated_at=note.updated_at,
            is_md=note.is_markdown,
        )


def create_app(db_path: Path) -> FastAPI:
    app = FastAPI(title="L Notepad", version="1.0")
    conn = dbmod.connect(db_path)
    dbmod.init_db(conn)

    templates_dir = Path(__file__).resolve().parent / "templates"
    static_dir = Path(__file__).resolve().parent / "static"
    templates = Jinja2Templates(directory=str(templates_dir))
    app.state.templates = templates
    app.state.conn = conn
    notes_root = Path(__file__).resolve().parent / "notepad_list"
    file_store.ensure_root(notes_root)
    app.state.notes_root = notes_root

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        resp = await call_next(request)
        # Skip CSP for auto-generated API docs (Swagger UI / ReDoc load CDN resources).
        if request.url.path in ("/docs", "/redoc"):
            return resp
        # Minimal CSP: keep scripts local (but allow inline scripts in existing templates).
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "base-uri 'self'; "
            "frame-ancestors 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "font-src 'self' data:;",
        )
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon_svg() -> FileResponse:
        return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon_ico() -> FileResponse:
        # Reuse the SVG icon to avoid browser 404s.
        return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")

    @app.get("/api/notes", response_model=list[NoteOut])
    def list_notes(limit: int = 200) -> list[NoteOut]:
        # 列表只返回摘要，读文件头即可，避免读取整篇全文。
        notes = file_store.list_notes_brief(notes_root, limit=limit)
        return [NoteOut.from_file_note(n, include_content=False) for n in notes]

    @app.post("/api/notes", response_model=NoteOut)
    def create_note(payload: NoteCreate) -> NoteOut:
        note = file_store.create_note(notes_root, title=payload.title, content=payload.content, category_dir=payload.category)
        return NoteOut.from_file_note(note, include_content=True)

    @app.get("/api/notes/{note_path:path}", response_model=NoteOut)
    def get_note(note_path: str) -> NoteOut:
        note = file_store.get_note(notes_root, note_path)
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        return NoteOut.from_file_note(note, include_content=True)

    @app.put("/api/notes/{note_path:path}", response_model=NoteOut)
    def update_note(note_path: str, payload: NoteUpdate) -> NoteOut:
        note = file_store.update_note(notes_root, note_path, new_title=payload.title, new_content=payload.content)
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        return NoteOut.from_file_note(note, include_content=True)

    @app.delete("/api/notes/{note_path:path}")
    def delete_note(note_path: str) -> dict[str, Any]:
        ok = file_store.delete_note(notes_root, note_path)
        if not ok:
            raise HTTPException(status_code=404, detail="Note not found")
        return {"ok": True}

    # ---- Server Log API ----

    @app.get("/api/logs", response_model=list[LogEntryOut])
    def list_logs(max_size: int = 2 * 1024 * 1024) -> list[LogEntryOut]:
        """List server log files from SERVER_LOG_DIR."""
        log_root = SERVER_LOG_DIR
        if not log_root.exists() or not log_root.is_dir():
            return []
        from datetime import datetime as _dt
        result: list[LogEntryOut] = []
        for p in sorted(log_root.rglob("*")):
            if not p.is_file():
                continue
            if p.name.startswith("."):
                continue
            try:
                st = p.stat()
                if st.st_size > max_size:
                    continue
            except OSError:
                continue
            rel = p.relative_to(log_root).as_posix()
            result.append(LogEntryOut(
                path=rel,
                size=st.st_size,
                mtime=_dt.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            ))
        return result

    @app.get("/api/logs/{log_path:path}")
    def get_log(log_path: str) -> dict[str, Any]:
        """Get the content of a specific server log file."""
        log_root = SERVER_LOG_DIR
        target = (log_root / log_path.replace("/", os.sep)).resolve()
        # Security: ensure the path is within log_root
        if log_root.resolve() not in target.parents and target != log_root.resolve():
            raise HTTPException(status_code=403, detail="Access denied")
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="Log file not found")
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Read failed: {e}")
        return {"path": log_path, "content": content}

    @app.put("/api/logs/{log_path:path}")
    def update_log(log_path: str, payload: NoteUpdate) -> dict[str, Any]:
        """Update (overwrite) a server log file."""
        log_root = SERVER_LOG_DIR
        target = (log_root / log_path.replace("/", os.sep)).resolve()
        if log_root.resolve() not in target.parents and target != log_root.resolve():
            raise HTTPException(status_code=403, detail="Access denied")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload.content, encoding="utf-8")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Write failed: {e}")
        return {"ok": True}

    @app.delete("/api/logs/{log_path:path}")
    def delete_log(log_path: str) -> dict[str, Any]:
        """Delete a server log file."""
        log_root = SERVER_LOG_DIR
        target = (log_root / log_path.replace("/", os.sep)).resolve()
        if log_root.resolve() not in target.parents and target != log_root.resolve():
            raise HTTPException(status_code=403, detail="Access denied")
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="Log file not found")
        try:
            target.unlink()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Delete failed: {e}")
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        # 状态页只展示标题与时间，使用 meta 列表，零文件内容读取。
        notes = file_store.list_notes_meta(notes_root, limit=200)
        # Starlette 1.0+ expects (request, name, context). Older versions accepted (name, context).
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "notes": notes,
                "root_base": _root_base(request),
                "web_base": _web_base(request),
                "_mounted_url": _mounted_url,
            },
        )

    # ---- Web UI (Jinja2) ----

    @app.get("/web", response_class=HTMLResponse)
    def web_list(request: Request, q: str = "") -> HTMLResponse:
        notes = file_store.list_notes(notes_root, limit=500)
        query = (q or "").strip().lower()
        if query:
            notes = [n for n in notes if query in n.title.lower() or query in n.content.lower()]
        return templates.TemplateResponse(
            request,
            "web_list.html",
            {
                "notes": notes,
                "q": q,
                "active_note_path": None,
                "root_base": _root_base(request),
                "web_base": _web_base(request),
                "_mounted_url": _mounted_url,
            },
        )

    @app.get("/web/new", response_class=HTMLResponse)
    def web_new(request: Request) -> HTMLResponse:
        # 编辑页侧栏只显示标题/时间/短摘要，读文件头即可。
        notes = file_store.list_notes_brief(notes_root, limit=500)
        return templates.TemplateResponse(
            request,
            "web_edit.html",
            {
                "note": None,
                "mode": "new",
                "notes": notes,
                "active_note_path": None,
                "root_base": _root_base(request),
                "web_base": _web_base(request),
                "_mounted_url": _mounted_url,
            },
        )

    @app.post("/web/new")
    async def web_new_post(request: Request) -> RedirectResponse:
        form = await request.form()
        title = str(form.get("title", "")).strip() or "未命名"
        content = str(form.get("content", ""))
        note = file_store.create_note(notes_root, title=title, content=content)
        return RedirectResponse(url=_mounted_url(request, f"web/{note.path}"), status_code=303)

    @app.get("/web/{note_path:path}", response_class=HTMLResponse)
    def web_edit(request: Request, note_path: str) -> HTMLResponse:
        # 编辑页侧栏只需短摘要；当前笔记全文单独 get_note 读取。
        notes = file_store.list_notes_brief(notes_root, limit=500)
        note = file_store.get_note(notes_root, note_path)
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        return templates.TemplateResponse(
            request,
            "web_edit.html",
            {
                "note": note,
                "mode": "edit",
                "notes": notes,
                "active_note_path": note_path,
                "root_base": _root_base(request),
                "web_base": _web_base(request),
                "_mounted_url": _mounted_url,
            },
        )

    @app.post("/web/{note_path:path}")
    async def web_edit_post(request: Request, note_path: str) -> RedirectResponse:
        form = await request.form()
        title = str(form.get("title", "")).strip() or "未命名"
        content = str(form.get("content", ""))
        note = file_store.update_note(notes_root, note_path, new_title=title, new_content=content)
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        return RedirectResponse(url=_mounted_url(request, f"web/{note.path}"), status_code=303)

    @app.post("/web/{note_path:path}/delete")
    async def web_delete_post(request: Request, note_path: str) -> RedirectResponse:
        file_store.delete_note(notes_root, note_path)
        return RedirectResponse(url=_mounted_url(request, "web"), status_code=303)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L Notepad backend server")
    parser.add_argument("--host", default=os.environ.get("L_NOTEPAD_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("L_NOTEPAD_PORT", "8765")))
    parser.add_argument("--db", default=None, help="sqlite db path (default: package data/notepad.sqlite3)")
    parser.add_argument("--log-level", default=os.environ.get("L_NOTEPAD_LOG_LEVEL", "info"))
    parser.add_argument(
        "--reload",
        action="store_true",
        default=os.environ.get("L_NOTEPAD_RELOAD", "").strip() in {"1", "true", "True", "yes", "YES"},
        help="Enable auto-reload (dev only)",
    )
    args = parser.parse_args(argv)

    app = create_app(_parse_db_path(args.db))
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level, reload=bool(args.reload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


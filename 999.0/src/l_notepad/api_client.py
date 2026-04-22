# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class ApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class NoteDto:
    id: int
    title: str
    content: str
    created_at: str
    updated_at: str


def _read_json(resp) -> Any:
    raw = resp.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


class NotepadApi:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def health(self) -> bool:
        data = self._get("/api/health")
        return bool(data and data.get("ok"))

    def list_notes(self) -> list[NoteDto]:
        data = self._get("/api/notes")
        return [NoteDto(**x) for x in (data or [])]

    def get_note(self, note_id: int) -> NoteDto:
        data = self._get(f"/api/notes/{note_id}")
        return NoteDto(**data)

    def create_note(self, title: str, content: str) -> NoteDto:
        data = self._post("/api/notes", {"title": title, "content": content})
        return NoteDto(**data)

    def update_note(self, note_id: int, title: str, content: str) -> NoteDto:
        data = self._put(f"/api/notes/{note_id}", {"title": title, "content": content})
        return NoteDto(**data)

    def delete_note(self, note_id: int) -> None:
        self._delete(f"/api/notes/{note_id}")

    def _get(self, path: str) -> Any:
        return self._request("GET", path, None)

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload)

    def _put(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("PUT", path, payload)

    def _delete(self, path: str) -> Any:
        return self._request("DELETE", path, None)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> Any:
        url = f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return _read_json(resp)
        except urllib.error.HTTPError as e:
            body = e.read()
            msg = body.decode("utf-8", errors="ignore") if body else str(e)
            raise ApiError(f"{method} {url} failed: {e.code} {msg}") from e
        except Exception as e:
            raise ApiError(f"{method} {url} failed: {e}") from e


# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from . import file_store
from .api_client import ApiError, LogDto, NoteDto
from .folder_favorites_hotkey import FolderFavoritesHotkeyService
from .ui import MainWindow


DOUBLE_CTRL_MIN_GAP_SEC = 0.05
DOUBLE_CTRL_MAX_GAP_SEC = 0.15

# 可选热键：名称 -> (vk_codes 集合, 显示名)
HOTKEY_OPTIONS: dict[str, tuple[set[int], str]] = {
    "Ctrl":   ({0x11, 0xA2, 0xA3}, "Ctrl"),
    "Alt":    ({0x12, 0xA4, 0xA5}, "Alt"),
    "Shift":  ({0x10, 0xA0, 0xA1}, "Shift"),
}
DEFAULT_HOTKEY_KEY = "Ctrl"


class DoubleKeyWatcher:
    """Listary-style 全局双击按键监听，支持可配置热键。"""

    def __init__(
        self,
        callback,
        log_callback=None,
        min_gap_sec: float = DOUBLE_CTRL_MIN_GAP_SEC,
        max_gap_sec: float = DOUBLE_CTRL_MAX_GAP_SEC,
        hotkey_key: str = DEFAULT_HOTKEY_KEY,
    ) -> None:
        self._callback = callback
        self._log_callback = log_callback
        self._min_gap_sec = float(min_gap_sec)
        self._max_gap_sec = float(max_gap_sec)
        self._hotkey_key = hotkey_key
        self._key_codes = HOTKEY_OPTIONS.get(hotkey_key, HOTKEY_OPTIONS[DEFAULT_HOTKEY_KEY])[0]
        self._last_triggered_at = 0.0
        self._lock = threading.Lock()
        self._pending_logs: list[str] = []
        self._trigger_pending = False
        self._key_down = False
        self._last_key_press_at = 0.0
        self._hook_id = None
        self._hook_proc = None
        self._hook_thread_id = 0
        self._flush_timer = QtCore.QTimer()
        self._flush_timer.setInterval(15)
        self._flush_timer.timeout.connect(self._flush_pending)
        self._flush_timer.start()
        if sys.platform == "win32":
            key_name = HOTKEY_OPTIONS.get(hotkey_key, HOTKEY_OPTIONS[DEFAULT_HOTKEY_KEY])[1]
            self._log(
                f"启动低级键盘 Hook 双击 {key_name} 监听，"
                f"有效间隔 {self._min_gap_sec:.2f}-{self._max_gap_sec:.2f}s"
            )
            self._hook_thread = threading.Thread(target=self._run_keyboard_hook, daemon=True)
            self._hook_thread.start()
        else:
            self._log("当前平台暂不支持全局双击热键监听")

    def _log(self, message: str) -> None:
        with self._lock:
            self._pending_logs.append(message)

    def _on_key_pressed(self) -> None:
        now = time.monotonic()
        gap = now - self._last_key_press_at
        key_name = HOTKEY_OPTIONS.get(self._hotkey_key, HOTKEY_OPTIONS[DEFAULT_HOTKEY_KEY])[1]
        in_range = self._min_gap_sec <= gap <= self._max_gap_sec
        self._log(
            f"{key_name} 按下，间隔 {gap:.3f}s"
            f"（有效范围 {self._min_gap_sec:.2f}~{self._max_gap_sec:.2f}s"
            f"{'✅ 命中' if in_range else '❌ 未命中'}）"
        )
        if in_range:
            self._trigger()
            self._last_key_press_at = 0.0
        else:
            self._last_key_press_at = now

    def update_interval(self, max_gap_sec: float) -> None:
        try:
            value = max(self._min_gap_sec, min(1.0, float(max_gap_sec)))
        except Exception:
            return
        self._max_gap_sec = value
        key_name = HOTKEY_OPTIONS.get(self._hotkey_key, HOTKEY_OPTIONS[DEFAULT_HOTKEY_KEY])[1]
        self._log(
            f"双击 {key_name} 间隔已更新，"
            f"有效间隔 {self._min_gap_sec:.2f}-{self._max_gap_sec:.2f}s"
        )

    def update_key(self, hotkey_key: str) -> None:
        """更新监听的热键。"""
        if hotkey_key not in HOTKEY_OPTIONS:
            return
        self._hotkey_key = hotkey_key
        self._key_codes = HOTKEY_OPTIONS[hotkey_key][0]
        self._key_down = False
        self._last_key_press_at = 0.0
        key_name = HOTKEY_OPTIONS[hotkey_key][1]
        self._log(f"热键已切换为: 双击 {key_name}")

    def _run_keyboard_hook(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            wh_keyboard_ll = 13
            wm_keydown = 0x0100
            wm_keyup = 0x0101
            wm_syskeydown = 0x0104
            wm_syskeyup = 0x0105
            wm_quit = 0x0012
            hc_action = 0

            low_level_keyboard_proc = ctypes.WINFUNCTYPE(
                wintypes.LPARAM,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int,
                low_level_keyboard_proc,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.CallNextHookEx.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.CallNextHookEx.restype = wintypes.LPARAM
            user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            user32.PostThreadMessageW.argtypes = [
                wintypes.DWORD,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD

            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("vkCode", wintypes.DWORD),
                    ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p),
                ]

            def _proc(n_code, w_param, l_param):
                if n_code == hc_action:
                    info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    vk = int(info.vkCode)
                    msg = int(w_param)
                    if vk in self._key_codes and msg in (wm_keydown, wm_syskeydown):
                        if not self._key_down:
                            self._key_down = True
                            self._on_key_pressed()
                    elif vk in self._key_codes and msg in (wm_keyup, wm_syskeyup):
                        self._key_down = False
                return user32.CallNextHookEx(self._hook_id, n_code, w_param, l_param)

            self._hook_proc = low_level_keyboard_proc(_proc)
            self._hook_thread_id = kernel32.GetCurrentThreadId()
            self._hook_id = user32.SetWindowsHookExW(
                wh_keyboard_ll,
                self._hook_proc,
                kernel32.GetModuleHandleW(None),
                0,
            )
            if not self._hook_id:
                self._log("低级键盘 Hook 注册失败")
                return
            key_name = HOTKEY_OPTIONS.get(self._hotkey_key, HOTKEY_OPTIONS[DEFAULT_HOTKEY_KEY])[1]
            self._log(f"低级键盘 Hook 注册成功，等待双击 {key_name}")

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
                if msg.message == wm_quit:
                    break
        except Exception as exc:
            self._log(f"低级键盘 Hook 异常: {exc}")

    def _trigger(self) -> None:
        now = time.monotonic()
        if now - self._last_triggered_at < 0.35:
            self._log("快捷键触发过快，已防抖忽略")
            return
        self._last_triggered_at = now
        key_name = HOTKEY_OPTIONS.get(self._hotkey_key, HOTKEY_OPTIONS[DEFAULT_HOTKEY_KEY])[1]
        self._log(f"双击 {key_name} 已触发")
        with self._lock:
            self._trigger_pending = True

    def _flush_pending(self) -> None:
        with self._lock:
            logs = self._pending_logs[:]
            self._pending_logs.clear()
            trigger_pending = self._trigger_pending
            self._trigger_pending = False
        if self._log_callback is not None:
            for message in logs:
                try:
                    self._log_callback(message)
                except Exception:
                    pass
        if trigger_pending:
            try:
                self._callback()
            except Exception:
                self._log("快捷键回调执行失败")

    def release(self) -> None:
        self._flush_timer.stop()
        if sys.platform == "win32" and self._hook_id:
            try:
                import ctypes

                ctypes.windll.user32.UnhookWindowsHookEx(self._hook_id)
                if self._hook_thread_id:
                    ctypes.windll.user32.PostThreadMessageW(self._hook_thread_id, 0x0012, 0, 0)
                self._log("低级键盘 Hook 已释放")
            except Exception:
                self._log("低级键盘 Hook 释放失败")
            self._hook_id = None


def _setup_tray_icon(
    app: QtWidgets.QApplication,
    win: MainWindow,
    icon: QtGui.QIcon,
) -> QtWidgets.QSystemTrayIcon | None:
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        return None

    tray = QtWidgets.QSystemTrayIcon(icon, app)
    tray.setToolTip("L Notepad")

    menu = QtWidgets.QMenu()
    act_show = menu.addAction("显示/恢复")
    act_hide = menu.addAction("最小化到托盘")
    menu.addSeparator()
    act_quit = menu.addAction("退出")

    def _show_window() -> None:
        win.show_from_hotkey()

    def _hide_window() -> None:
        win.showMinimized()

    def _quit_app() -> None:
        # ui.py closes-to-tray by default; force real close before quitting.
        setattr(win, "_allow_close", True)
        win.close()
        app.quit()

    act_show.triggered.connect(_show_window)
    act_hide.triggered.connect(_hide_window)
    act_quit.triggered.connect(_quit_app)

    tray.setContextMenu(menu)

    def _on_activated(reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
            QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            _show_window()

    tray.activated.connect(_on_activated)
    tray.show()
    return tray


def _set_windows_appid(appid: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)
    except Exception:
        pass


@dataclass(frozen=True)
class _IndexedNote:
    note_id: int
    rel_path: str
    dto: NoteDto


def _stable_note_id(rel_path: str) -> int:
    """Stable int id from relative path (survives mtime sort changes)."""
    raw = zlib.crc32(rel_path.encode("utf-8")) & 0x7FFFFFFF
    return raw if raw != 0 else 1


class LocalNotepadApi:
    """Provide the same interface as NotepadApi, backed by local files."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or file_store.default_root_dir()
        file_store.ensure_root(self.root_dir)
        self._index: list[_IndexedNote] | None = None
        self._by_id: dict[int, _IndexedNote] | None = None

    def list_notes(self) -> list[NoteDto]:
        return [x.dto for x in self._indexed_notes()]

    def get_note(self, note_id: int) -> NoteDto:
        indexed = self._find_by_id(note_id)
        if indexed is None:
            raise ApiError(f"note not found: {note_id}")
        note = file_store.get_note(self.root_dir, indexed.rel_path)
        if note is None:
            self._invalidate_index()
            raise ApiError(f"note not found: {note_id}")
        return self._to_dto(indexed.note_id, note)

    def create_note(self, title: str, content: str) -> NoteDto:
        try:
            created = file_store.create_note(self.root_dir, title=title, content=content)
        except Exception as exc:
            raise ApiError(f"create note failed: {exc}") from exc
        self._invalidate_index()
        note_id = _stable_note_id(created.path)
        return self._to_dto(note_id, created)

    def update_note(self, note_id: int, title: str, content: str) -> NoteDto:
        indexed = self._find_by_id(note_id)
        if indexed is None:
            raise ApiError(f"note not found: {note_id}")
        try:
            updated = file_store.update_note(
                self.root_dir,
                indexed.rel_path,
                new_title=title,
                new_content=content,
            )
        except Exception as exc:
            raise ApiError(f"update note failed: {exc}") from exc
        if updated is None:
            raise ApiError(f"note not found: {note_id}")
        self._invalidate_index()
        new_id = _stable_note_id(updated.path)
        return self._to_dto(new_id, updated)

    def delete_note(self, note_id: int) -> None:
        indexed = self._find_by_id(note_id)
        if indexed is None:
            raise ApiError(f"note not found: {note_id}")
        try:
            ok = file_store.delete_note(self.root_dir, indexed.rel_path)
        except Exception as exc:
            raise ApiError(f"delete note failed: {exc}") from exc
        if not ok:
            raise ApiError(f"note not found: {note_id}")
        self._invalidate_index()

    def _invalidate_index(self) -> None:
        self._index = None
        self._by_id = None

    def _find_by_id(self, note_id: int) -> _IndexedNote | None:
        self._ensure_index()
        assert self._by_id is not None
        return self._by_id.get(int(note_id))

    def _ensure_index(self) -> None:
        if self._index is not None and self._by_id is not None:
            return
        metas = file_store.list_notes_meta(self.root_dir, limit=10_000)
        index: list[_IndexedNote] = []
        by_id: dict[int, _IndexedNote] = {}
        for meta in metas:
            note_id = _stable_note_id(meta.path)
            dto = self._meta_to_dto(note_id, meta)
            item = _IndexedNote(note_id=note_id, rel_path=meta.path, dto=dto)
            index.append(item)
            by_id[note_id] = item
        self._index = index
        self._by_id = by_id

    def _indexed_notes(self) -> list[_IndexedNote]:
        self._ensure_index()
        assert self._index is not None
        return self._index

    @staticmethod
    def _meta_to_dto(note_id: int, meta: file_store.FileNoteMeta) -> NoteDto:
        return NoteDto(
            id=int(note_id),
            title=meta.title,
            content="",
            created_at=meta.created_at,
            updated_at=meta.updated_at,
        )

    @staticmethod
    def _to_dto(note_id: int, note: file_store.FileNote) -> NoteDto:
        return NoteDto(
            id=int(note_id),
            title=note.title,
            content=note.content,
            created_at=note.created_at,
            updated_at=note.updated_at,
        )

    # ---- Server Log API (local filesystem / remote server) ----

    @staticmethod
    def _log_server_url() -> str:
        """Return remote log server URL. Default: http://121.196.144.88:8765"""
        import os
        server = os.environ.get("L_NOTEPAD_LOG_SERVER", "http://121.196.144.88:8765").strip()
        if not server.startswith(("http://", "https://")):
            port = os.environ.get("L_NOTEPAD_PORT", "8765")
            server = f"http://{server}:{port}"
        return server.rstrip("/")

    @staticmethod
    def _log_dir() -> Path:
        import os
        return Path(os.environ.get("L_NOTEPAD_LOG_DIR", r"D:\Temp\Log"))

    def list_logs(self, max_size: int = 2 * 1024 * 1024) -> list[LogDto]:
        """List server log files from remote server, fallback to local on failure."""
        remote_url = self._log_server_url()
        try:
            return self._list_logs_remote(remote_url)
        except Exception as e:
            # 远程获取失败时降级到本地
            print(f"[l_notepad] WARNING: Remote log list failed ({e}), falling back to local")
            return self._list_logs_local(max_size)

    def _list_logs_remote(self, base_url: str) -> list[LogDto]:
        """Fetch log list from remote server via HTTP."""
        import json
        import urllib.request
        import urllib.error
        try:
            req = urllib.request.Request(f"{base_url}/api/logs")
            with urllib.request.urlopen(req, timeout=3) as resp:  # 减少超时时间到 3 秒
                data = json.loads(resp.read().decode("utf-8"))
            return [LogDto(**x) for x in (data or [])]
        except Exception as e:
            print(f"[l_notepad] ERROR: Remote log list failed: {e}")
            raise ApiError(f"Remote log list failed: {e}") from e

    def _list_logs_local(self, max_size: int = 2 * 1024 * 1024) -> list[LogDto]:
        """List server log files from local filesystem."""
        from datetime import datetime as _dt
        log_root = self._log_dir()
        if not log_root.exists() or not log_root.is_dir():
            return []
        result: list[LogDto] = []
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
            result.append(LogDto(
                path=rel,
                size=st.st_size,
                mtime=_dt.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            ))
        return result

    def get_log(self, log_path: str) -> dict[str, str]:
        """Get server log content from remote server."""
        remote_url = self._log_server_url()
        return self._get_log_remote(remote_url, log_path)

    def _get_log_remote(self, base_url: str, log_path: str) -> dict[str, str]:
        """Fetch log content from remote server via HTTP."""
        import json
        import urllib.request
        import urllib.error
        try:
            req = urllib.request.Request(f"{base_url}/api/logs/{log_path}")
            with urllib.request.urlopen(req, timeout=5) as resp:  # 获取日志内容，超时 5 秒
                data = json.loads(resp.read().decode("utf-8"))
            return data
        except Exception as e:
            print(f"[l_notepad] ERROR: Remote get log failed: {e}")
            raise ApiError(f"Remote get log failed: {e}") from e

    def _get_log_local(self, log_path: str) -> dict[str, str]:
        """Get server log content from local filesystem."""
        import os
        log_root = self._log_dir()
        target = (log_root / log_path.replace("/", os.sep)).resolve()
        if log_root.resolve() not in target.parents and target != log_root.resolve():
            raise ApiError(f"Access denied: {log_path}")
        if not target.exists() or not target.is_file():
            raise ApiError(f"Log file not found: {log_path}")
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"path": log_path, "content": content}

    def update_log(self, log_path: str, content: str) -> None:
        """Update (overwrite) a server log file via remote or local."""
        remote_url = self._log_server_url()
        if remote_url:
            self._update_log_remote(remote_url, log_path, content)
        else:
            self._update_log_local(log_path, content)

    def _update_log_remote(self, base_url: str, log_path: str, content: str) -> None:
        """Update log on remote server via HTTP PUT."""
        import json
        import urllib.request
        import urllib.error
        try:
            payload = json.dumps({"title": "", "content": content}).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}/api/logs/{log_path}",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:  # 更新日志，超时 5 秒
                pass
        except Exception as e:
            print(f"[l_notepad] ERROR: Remote update log failed: {e}")
            raise ApiError(f"Remote update log failed: {e}") from e

    def _update_log_local(self, log_path: str, content: str) -> None:
        """Update log on local filesystem."""
        import os
        log_root = self._log_dir()
        target = (log_root / log_path.replace("/", os.sep)).resolve()
        if log_root.resolve() not in target.parents and target != log_root.resolve():
            raise ApiError(f"Access denied: {log_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _acquire_single_instance_lock(app: QtWidgets.QApplication) -> QtCore.QLockFile | None:
    """PC 模式单实例：使用 QLockFile 避免多开。"""
    data_dir_str = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.AppDataLocation
    )
    base_dir = Path(data_dir_str or Path.home())
    lock_path = base_dir / "l_notepad_pc.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock = QtCore.QLockFile(str(lock_path))
    # 不允许陈旧锁自动失效，要求用户手动清理。
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QtWidgets.QMessageBox.warning(
            None,
            "L Notepad 已在运行",
            "L Notepad（PC 本地模式）已经有一个实例在运行，禁止多开。",
        )
        return None
    return lock


def main() -> int:
    _set_windows_appid("Lugwit.l_notepad.pc")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    lock = _acquire_single_instance_lock(app)
    if lock is None:
        return 1
    icon_path = Path(__file__).resolve().parent / "static" / "favicon.svg"
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    api = LocalNotepadApi()

    def _restart_process() -> None:
        src_dir = str(Path(__file__).resolve().parents[1])
        ok = QtCore.QProcess.startDetached(
            sys.executable,
            ["-m", "l_notepad.local_main"],
            src_dir,
        )
        if not ok:
            raise RuntimeError("QProcess.startDetached failed")

    settings = QtCore.QSettings("Lugwit", "l_notepad_pc")
    max_gap_raw = settings.value("hotkey/double_ctrl_max_gap_sec", str(DOUBLE_CTRL_MAX_GAP_SEC))
    try:
        max_gap_sec = float(str(max_gap_raw))
    except Exception:
        max_gap_sec = DOUBLE_CTRL_MAX_GAP_SEC
    max_gap_sec = max(DOUBLE_CTRL_MIN_GAP_SEC, min(1.0, max_gap_sec))

    hotkey_key = str(settings.value("hotkey/double_key", DEFAULT_HOTKEY_KEY))
    if hotkey_key not in HOTKEY_OPTIONS:
        hotkey_key = DEFAULT_HOTKEY_KEY

    hotkey_ref: dict[str, DoubleKeyWatcher] = {}

    def _update_hotkey_interval(value: float) -> None:
        watcher = hotkey_ref.get("watcher")
        if watcher is not None:
            watcher.update_interval(value)

    def _update_hotkey_key(key: str) -> None:
        watcher = hotkey_ref.get("watcher")
        if watcher is not None:
            watcher.update_key(key)

    win = MainWindow(
        api,
        restart_callback=_restart_process,
        hotkey_interval_callback=_update_hotkey_interval,
        hotkey_key_callback=_update_hotkey_key,
    )
    tray = _setup_tray_icon(app, win, app.windowIcon())
    win.set_tray_icon(tray)

    def _append_log(message: str) -> None:
        QtCore.QMetaObject.invokeMethod(
            win,
            "append_log",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(str, message),
        )

    ff_hotkey = FolderFavoritesHotkeyService(win)

    def _on_ff_hotkey_triggered() -> None:
        QtCore.QMetaObject.invokeMethod(
            win,
            "show_folder_favorites_from_hotkey",
            QtCore.Qt.ConnectionType.QueuedConnection,
        )

    def _on_ff_hotkey_started(ok: bool) -> None:
        if ok:
            _append_log("文件夹收藏全局快捷键已启动 (Ctrl+鼠标)")
        else:
            _append_log("文件夹收藏全局快捷键未启动")

    def _on_ff_hotkey_failed(message: str) -> None:
        _append_log(f"[WARN] {message}")

    def _update_folder_favorites_hotkey(button: str) -> None:
        ff_hotkey.set_trigger_button(button)
        label = "中键" if button == "middle" else "左键"
        _append_log(f"文件夹收藏快捷键已切换为 Ctrl+{label}")

    win._folder_favorites_hotkey_callback = _update_folder_favorites_hotkey
    ff_hotkey.started.connect(_on_ff_hotkey_started)
    ff_hotkey.failed.connect(_on_ff_hotkey_failed)
    ff_hotkey.start(_on_ff_hotkey_triggered)

    hotkey = DoubleKeyWatcher(
        lambda: QtCore.QMetaObject.invokeMethod(
            win, "show_from_hotkey", QtCore.Qt.ConnectionType.QueuedConnection
        ),
        log_callback=_append_log,
        min_gap_sec=DOUBLE_CTRL_MIN_GAP_SEC,
        max_gap_sec=max_gap_sec,
        hotkey_key=hotkey_key,
    )
    hotkey_ref["watcher"] = hotkey
    win.show()
    code = int(app.exec())
    hotkey.release()
    ff_hotkey.stop()
    if tray is not None:
        tray.hide()
    if lock is not None:
        lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())

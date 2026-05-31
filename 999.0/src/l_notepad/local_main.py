# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .api_client import ApiError, NoteDto
from . import file_store
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


def main() -> int:
    _set_windows_appid("Lugwit.l_notepad.pc")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
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
    if tray is not None:
        tray.hide()
    return code


if __name__ == "__main__":
    raise SystemExit(main())

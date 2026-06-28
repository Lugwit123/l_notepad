# -*- coding: utf-8 -*-
"""l_notepad 内嵌的文件夹收藏全局快捷键（Ctrl + 中键/左键）。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Callable

from PySide6 import QtCore

BUTTON_MSGS = {
    "middle": {"down": 0x0207, "name": "中键", "vk": 0x04},
    "left": {"down": 0x0201, "name": "左键", "vk": 0x01},
}

WH_MOUSE_LL = 14
GIT_HTTP_CONNECT_TIMEOUT_SEC = 5

logger = logging.getLogger(__name__)


def _safe_log(message: str, level: int = logging.DEBUG) -> None:
    try:
        logger.log(level, message)
    except Exception:
        pass


def _get_config_file() -> Path:
    app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
    cfg_dir = Path(app_data) / "l_folder_favorites"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "config.json"


def load_hotkey_button() -> str:
    """从配置文件加载快捷键按钮设置，返回 'middle' 或 'left'。"""
    try:
        button = json.loads(_get_config_file().read_text(encoding="utf-8")).get(
            "hotkey_button", "middle"
        )
    except Exception:
        button = "middle"
    return button if button in BUTTON_MSGS else "middle"


def save_hotkey_button(button: str) -> None:
    """保存快捷键按钮设置到配置文件。"""
    button = button if button in BUTTON_MSGS else "middle"
    cfg_file = _get_config_file()
    config = {}
    try:
        config = json.loads(cfg_file.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            config = {}
    except Exception:
        pass
    config["hotkey_button"] = button
    cfg_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


_on_windows = sys.platform == "win32"
if _on_windows:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
    )
    _user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        HOOKPROC,
        ctypes.wintypes.HINSTANCE,
        ctypes.wintypes.DWORD,
    ]
    _user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK
    _user32.CallNextHookEx.argtypes = [
        ctypes.wintypes.HHOOK,
        ctypes.c_int,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    ]
    _user32.CallNextHookEx.restype = ctypes.c_long
    _user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]
    _user32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL
    _kernel32.GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = ctypes.wintypes.HMODULE
else:
    _user32 = None
    _kernel32 = None
    HOOKPROC = None


class GlobalMouseMonitor(QtCore.QObject):
    """全局鼠标监控器，用于检测 Ctrl+中键/左键。"""

    triggered = QtCore.Signal()

    VK_LCONTROL = 0xA2
    VK_RCONTROL = 0xA3

    def __init__(self, trigger_button: str = "middle", parent=None) -> None:
        super().__init__(parent)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._poll)
        self._ctrl_was_down = False
        self._poll_count = 0
        self._hook_handle = None
        self._hook_proc_ref = None
        self._last_trigger_time = 0.0
        self._last_ctrl_log_time = 0.0
        self._last_heartbeat_time = 0.0
        self._trigger_button = "middle"
        self._btn_down_msg = BUTTON_MSGS["middle"]["down"]
        self._btn_name = BUTTON_MSGS["middle"]["name"]
        self._btn_vk = BUTTON_MSGS["middle"]["vk"]
        self.set_trigger_button(trigger_button)

    def start(self) -> bool:
        """启动鼠标钩子 + Ctrl 轮询。"""
        if not _on_windows:
            _safe_log("全局鼠标快捷键仅支持 Windows", logging.WARNING)
            return False

        self._hook_proc_ref = HOOKPROC(self._mouse_ll_proc)
        h_mod = _kernel32.GetModuleHandleW(None)
        try:
            self._hook_handle = _user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._hook_proc_ref, h_mod, 0
            )
        except Exception as exc:
            self._hook_handle = None
            _safe_log(f"SetWindowsHookExW 异常: {exc}", logging.WARNING)

        if self._hook_handle:
            _safe_log(f"WH_MOUSE_LL 钩子已安装 (handle={self._hook_handle})", logging.INFO)
        else:
            err = ctypes.get_last_error() or 0
            _safe_log(f"WH_MOUSE_LL 钩子安装失败 (GetLastError={err})，仅用轮询", logging.WARNING)

        self._timer.start()
        _safe_log("Ctrl 轮询已启动 (QTimer 30ms)", logging.INFO)
        return True

    def stop(self) -> None:
        self._timer.stop()
        self._stop_hook()

    def _stop_hook(self) -> None:
        if self._hook_handle:
            try:
                _user32.UnhookWindowsHookEx(self._hook_handle)
            except Exception:
                pass
            self._hook_handle = None

    def _mouse_ll_proc(self, n_code, w_param, l_param):
        if n_code >= 0 and w_param == self._btn_down_msg:
            ctrl_down = bool(
                (_user32.GetAsyncKeyState(self.VK_LCONTROL) & 0x8000)
                or (_user32.GetAsyncKeyState(self.VK_RCONTROL) & 0x8000)
            )
            if ctrl_down:
                now = time.time()
                if now - self._last_trigger_time > 0.2:
                    self._last_trigger_time = now
                    _safe_log(f"Ctrl+{self._btn_name} 已触发", logging.INFO)
                    self.triggered.emit()
        return _user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param)

    def _poll(self) -> None:
        if not _on_windows:
            return
        self._poll_count += 1
        lctrl = bool(_user32.GetAsyncKeyState(self.VK_LCONTROL) & 0x8000)
        rctrl = bool(_user32.GetAsyncKeyState(self.VK_RCONTROL) & 0x8000)
        ctrl_down = lctrl or rctrl
        now = time.time()

        if ctrl_down != self._ctrl_was_down and now - self._last_ctrl_log_time > 2.0:
            self._last_ctrl_log_time = now
            side = "L" if lctrl else ("R" if rctrl else "None")
            state = "PRESSED" if ctrl_down else "RELEASED"
            _safe_log(f"Ctrl {state} (side={side})")

        if ctrl_down and not self._ctrl_was_down:
            btn_down = bool(_user32.GetAsyncKeyState(self._btn_vk) & 0x8000)
            if btn_down and now - self._last_trigger_time > 0.2:
                self._last_trigger_time = now
                _safe_log("Ctrl+鼠标 快捷键已触发（轮询备用）", logging.INFO)
                self.triggered.emit()

        if now - self._last_heartbeat_time > 60.0:
            self._last_heartbeat_time = now
            _safe_log(
                f"热键监听 heartbeat #{self._poll_count}, "
                f"ctrl={ctrl_down}, hook={'OK' if self._hook_handle else 'FAIL'}"
            )

        self._ctrl_was_down = ctrl_down

    def set_trigger_button(self, button: str) -> None:
        button = button if button in BUTTON_MSGS else "middle"
        self._trigger_button = button
        info = BUTTON_MSGS[button]
        self._btn_down_msg = info["down"]
        self._btn_name = info["name"]
        self._btn_vk = info["vk"]
        _safe_log(f"触发按钮已切换为: Ctrl+{self._btn_name}", logging.INFO)


class FolderFavoritesHotkeyService(QtCore.QObject):
    """管理 GlobalMouseMonitor，供 l_notepad 在任意程序中唤起「文件夹收藏」标签。"""

    started = QtCore.Signal(bool)
    failed = QtCore.Signal(str)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._monitor: GlobalMouseMonitor | None = None

    def is_available(self) -> bool:
        return _on_windows

    def start(self, on_triggered: Callable[[], None]) -> bool:
        if not _on_windows:
            self.failed.emit("Ctrl+鼠标 快捷键仅支持 Windows")
            self.started.emit(False)
            return False

        button = load_hotkey_button()
        self._monitor = GlobalMouseMonitor(trigger_button=button)
        self._monitor.triggered.connect(on_triggered)
        ok = bool(self._monitor.start())
        self.started.emit(ok)
        if not ok:
            self.failed.emit("全局鼠标钩子启动失败")
        return ok

    def stop(self) -> None:
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None

    def set_trigger_button(self, button: str) -> None:
        save_hotkey_button(button)
        if self._monitor is not None:
            self._monitor.set_trigger_button(button)

# -*- coding: utf-8 -*-
"""l_notepad 内嵌的文件夹收藏全局快捷键（Ctrl + 中键/左键）。"""

from __future__ import annotations

from typing import Callable

from PySide6 import QtCore

try:
    from l_folder_favorites.hotkey_listener import (
        GlobalMouseMonitor,
        load_hotkey_button,
        save_hotkey_button,
    )
except ImportError:  # pragma: no cover - rez 环境未加载 l_folder_favorites 时
    GlobalMouseMonitor = None  # type: ignore[misc, assignment]
    load_hotkey_button = None  # type: ignore[misc, assignment]
    save_hotkey_button = None  # type: ignore[misc, assignment]


class FolderFavoritesHotkeyService(QtCore.QObject):
    """管理 GlobalMouseMonitor，供 l_notepad 在任意程序中唤起「文件夹收藏」标签。"""

    started = QtCore.Signal(bool)
    failed = QtCore.Signal(str)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._monitor: GlobalMouseMonitor | None = None

    def is_available(self) -> bool:
        return GlobalMouseMonitor is not None

    def start(self, on_triggered: Callable[[], None]) -> bool:
        if GlobalMouseMonitor is None or load_hotkey_button is None:
            self.failed.emit("未找到 l_folder_favorites 包，无法启动 Ctrl+鼠标 快捷键")
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
        if save_hotkey_button is not None:
            save_hotkey_button(button)
        if self._monitor is not None:
            self._monitor.set_trigger_button(button)

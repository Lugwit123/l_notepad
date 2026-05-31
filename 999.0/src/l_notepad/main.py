# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .api_client import NotepadApi
from .web_ui import WebNotepadWindow


def _set_windows_appid(appid: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)
    except Exception:
        pass


def _acquire_single_instance_lock(app: QtWidgets.QApplication) -> QtCore.QLockFile | None:
    """带 API 模式单实例：使用 QLockFile 避免多开。"""
    data_dir_str = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.AppDataLocation
    )
    base_dir = Path(data_dir_str or Path.home())
    lock_path = base_dir / "l_notepad_with_api.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock = QtCore.QLockFile(str(lock_path))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QtWidgets.QMessageBox.warning(
            None,
            "L Notepad 已在运行",
            "L Notepad（带 API 模式）已经有一个实例在运行，禁止多开。",
        )
        return None
    return lock


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _is_port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
        return True
    except OSError:
        return False


def _wait_backend(base_url: str, timeout_s: float = 8.0) -> bool:
    api = NotepadApi(base_url)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if api.health():
                return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


def _start_backend_subprocess(host: str, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["L_NOTEPAD_HOST"] = host
    env["L_NOTEPAD_PORT"] = str(port)
    cmd = [sys.executable, "-m", "l_notepad.backend_server", "--host", host, "--port", str(port)]
    return subprocess.Popen(cmd, env=env)


def main() -> int:
    _set_windows_appid("Lugwit.l_notepad.with_api")
    host = os.environ.get("L_NOTEPAD_HOST", "127.0.0.1")
    port_env = int(os.environ.get("L_NOTEPAD_PORT", "8765"))
    port = _find_free_port() if port_env == 0 else port_env
    if port_env != 0 and not _is_port_available(host, port):
        print(f"[l_notepad] ERROR: 端口 {port} 被占用，请先关闭占用者（或设置 L_NOTEPAD_PORT=0 使用随机端口）。", file=sys.stderr)
        return 3
    base_url = f"http://{host}:{port}"
    web_url = f"{base_url}/web"

    backend = _start_backend_subprocess(host, port)
    if not _wait_backend(base_url):
        with suppress(Exception):
            backend.terminate()
        return 2

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    lock = _acquire_single_instance_lock(app)
    if lock is None:
        with suppress(Exception):
            backend.terminate()
        return 1
    icon_path = Path(__file__).resolve().parent / "static" / "favicon.svg"
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    # Desktop app loads the web frontend directly.
    _ = NotepadApi(base_url)  # keep for potential future health/extension
    win = WebNotepadWindow(web_url)
    win.show()
    code = app.exec()

    with suppress(Exception):
        backend.terminate()
    with suppress(Exception):
        backend.wait(timeout=2)
    if lock is not None:
        lock.unlock()
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())


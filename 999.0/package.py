# -*- coding: utf-8 -*-

name = "l_notepad"
version = "999.0"
description = "Simple notepad: local PC mode + optional FastAPI mode"
authors = ["Lugwit Team"]

# NOTE:
# - sqlite3 is in stdlib
requires = [
    "python-3.12.10",
    "fastapi",
    "uvicorn",
    "jinja2",
    "pydantic",
    "pyside6",
    "l_qt_wgt_lib",
]

build_command = False
cachable = True

relocatable = True


def commands():
    env.PYTHONPATH.prepend("{root}/src")
    env.L_NOTEPAD_ROOT = "{root}"
    
    # 设置 UTF-8 编码避免控制台乱码
    env.PYTHONIOENCODING = 'utf-8'

    # Pure PC mode: local file-based notes, no backend process.
    # 使用 cmd /c 包装以设置 UTF-8 代码页
    alias("l_notepad", "python -m l_notepad.local_main")
    # 原始模式：不使用自定义无边框标题栏，使用系统原生标题栏
    alias("l_notepad_ori", "python -m l_notepad.local_main_ori")
    # Keep original behavior: launch UI with embedded backend service.
    alias("l_notepad_with_api", "python -m l_notepad.main")
    alias("l_notepad_api", "python -m l_notepad.backend_server")


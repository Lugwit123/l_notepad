# -*- coding: utf-8 -*-

name = "l_notepad"
version = "999.0"
description = "Simple notepad: local PC mode + optional FastAPI mode"
authors = ["Lugwit Team"]

# NOTE:
# - sqlite3 is in stdlib
requires = [
    "python-3.12+<3.13",
    "fastapi",
    "uvicorn",
    "jinja2",
    "pydantic",
    "pyside6",
]

build_command = False
cachable = True
relocatable = True


def commands():
    env.PYTHONPATH.prepend("{root}/src")
    env.L_NOTEPAD_ROOT = "{root}"

    # Pure PC mode: local file-based notes, no backend process.
    alias("l_notepad", "python -m l_notepad.local_main")
    # Keep original behavior: launch UI with embedded backend service.
    alias("l_notepad_with_api", "python -m l_notepad.main")
    alias("l_notepad_api", "python -m l_notepad.backend_server")


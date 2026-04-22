# -*- coding: utf-8 -*-

name = "l_notepad"
version = "999.0"
description = "Simple notepad: FastAPI+SQLite+Jinja2 backend, PySide6 frontend"
authors = ["Lugwit Team"]

# NOTE:
# - sqlite3 is in stdlib
# - fastapi/jinja2/uvicorn/pyside6 are expected to be available in the python runtime
requires = ["python-3.12+<3.13"]

build_command = False
cachable = True
relocatable = True


def commands():
    env.PYTHONPATH.prepend("{root}/src")
    env.L_NOTEPAD_ROOT = "{root}"

    alias("l_notepad", "python {root}/src/l_notepad/main.py")
    alias("l_notepad_api", "python {root}/src/l_notepad/backend_server.py")


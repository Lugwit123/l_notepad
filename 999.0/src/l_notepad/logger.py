from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SafeFileHandler(logging.FileHandler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except (OSError, ValueError):
            pass

    def flush(self) -> None:
        try:
            super().flush()
        except (OSError, ValueError):
            pass


@dataclass
class LogContext:
    log_path: Path
    logger_name: str = "l_notepad"


_context: LogContext | None = None


def setup(log_path: str | Path, *, level: int = logging.INFO) -> logging.Logger:
    global _context
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _context = LogContext(log_path=path)

    logger = logging.getLogger(_context.logger_name)
    logger.setLevel(level)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = SafeFileHandler(path, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger


def get_logger() -> logging.Logger:
    if _context is None:
        return logging.getLogger("l_notepad")
    return logging.getLogger(_context.logger_name)


def log(*args: Any, level: str = "INFO", sep: str = " ", end: str = "") -> None:
    message = sep.join(str(a) for a in args)
    lvl = str(level or "INFO").upper()
    logger = get_logger()
    if lvl == "DEBUG":
        logger.debug(message)
    elif lvl in {"WARN", "WARNING"}:
        logger.warning(message)
    elif lvl == "ERROR":
        logger.error(message)
    else:
        logger.info(message)
    builtins.print(message, end=end)

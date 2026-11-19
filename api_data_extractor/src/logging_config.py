"""Logging setup with secret redaction.

Every registered secret is replaced by ``***`` in all log records, whatever
handler they go to (console or ``extraction_log.txt``).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional, Set

from . import APP_NAME

_SECRETS: Set[str] = set()
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"


def register_secret(secret: str) -> None:
    if secret and len(secret) >= 4:
        _SECRETS.add(secret)


def redact(text: str) -> str:
    for secret in _SECRETS:
        text = text.replace(secret, "***")
    return text


class SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _SECRETS:
            record.msg = redact(record.getMessage())
            record.args = ()
        return True


def get_logger() -> logging.Logger:
    logger = logging.getLogger(APP_NAME)
    if not any(isinstance(f, SecretRedactingFilter) for f in logger.filters):
        logger.addFilter(SecretRedactingFilter())
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def setup_console(level: int = logging.INFO) -> logging.Logger:
    logger = get_logger()
    for h in list(logger.handlers):
        if getattr(h, "_ade_console", False):
            logger.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, "%H:%M:%S"))
    handler._ade_console = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return logger


def add_file_handler(path: Path) -> logging.Handler:
    logger = get_logger()
    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return handler


def remove_handler(handler: Optional[logging.Handler]) -> None:
    if handler is not None:
        get_logger().removeHandler(handler)
        handler.close()

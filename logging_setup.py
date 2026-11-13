"""Application-wide logging configuration.

The Windows desktop build is launched through ``pythonw.exe`` (see
``setup.ps1``), which has no console attached. Every ``logger.exception(...)``
call in the GUI therefore went nowhere: a failed export or a failed updater
health check left no trace to diagnose. :func:`configure_logging` attaches a
rotating file handler under a per-user log directory and, when a console is
actually available, a stream handler as well.

The function is deliberately defensive: it must never raise, because it runs
before the Qt application object exists and a crash there would prevent the
app from starting at all.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_DIR_NAME = "CarrierACSelector"
LOG_FILE_NAME = "carrier-ac-selector.log"
MAX_LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

_HANDLER_MARKER = "_carrier_ac_selector_handler"


def default_log_dir() -> Path:
    """Return the per-user directory that should hold the application log."""

    override = os.environ.get("CARRIER_LOG_DIR")
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / APP_DIR_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_DIR_NAME

    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".local" / "state" / APP_DIR_NAME


def default_log_path() -> Path:
    """Return the full path of the application log file."""

    return default_log_dir() / LOG_FILE_NAME


def _already_configured() -> logging.Handler | None:
    for handler in logging.getLogger().handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            return handler
    return None


def configure_logging(
    level: int = logging.INFO,
    log_dir: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Configure root logging once and return the active log file path.

    Returns ``None`` when the log file cannot be created (read-only install
    directory, locked file, sandboxed filesystem). In that case a console
    handler is still installed when ``sys.stderr`` exists, so nothing is lost
    on a normal terminal run.
    """

    existing = _already_configured()
    if existing is not None:
        base_filename = getattr(existing, "baseFilename", None)
        return Path(base_filename) if base_filename else None

    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)

    log_path: Path | None = None
    try:
        directory = Path(log_dir) if log_dir is not None else default_log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        candidate = directory / LOG_FILE_NAME
        file_handler = RotatingFileHandler(
            candidate,
            maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        setattr(file_handler, _HANDLER_MARKER, True)
        root.addHandler(file_handler)
        log_path = candidate
    except (OSError, ValueError):
        # Never let logging setup stop the application from starting.
        log_path = None

    stream = getattr(sys, "stderr", None)
    if stream is not None:
        stream_handler = logging.StreamHandler(stream)
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(level)
        setattr(stream_handler, _HANDLER_MARKER, True)
        root.addHandler(stream_handler)

    if log_path is None and stream is None:
        # No destination at all: keep the logging module quiet about it.
        root.addHandler(logging.NullHandler())

    return log_path

# SPDX-License-Identifier: AGPL-3.0-or-later
"""Structured JSON-lines logging with an in-memory ring buffer.

The in-app Logs screen reads the ring buffer, while the rotating file keeps an
auditable record on disk. Debug mode logs DNS answers, redirect hops, timings,
matched rules and cache decisions as described in FR-9.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import zipfile
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from blockprobe import __version__

_RING_BUFFER: deque[dict[str, Any]] = deque(maxlen=2000)
_RING_HANDLER: RingBufferHandler | None = None  # type: ignore[name-defined]
_FILE_HANDLER: logging.Handler | None = None
_LOGGER_NAME = "blockprobe"


class JsonFormatter(logging.Formatter):
    """Format records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("run_id", "portal_id", "page", "domain", "event", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class RingBufferHandler(logging.Handler):
    """Keep the most recent records in memory for the Flet Logs screen."""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self.buffer: deque[dict[str, Any]] = deque(maxlen=capacity)
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            raw = self.format(record)
            entry = json.loads(raw)
            entry["id"] = record.created
            self.buffer.append(entry)
        except Exception:  # pragma: no cover - logging must never break the app
            self.handleError(record)

    def snapshot(
        self,
        level: str | None = None,
        search: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        minimum = levels.get((level or "DEBUG").upper(), logging.DEBUG)
        level_numbers = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        needle = (search or "").strip().lower()
        items: list[dict[str, Any]] = []
        for entry in reversed(self.buffer):
            if level_numbers.get(str(entry.get("level")), logging.DEBUG) < minimum:
                continue
            if needle and needle not in json.dumps(entry, ensure_ascii=False).lower():
                continue
            items.append(entry)
            if len(items) >= limit:
                break
        return list(reversed(items))

    def clear(self) -> None:
        self.buffer.clear()


def configure_logging(
    log_dir: Path,
    debug: bool = False,
    run_id: str | None = None,
    console: bool = False,
) -> logging.Logger:
    """Configure file and ring-buffer logging once per process."""
    global _RING_HANDLER, _FILE_HANDLER
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False

    if _RING_HANDLER is None:
        _RING_HANDLER = RingBufferHandler()
        logger.addHandler(_RING_HANDLER)

    log_file = log_dir / "blockprobe.jsonl"
    if _FILE_HANDLER is None:
        handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(JsonFormatter())
        _FILE_HANDLER = handler
        logger.addHandler(handler)
    else:
        _FILE_HANDLER.setLevel(logging.DEBUG if debug else logging.INFO)

    if console and not any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, RingBufferHandler)
        for handler in logger.handlers
    ):
        stream = logging.StreamHandler()
        stream.setFormatter(JsonFormatter())
        logger.addHandler(stream)

    logger.info(
        "logging configured",
        extra={"event": "logging_configured", "run_id": run_id, "version": __version__},
    )
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def ring_handler() -> RingBufferHandler | None:
    return _RING_HANDLER


def get_logs(
    level: str | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    if _RING_HANDLER is None:
        return []
    return _RING_HANDLER.snapshot(level=level, search=search, limit=limit)


def clear_logs() -> None:
    if _RING_HANDLER is not None:
        _RING_HANDLER.clear()


def export_logs_zip(destination: Path, search: str | None = None, level: str | None = None) -> Path:
    """Export the current ring buffer plus the JSONL log file as a zip archive."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    entries = get_logs(level=level, search=search, limit=2000)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "logs/ring-buffer.jsonl",
            "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries),
        )
        log_file = Path(_FILE_HANDLER.baseFilename) if isinstance(
            _FILE_HANDLER, logging.handlers.RotatingFileHandler
        ) else None
        if log_file and log_file.exists():
            archive.write(log_file, arcname="logs/blockprobe.jsonl")
    return destination

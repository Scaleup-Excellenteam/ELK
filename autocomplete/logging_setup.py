"""Structured event logging for the web interface.

Every event is written as one JSON line to disk (so it can later be shipped
to a real log pipeline) and mirrored into a small in-memory ring buffer that
the admin dashboard reads for a live view of recent activity.
"""

import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

LOG_DIRECTORY = Path(__file__).with_name("logs")
LOG_FILE = LOG_DIRECTORY / "app.log"
_MAX_RECENT_ENTRIES = 500

_recent_entries: deque[dict[str, Any]] = deque(maxlen=_MAX_RECENT_ENTRIES)
_entries_lock = Lock()

_logger = logging.getLogger("autocomplete")
_logger.setLevel(logging.INFO)


def _ensure_file_handler() -> None:
    if any(isinstance(handler, logging.FileHandler) for handler in _logger.handlers):
        return
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.propagate = False


def log_event(event: str, **fields: Any) -> dict[str, Any]:
    """Record one structured event to the log file and in-memory buffer."""

    _ensure_file_handler()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        **fields,
    }
    _logger.info(json.dumps(entry, ensure_ascii=False))
    with _entries_lock:
        _recent_entries.append(entry)
    return entry


def get_recent_entries(limit: int = 50, event: str | None = None) -> list[dict[str, Any]]:
    """Return the most recently logged events, newest first."""

    with _entries_lock:
        entries = list(_recent_entries)
    if event is not None:
        entries = [entry for entry in entries if entry.get("event") == event]
    entries.reverse()
    return entries[:limit]


def get_log_file_size() -> int:
    """Return the current size in bytes of the on-disk log file, or 0."""

    return LOG_FILE.stat().st_size if LOG_FILE.is_file() else 0

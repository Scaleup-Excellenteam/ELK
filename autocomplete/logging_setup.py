"""Structured event logging for the web interface.

Every event is written as one JSON line to disk (so it can later be shipped
to a real log pipeline) and mirrored into a small in-memory ring buffer that
the admin dashboard reads for a live view of recent activity.
"""

import json
import logging
import math
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


def _load_recent_entries_from_disk() -> None:
    """Restore the bounded live dashboard buffer after a server restart."""

    if not LOG_FILE.is_file():
        return

    # Read only a bounded tail. A production log may be large, so startup must
    # not scan the entire file merely to restore the admin dashboard.
    with LOG_FILE.open("rb") as log_handle:
        log_handle.seek(0, 2)
        position = log_handle.tell()
        chunks: list[bytes] = []
        newline_count = 0
        while position > 0 and newline_count <= _MAX_RECENT_ENTRIES:
            chunk_size = min(8192, position)
            position -= chunk_size
            log_handle.seek(position)
            chunk = log_handle.read(chunk_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")

    raw_tail = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    for line in raw_tail.splitlines()[-_MAX_RECENT_ENTRIES:]:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(entry, dict):
            _recent_entries.append(entry)


_load_recent_entries_from_disk()


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


def get_activity_summary(search_limit: int = 100) -> dict[str, Any]:
    """Aggregate recent activity without exposing raw query text."""

    entries = get_recent_entries(limit=_MAX_RECENT_ENTRIES)
    searches = [entry for entry in entries if entry.get("event") == "completion"]
    searches = searches[:search_limit]
    selections = [
        entry for entry in entries if entry.get("event") == "completion_selected"
    ]

    latencies = [
        float(entry["elapsed_ms"])
        for entry in searches
        if isinstance(entry.get("elapsed_ms"), (int, float))
    ]
    ordered_latencies = sorted(latencies)
    percentile_index = max(0, math.ceil(len(ordered_latencies) * 0.95) - 1)
    p95_latency_ms = (
        ordered_latencies[percentile_index] if ordered_latencies else 0.0
    )
    cache_hits = sum(entry.get("cache_hit") is True for entry in searches)
    characters_saved = sum(
        int(entry.get("characters_saved", 0))
        for entry in selections
        if isinstance(entry.get("characters_saved"), int)
    )
    error_count = sum(
        entry.get("event") in {"completion_error", "completion_rejected"}
        for entry in entries
    )

    return {
        "search_count": len(searches),
        "average_latency_ms": round(sum(latencies) / len(latencies), 2)
        if latencies
        else 0.0,
        "p95_latency_ms": round(p95_latency_ms, 2),
        "cache_hits": cache_hits,
        "cache_hit_rate": round(cache_hits / len(searches) * 100, 1)
        if searches
        else 0.0,
        "selected_completions": len(selections),
        "characters_saved": characters_saved,
        "slow_searches": sum(latency >= 500 for latency in latencies),
        "error_count": error_count,
        "latency_samples": list(reversed(latencies[:30])),
    }


def get_log_file_size() -> int:
    """Return the current size in bytes of the on-disk log file, or 0."""

    return LOG_FILE.stat().st_size if LOG_FILE.is_file() else 0

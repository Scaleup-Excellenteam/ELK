"""Filesystem hand-off between offline index builds and online serving.

Offline builds never overwrite the index a running service might be
reading from mid-request. Each build lands in its own versioned snapshot
file under a ``<index>.snapshots`` directory, and only once that build
validates does a small atomic pointer file (``<index>.current``) get
updated to name it, via write-then-rename so a reader never observes a
half-written pointer.

The online service resolves that pointer fresh on every request instead
of loading an index once at startup: this codebase already opens a new
SQLite connection per query and re-stats the index file on every request
for cache-key purposes, so pointer resolution slots into that same
already-stateless, already-per-request pattern. A new snapshot is picked
up on the very next request with no restart, while requests already in
flight keep reading whichever snapshot they resolved -- the old snapshot
file is never modified or deleted out from under them.
"""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .index import build_index, get_index_stats


class SnapshotBuildError(RuntimeError):
    """A new snapshot failed to validate; the pointer is left untouched."""


def snapshots_directory(base_index_path: str | Path) -> Path:
    """Return the directory holding versioned snapshots for ``base_index_path``."""

    base = Path(base_index_path)
    return base.with_name(base.name + ".snapshots")


def pointer_path(base_index_path: str | Path) -> Path:
    """Return the atomic pointer file naming the current snapshot."""

    base = Path(base_index_path)
    return base.with_name(base.name + ".current")


def new_snapshot_path(
    base_index_path: str | Path,
    *,
    now: datetime | None = None,
) -> Path:
    """Return a fresh, not-yet-existing versioned snapshot path."""

    directory = snapshots_directory(base_index_path)
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S%f")

    candidate = directory / f"{timestamp}.sqlite3"
    disambiguator = 1
    while candidate.exists():
        candidate = directory / f"{timestamp}-{disambiguator}.sqlite3"
        disambiguator += 1

    return candidate


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` so a reader never sees a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        os.unlink(temporary_name)
        raise


def publish_snapshot(base_index_path: str | Path, snapshot_path: str | Path) -> None:
    """Atomically point ``base_index_path`` at an already-built snapshot."""

    _atomic_write(pointer_path(base_index_path), Path(snapshot_path).name)


def read_pointer(base_index_path: str | Path) -> Path | None:
    """Return the snapshot path currently named by the pointer, if any."""

    try:
        snapshot_name = pointer_path(base_index_path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None

    if not snapshot_name:
        return None

    return snapshots_directory(base_index_path) / snapshot_name


def resolve_active_index_path(base_index_path: str | Path) -> str | Path:
    """Return the index path that should serve traffic right now.

    When the pointer names a snapshot that exists on disk, that snapshot
    is returned. Otherwise ``base_index_path`` is returned completely
    unchanged (same object, same type), which is what keeps every
    existing, non-snapshot ``build``/``--index`` workflow behaving exactly
    as it did before ZDT existed.
    """

    snapshot_path = read_pointer(base_index_path)
    if snapshot_path is not None and snapshot_path.is_file():
        return snapshot_path
    return base_index_path


def build_snapshot(
    source_root: str | Path,
    base_index_path: str | Path,
    batch_size: int = 5_000,
) -> tuple[Path, int]:
    """Build a new versioned snapshot and publish it once it validates.

    Returns the new snapshot's path and the number of sentences stored.
    Raises ``SnapshotBuildError`` -- without touching the pointer -- if the
    freshly built snapshot cannot be read back, so a bad build never takes
    traffic away from the last good snapshot.
    """

    snapshot_path = new_snapshot_path(base_index_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    stored_sentences = build_index(source_root, snapshot_path, batch_size=batch_size)

    try:
        get_index_stats(snapshot_path)
    except Exception as error:
        raise SnapshotBuildError(
            f"New snapshot at {snapshot_path} failed validation: {error}"
        ) from error

    publish_snapshot(base_index_path, snapshot_path)
    return snapshot_path, stored_sentences

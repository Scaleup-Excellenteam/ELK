"""Tests for the offline-to-online filesystem hand-off (ZDT snapshots).

Offline builds never overwrite the index a running service is reading.
Each build lands in its own versioned snapshot file, and an atomic pointer
file names which snapshot is current. These tests cover the pointer
mechanics directly, independent of the CLI and web layers that consume
them (see test_cli_snapshot.py and test_web_snapshot.py).
"""

import unittest
from unittest.mock import patch

from autocomplete.snapshot import (
    SnapshotBuildError,
    build_snapshot,
    new_snapshot_path,
    pointer_path,
    publish_snapshot,
    read_pointer,
    resolve_active_index_path,
    snapshots_directory,
)
from tests.support import TemporaryCorpusTestCase


class PathHelperTests(TemporaryCorpusTestCase):
    def test_snapshots_directory_sits_beside_the_base_index_with_a_suffix(self) -> None:
        base = self.temporary_path / "autocomplete.sqlite3"

        self.assertEqual(
            snapshots_directory(base),
            self.temporary_path / "autocomplete.sqlite3.snapshots",
        )

    def test_pointer_path_sits_beside_the_base_index_with_a_suffix(self) -> None:
        base = self.temporary_path / "autocomplete.sqlite3"

        self.assertEqual(
            pointer_path(base),
            self.temporary_path / "autocomplete.sqlite3.current",
        )

    def test_new_snapshot_path_is_placed_under_the_snapshots_directory(self) -> None:
        base = self.temporary_path / "autocomplete.sqlite3"

        candidate = new_snapshot_path(base)

        self.assertEqual(candidate.parent, snapshots_directory(base))
        self.assertEqual(candidate.suffix, ".sqlite3")
        self.assertFalse(candidate.exists())

    def test_new_snapshot_path_avoids_a_candidate_that_already_exists(self) -> None:
        base = self.temporary_path / "autocomplete.sqlite3"
        directory = snapshots_directory(base)
        directory.mkdir(parents=True)

        import datetime

        fixed_now = datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        taken_path = new_snapshot_path(base, now=fixed_now)
        taken_path.write_text("occupied", encoding="utf-8")

        candidate = new_snapshot_path(base, now=fixed_now)

        self.assertNotEqual(candidate, taken_path)
        self.assertFalse(candidate.exists())


class PointerReadWriteTests(TemporaryCorpusTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.base = self.temporary_path / "autocomplete.sqlite3"

    def test_read_pointer_returns_none_without_a_pointer_file(self) -> None:
        self.assertIsNone(read_pointer(self.base))

    def test_publish_snapshot_writes_a_pointer_readable_back_to_the_same_path(self) -> None:
        directory = snapshots_directory(self.base)
        directory.mkdir(parents=True)
        snapshot = directory / "20260101T000000000000.sqlite3"
        snapshot.write_text("fake index", encoding="utf-8")

        publish_snapshot(self.base, snapshot)

        self.assertEqual(read_pointer(self.base), snapshot)

    def test_publish_snapshot_replaces_a_previous_pointer_atomically(self) -> None:
        directory = snapshots_directory(self.base)
        directory.mkdir(parents=True)
        first = directory / "first.sqlite3"
        second = directory / "second.sqlite3"
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")

        publish_snapshot(self.base, first)
        publish_snapshot(self.base, second)

        self.assertEqual(read_pointer(self.base), second)
        # The pointer flip must never leave a stray temporary file behind.
        leftover_temp_files = [
            entry for entry in self.base.parent.iterdir() if entry.name.endswith(".tmp")
        ]
        self.assertEqual(leftover_temp_files, [])

    def test_read_pointer_ignores_surrounding_whitespace(self) -> None:
        directory = snapshots_directory(self.base)
        directory.mkdir(parents=True)
        snapshot = directory / "padded.sqlite3"
        snapshot.write_text("fake index", encoding="utf-8")
        pointer_path(self.base).write_text(f"  {snapshot.name}\n", encoding="utf-8")

        self.assertEqual(read_pointer(self.base), snapshot)


class ResolveActiveIndexPathTests(TemporaryCorpusTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.base = self.temporary_path / "autocomplete.sqlite3"

    def test_falls_back_to_the_base_path_without_a_pointer(self) -> None:
        self.assertEqual(resolve_active_index_path(self.base), self.base)

    def test_preserves_the_original_argument_type_and_value_on_fallback(self) -> None:
        # A pointer never exists for "chosen.sqlite3" in the process's cwd,
        # so callers that pass plain strings (e.g. the interactive CLI
        # session) must get that exact string back, unchanged.
        result = resolve_active_index_path("chosen.sqlite3")

        self.assertEqual(result, "chosen.sqlite3")
        self.assertIsInstance(result, str)

    def test_resolves_to_the_published_snapshot(self) -> None:
        directory = snapshots_directory(self.base)
        directory.mkdir(parents=True)
        snapshot = directory / "live.sqlite3"
        snapshot.write_text("fake index", encoding="utf-8")
        publish_snapshot(self.base, snapshot)

        self.assertEqual(resolve_active_index_path(self.base), snapshot)

    def test_falls_back_to_the_base_path_when_the_pointer_targets_a_missing_file(self) -> None:
        directory = snapshots_directory(self.base)
        directory.mkdir(parents=True)
        snapshot = directory / "gone.sqlite3"
        snapshot.write_text("fake index", encoding="utf-8")
        publish_snapshot(self.base, snapshot)
        snapshot.unlink()

        self.assertEqual(resolve_active_index_path(self.base), self.base)


class BuildSnapshotTests(TemporaryCorpusTestCase):
    def test_builds_into_a_new_versioned_file_and_publishes_the_pointer(self) -> None:
        corpus_path = self.write_corpus({"a.txt": "One.\nTwo.\n"})
        base_index_path = self.temporary_path / "autocomplete.sqlite3"

        snapshot_path, stored_sentences = build_snapshot(corpus_path, base_index_path)

        self.assertEqual(stored_sentences, 2)
        self.assertTrue(snapshot_path.is_file())
        self.assertEqual(snapshot_path.parent, snapshots_directory(base_index_path))
        self.assertEqual(read_pointer(base_index_path), snapshot_path)
        # The base path itself is never written to directly.
        self.assertFalse(base_index_path.exists())

    def test_never_overwrites_a_previous_snapshot(self) -> None:
        base_index_path = self.temporary_path / "autocomplete.sqlite3"
        first_corpus = self.write_corpus({"a.txt": "One.\n"})
        first_snapshot, _count = build_snapshot(first_corpus, base_index_path)

        second_corpus_dir = self.temporary_path / "corpus2"
        second_corpus_dir.mkdir()
        (second_corpus_dir / "b.txt").write_text("Two.\nThree.\n", encoding="utf-8")
        second_snapshot, second_count = build_snapshot(second_corpus_dir, base_index_path)

        self.assertNotEqual(first_snapshot, second_snapshot)
        self.assertTrue(first_snapshot.is_file(), "the previous snapshot must survive")
        self.assertTrue(second_snapshot.is_file())
        self.assertEqual(second_count, 2)
        self.assertEqual(read_pointer(base_index_path), second_snapshot)

    def test_leaves_the_previous_pointer_untouched_when_a_new_build_fails_validation(
        self,
    ) -> None:
        base_index_path = self.temporary_path / "autocomplete.sqlite3"
        good_corpus = self.write_corpus({"a.txt": "One.\n"})
        good_snapshot, _count = build_snapshot(good_corpus, base_index_path)

        with patch(
            "autocomplete.snapshot.get_index_stats",
            side_effect=RuntimeError("simulated corruption"),
        ):
            with self.assertRaises(SnapshotBuildError):
                build_snapshot(good_corpus, base_index_path)

        self.assertEqual(read_pointer(base_index_path), good_snapshot)


if __name__ == "__main__":
    unittest.main()

"""CLI surface of the ZDT snapshot hand-off: the ``build-snapshot`` command."""

import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from autocomplete.cli import _build_snapshot_command, main, run_interactive
from autocomplete.snapshot import build_snapshot, read_pointer, snapshots_directory
from tests.support import TemporaryCorpusTestCase


class BuildSnapshotCommandTests(TemporaryCorpusTestCase):
    def test_builds_a_snapshot_and_publishes_the_pointer(self) -> None:
        corpus_path = self.write_corpus({"a.txt": "One.\nTwo.\n"})
        base_index_path = self.temporary_path / "autocomplete.sqlite3"
        printed = io.StringIO()

        with redirect_stdout(printed):
            _build_snapshot_command(str(corpus_path), str(base_index_path))

        output = printed.getvalue()
        self.assertIn("Snapshot ready: 2 sentences stored in", output)
        published = read_pointer(base_index_path)
        self.assertIsNotNone(published)
        self.assertTrue(published.is_file())
        self.assertEqual(published.parent, snapshots_directory(base_index_path))
        # The base path is a pointer target, not a file written in place.
        self.assertFalse(base_index_path.exists())


class CommandDispatchTests(TemporaryCorpusTestCase):
    def test_build_snapshot_uses_the_given_corpus_and_index_paths(self) -> None:
        with patch(
            "autocomplete.cli.build_snapshot",
            return_value=(self.temporary_path / "snap.sqlite3", 7),
        ) as build:
            with redirect_stdout(io.StringIO()):
                with patch.object(
                    sys, "argv", ["autocomplete", "build-snapshot", "Archive"]
                ):
                    main()

        self.assertEqual(build.call_args.args, ("Archive", "autocomplete.sqlite3"))

    def test_build_snapshot_accepts_a_custom_index_path(self) -> None:
        with patch(
            "autocomplete.cli.build_snapshot",
            return_value=(self.temporary_path / "snap.sqlite3", 1),
        ) as build:
            with redirect_stdout(io.StringIO()):
                with patch.object(
                    sys,
                    "argv",
                    [
                        "autocomplete",
                        "build-snapshot",
                        "Archive",
                        "--index",
                        "other.sqlite3",
                    ],
                ):
                    main()

        self.assertEqual(build.call_args.args, ("Archive", "other.sqlite3"))


class HotSwapInteractiveSessionTests(TemporaryCorpusTestCase):
    def test_a_pointer_flip_mid_session_is_served_without_restarting(self) -> None:
        base_index_path = self.temporary_path / "autocomplete.sqlite3"
        build_snapshot(
            self.write_corpus({"a.txt": "Old snapshot sentence.\n"}),
            base_index_path,
        )

        second_corpus = self.temporary_path / "corpus2"
        second_corpus.mkdir()
        (second_corpus / "b.txt").write_text("New snapshot sentence.\n", encoding="utf-8")

        fragments = iter(["old", "#", "new"])

        def scripted_input(_prompt: str) -> str:
            try:
                fragment = next(fragments)
            except StopIteration as error:
                raise EOFError from error
            if fragment == "#":
                # Simulate an offline build finishing and flipping the
                # pointer concurrently, in between two interactive turns.
                build_snapshot(second_corpus, base_index_path)
            return fragment

        output: list[str] = []
        run_interactive(base_index_path, input_fn=scripted_input, output_fn=output.append)

        joined = "\n".join(output)
        self.assertIn("Old snapshot sentence.", joined)
        self.assertIn("New snapshot sentence.", joined)


if __name__ == "__main__":
    unittest.main()

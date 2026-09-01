"""Argument parsing, command dispatch and the interactive session loop."""

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import autocomplete
from autocomplete.cli import _build_command, main, run_interactive
from tests.support import TemporaryCorpusTestCase


def _scripted_input(fragments: list[str]):
    """Return an input function that replays fragments then reports end of file."""

    remaining = iter(fragments)

    def read(_prompt: str) -> str:
        try:
            return next(remaining)
        except StopIteration as error:
            raise EOFError from error

    return read


class InteractiveSessionTests(TemporaryCorpusTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.index_path = self.build_index_from_lines(
            ["Python documentation is useful.", "Another sentence entirely."]
        )

    def _run(self, fragments: list[str]) -> tuple[list[str], list[str]]:
        """Replay typed fragments and return the prompts and printed lines."""

        prompts: list[str] = []
        output: list[str] = []
        read_fragment = _scripted_input(fragments)

        def recording_input(prompt: str) -> str:
            prompts.append(prompt)
            return read_fragment(prompt)

        run_interactive(
            self.index_path,
            input_fn=recording_input,
            output_fn=output.append,
        )
        return prompts, output

    def test_prompts_with_the_ready_message_before_any_input(self) -> None:
        prompts, _output = self._run(["python"])

        self.assertEqual(prompts[0], "The system is ready. Enter your text:\n")

    def test_prompts_with_the_text_typed_so_far(self) -> None:
        prompts, _output = self._run(["python", " documentation"])

        self.assertEqual(prompts[1], "python")
        self.assertEqual(prompts[2], "python documentation")

    def test_lists_numbered_suggestions_with_their_source_and_score(self) -> None:
        _prompts, output = self._run(["python"])

        self.assertEqual(output[0], "Here are 1 suggestions:")
        self.assertEqual(
            output[1],
            "1. Python documentation is useful. (sentences.txt:1, score=12)",
        )

    def test_reports_when_nothing_matches(self) -> None:
        _prompts, output = self._run(["zzzzzzzzzz"])

        self.assertEqual(output[0], "Here are 0 suggestions:")

    def test_a_hash_resets_the_typed_text_without_searching(self) -> None:
        prompts, output = self._run(["python", "#", "another"])

        self.assertEqual(prompts[2], "The system is ready. Enter your text:\n")
        self.assertTrue(
            any("Another sentence entirely." in message for message in output)
        )
        self.assertFalse(
            any("Python documentation" in message for message in output[2:])
        )

    def test_a_hash_inside_a_fragment_also_resets(self) -> None:
        prompts, _output = self._run(["python", "documentation#"])

        self.assertEqual(prompts[2], "The system is ready. Enter your text:\n")

    def test_says_goodbye_at_end_of_input(self) -> None:
        _prompts, output = self._run([])

        self.assertEqual(output[-1], "\nGoodbye.")

    def test_says_goodbye_on_a_keyboard_interrupt(self) -> None:
        output: list[str] = []

        def interrupting_input(_prompt: str) -> str:
            raise KeyboardInterrupt

        run_interactive(self.index_path, input_fn=interrupting_input, output_fn=output.append)

        self.assertEqual(output, ["\nGoodbye."])

    def test_passes_the_accumulated_text_and_index_to_the_engine(self) -> None:
        with patch(
            "autocomplete.cli.get_best_k_completions",
            return_value=[],
        ) as completions:
            run_interactive(
                "chosen.sqlite3",
                input_fn=_scripted_input(["to", "p"]),
                output_fn=lambda _message: None,
            )

        self.assertEqual(
            [call.args for call in completions.call_args_list],
            [("to", "chosen.sqlite3"), ("top", "chosen.sqlite3")],
        )


class BuildCommandTests(TemporaryCorpusTestCase):
    def test_reports_the_number_of_stored_sentences(self) -> None:
        corpus_path = self.write_corpus({"a.txt": "One.\nTwo.\n"})
        index_path = self.temporary_path / "built.sqlite3"
        printed = io.StringIO()

        with redirect_stdout(printed):
            _build_command(str(corpus_path), str(index_path))

        self.assertIn("Index ready: 2 sentences stored in", printed.getvalue())
        self.assertTrue(index_path.is_file())


class CommandDispatchTests(TemporaryCorpusTestCase):
    def test_build_uses_the_given_corpus_and_index_paths(self) -> None:
        with patch("autocomplete.cli.build_index", return_value=7) as build:
            with redirect_stdout(io.StringIO()):
                with patch.object(sys, "argv", ["autocomplete", "build", "Archive"]):
                    main()

        self.assertEqual(build.call_args.args, ("Archive", "autocomplete.sqlite3"))

    def test_build_accepts_a_custom_index_path(self) -> None:
        with patch("autocomplete.cli.build_index", return_value=1) as build:
            with redirect_stdout(io.StringIO()):
                with patch.object(
                    sys,
                    "argv",
                    ["autocomplete", "build", "Archive", "--index", "other.sqlite3"],
                ):
                    main()

        self.assertEqual(build.call_args.args, ("Archive", "other.sqlite3"))

    def test_serve_starts_the_interactive_session(self) -> None:
        with patch("autocomplete.cli.run_interactive") as interactive:
            with patch.object(sys, "argv", ["autocomplete", "serve"]):
                main()

        self.assertEqual(interactive.call_args.args, ("autocomplete.sqlite3",))

    def test_ui_serves_the_application_on_the_requested_address(self) -> None:
        created_apps = []

        def fake_create_app(index_path):
            created_apps.append(index_path)
            return "application"

        with patch("uvicorn.run") as uvicorn_run:
            with patch("autocomplete.web.create_app", fake_create_app):
                with patch.object(
                    sys,
                    "argv",
                    ["autocomplete", "ui", "--host", "0.0.0.0", "--port", "9001"],
                ):
                    main()

        self.assertEqual(created_apps, ["autocomplete.sqlite3"])
        self.assertEqual(uvicorn_run.call_args.args, ("application",))
        self.assertEqual(
            uvicorn_run.call_args.kwargs,
            {"host": "0.0.0.0", "port": 9001},
        )

    def test_requires_a_command(self) -> None:
        with patch.object(sys, "argv", ["autocomplete"]), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main()

        self.assertEqual(raised.exception.code, 2)

    def test_rejects_an_unknown_command(self) -> None:
        argv = ["autocomplete", "explode"]
        with patch.object(sys, "argv", argv), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main()

        self.assertEqual(raised.exception.code, 2)


class ModuleEntryPointTests(unittest.TestCase):
    def test_the_package_can_be_run_as_a_module(self) -> None:
        repository_root = Path(autocomplete.__file__).resolve().parent.parent
        completed = subprocess.run(
            [sys.executable, "-m", "autocomplete", "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=repository_root,
        )

        self.assertEqual(completed.returncode, 0)
        for command in ("build", "serve", "ui"):
            self.assertIn(command, completed.stdout)


if __name__ == "__main__":
    unittest.main()

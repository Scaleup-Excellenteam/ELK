import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from autocomplete.cli import run_interactive
from autocomplete.index import build_index
from autocomplete.models import AutoCompleteData


class InteractiveCliTests(unittest.TestCase):
    @staticmethod
    def _input_from(fragments):
        typed_fragments = iter(fragments)

        def fake_input(_prompt: str) -> str:
            try:
                return next(typed_fragments)
            except StopIteration as error:
                raise EOFError from error

        return fake_input

    def test_appends_new_characters_to_the_existing_text(self) -> None:
        typed_fragments = iter(["to", "p"])

        def fake_input(_prompt: str) -> str:
            try:
                return next(typed_fragments)
            except StopIteration as error:
                raise EOFError from error

        with patch(
            "autocomplete.cli.get_best_k_completions",
            return_value=[],
        ) as mocked_completion:
            run_interactive(
                "unused.sqlite3",
                input_fn=fake_input,
                output_fn=lambda _message: None,
            )

        self.assertEqual(
            mocked_completion.call_args_list,
            [
                call("to", "unused.sqlite3"),
                call("top", "unused.sqlite3"),
            ],
        )

    def test_continues_typing_and_resets_after_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "corpus"
            corpus_path.mkdir()
            (corpus_path / "sentences.txt").write_text(
                "This phrase is complete.\nAnother sentence.\n",
                encoding="utf-8",
            )
            index_path = temporary_path / "autocomplete.sqlite3"
            build_index(corpus_path, index_path)

            typed_fragments = iter(["this", " phrase", "#", "another"])
            prompts = []
            output = []

            def fake_input(prompt: str) -> str:
                prompts.append(prompt)
                try:
                    return next(typed_fragments)
                except StopIteration as error:
                    raise EOFError from error

            run_interactive(index_path, input_fn=fake_input, output_fn=output.append)

        self.assertEqual(prompts[1], "\nContinue [this] > ")
        self.assertIn("This phrase is complete.", "\n".join(output))
        self.assertEqual(prompts[3], "\nSearch > ")
        self.assertIn("\nQuery reset.", output)
        self.assertIn("Another sentence.", "\n".join(output))
        self.assertEqual(output[-1], "\nGoodbye.")

    def test_formats_results_without_changing_original_punctuation(self) -> None:
        result = AutoCompleteData(
            completed_sentence="!Important original sentence.",
            source_text="docs/example.txt",
            offset=42,
            score=18,
        )
        output = []

        with patch(
            "autocomplete.cli.get_best_k_completions",
            return_value=[result],
        ):
            run_interactive(
                "unused.sqlite3",
                input_fn=self._input_from(["important"]),
                output_fn=output.append,
            )

        self.assertIn("\nSuggestions (1)", output)
        self.assertIn("1) !Important original sentence.", output)
        self.assertIn("   Source: docs/example.txt | line 42 | score 18", output)

    def test_reuses_empty_cached_results_after_reset_and_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            index_path = Path(temporary_directory) / "autocomplete.sqlite3"
            index_path.write_bytes(b"test index")

            with patch(
                "autocomplete.cli.get_best_k_completions",
                return_value=[],
            ) as mocked_completion:
                run_interactive(
                    index_path,
                    input_fn=self._input_from(["Python", "#", "PYTHON!!!"]),
                    output_fn=lambda _message: None,
                )

        mocked_completion.assert_called_once_with("Python", index_path)

    def test_cli_cache_evicts_the_least_recently_used_query(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            index_path = Path(temporary_directory) / "autocomplete.sqlite3"
            index_path.write_bytes(b"test index")

            with patch(
                "autocomplete.cli.get_best_k_completions",
                return_value=[],
            ) as mocked_completion:
                run_interactive(
                    index_path,
                    input_fn=self._input_from(
                        ["a", "#", "b", "#", "a", "#", "c", "#", "b"]
                    ),
                    output_fn=lambda _message: None,
                    cache_capacity=2,
                )

        self.assertEqual(
            mocked_completion.call_args_list,
            [
                call("a", index_path),
                call("b", index_path),
                call("c", index_path),
                call("b", index_path),
            ],
        )

    def test_cli_cache_does_not_survive_an_index_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            index_path = Path(temporary_directory) / "autocomplete.sqlite3"
            index_path.write_bytes(b"first index")
            typed_fragments = iter(["python", "#", "python"])
            python_searches = 0

            def fake_input(_prompt: str) -> str:
                nonlocal python_searches
                try:
                    fragment = next(typed_fragments)
                except StopIteration as error:
                    raise EOFError from error
                if fragment == "python":
                    python_searches += 1
                    if python_searches == 2:
                        index_path.write_bytes(b"second larger index")
                return fragment

            with patch(
                "autocomplete.cli.get_best_k_completions",
                return_value=[],
            ) as mocked_completion:
                run_interactive(
                    index_path,
                    input_fn=fake_input,
                    output_fn=lambda _message: None,
                )

        self.assertEqual(mocked_completion.call_count, 2)


if __name__ == "__main__":
    unittest.main()

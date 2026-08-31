import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from autocomplete.cli import run_interactive
from autocomplete.index import build_index


class InteractiveCliTests(unittest.TestCase):
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

        self.assertEqual(prompts[1], "this")
        self.assertIn("This phrase is complete.", "\n".join(output))
        self.assertEqual(prompts[3], "The system is ready. Enter your text:\n")
        self.assertIn("Another sentence.", "\n".join(output))
        self.assertEqual(output[-1], "\nGoodbye.")


if __name__ == "__main__":
    unittest.main()

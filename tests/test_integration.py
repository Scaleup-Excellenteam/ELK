"""End-to-end checks across the offline build and both online interfaces."""

import asyncio
import io
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import httpx

import autocomplete
from autocomplete import (
    AutoCompleteData,
    best_match_score,
    get_best_k_completions,
    normalize_text,
)
from autocomplete.cli import main, run_interactive
from autocomplete.engine import get_best_unique_completions
from autocomplete.web import create_app
from tests.support import TemporaryCorpusTestCase


_CORPUS = {
    "docs/intro.txt": (
        "Python documentation is useful.\n"
        "\n"
        "The quick brown fox jumps over the lazy dog.\n"
    ),
    "docs/deeper/appendix.txt": (
        "Python documentation is useful.\n"
        "Reading the manual saves time.\n"
    ),
    "notes.txt": "Python documentation is useless.\nUnrelated note.\n",
    "ignored.md": "Python documentation is missing.\n",
}


class BuildThenSearchTests(TemporaryCorpusTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.corpus_path = self.write_corpus(_CORPUS)
        self.index_path = self.temporary_path / "autocomplete.sqlite3"

        with redirect_stdout(io.StringIO()):
            with patch.object(
                sys,
                "argv",
                [
                    "autocomplete",
                    "build",
                    str(self.corpus_path),
                    "--index",
                    str(self.index_path),
                ],
            ):
                main()

    def test_the_build_command_indexes_only_the_text_files(self) -> None:
        with sqlite3.connect(self.index_path) as connection:
            sentences = {
                sentence
                for (sentence,) in connection.execute(
                    "SELECT original_sentence FROM sentence_groups"
                )
            }

        self.assertIn("Python documentation is useful.", sentences)
        self.assertNotIn("Python documentation is missing.", sentences)

    def test_the_index_answers_the_query_the_corpus_supports(self) -> None:
        results = get_best_k_completions("python documentation is useful", self.index_path)

        self.assertEqual(
            [(result.source_text, result.offset) for result in results],
            [("docs/deeper/appendix.txt", 1), ("docs/intro.txt", 1)],
        )
        self.assertTrue(
            all(result.score == 2 * len("python documentation is useful") for result in results)
        )

    def test_every_returned_location_quotes_the_real_corpus_line(self) -> None:
        results = get_best_k_completions("documentation", self.index_path)

        self.assertTrue(results)
        for result in results:
            with self.subTest(result=result):
                lines = (self.corpus_path / result.source_text).read_text(
                    encoding="utf-8"
                ).splitlines()
                self.assertEqual(lines[result.offset - 1], result.completed_sentence)

    def test_the_engine_score_agrees_with_the_public_scoring_helper(self) -> None:
        for query in ["documentation", "documentatixn", "the quick brown", "lazy dg"]:
            with self.subTest(query=query):
                for result in get_best_k_completions(query, self.index_path):
                    self.assertEqual(
                        result.score,
                        best_match_score(query, result.completed_sentence),
                    )

    def test_the_interactive_session_reports_the_engine_results(self) -> None:
        fragments = iter(["python ", "documentation"])
        output: list[str] = []

        def reader(_prompt: str) -> str:
            try:
                return next(fragments)
            except StopIteration as error:
                raise EOFError from error

        run_interactive(self.index_path, input_fn=reader, output_fn=output.append)

        expected = get_best_k_completions("python documentation", self.index_path)
        self.assertEqual(output[-len(expected) - 2], f"Here are {len(expected)} suggestions:")
        for position, result in enumerate(expected, start=1):
            self.assertIn(
                f"{position}. {result.completed_sentence} "
                f"({result.source_text}:{result.offset}, score={result.score})",
                output,
            )

    def test_the_web_interface_groups_the_same_results(self) -> None:
        app = create_app(self.index_path)

        async def send_request():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                completions = await client.post(
                    "/api/completions",
                    json={"query": "python documentation"},
                )
                suggestion = completions.json()["suggestions"][0]
                locations = await client.get(
                    f"/api/completions/{suggestion['sentence_id']}/locations"
                )
                return suggestion, locations.json()

        suggestion, location_page = asyncio.run(send_request())

        self.assertEqual(suggestion["completed_sentence"], "Python documentation is useful.")
        self.assertEqual(suggestion["occurrence_count"], 2)
        self.assertEqual(
            location_page["locations"],
            [
                {"source_text": "docs/deeper/appendix.txt", "offset": 1},
                {"source_text": "docs/intro.txt", "offset": 1},
            ],
        )

    def test_the_grouped_search_keeps_the_ranked_search_top_sentence(self) -> None:
        for query in ["python documentation", "documentatixn", "manual"]:
            with self.subTest(query=query):
                ranked = get_best_k_completions(query, self.index_path)
                grouped = get_best_unique_completions(query, self.index_path)

                self.assertEqual(
                    grouped[0].completed_sentence,
                    ranked[0].completed_sentence,
                )
                self.assertEqual(grouped[0].score, ranked[0].score)


class PublicPackageApiTests(unittest.TestCase):
    def test_exports_the_documented_names(self) -> None:
        self.assertEqual(
            sorted(autocomplete.__all__),
            [
                "AutoCompleteData",
                "best_match_score",
                "get_best_k_completions",
                "normalize_text",
            ],
        )

        for name in autocomplete.__all__:
            with self.subTest(name=name):
                self.assertIs(getattr(autocomplete, name), globals()[name])

    def test_the_result_type_carries_the_required_fields(self) -> None:
        result = AutoCompleteData(
            completed_sentence="Python documentation is useful.",
            source_text="docs/intro.txt",
            offset=1,
            score=12,
        )

        self.assertEqual(
            (result.completed_sentence, result.source_text, result.offset, result.score),
            ("Python documentation is useful.", "docs/intro.txt", 1, 12),
        )
        self.assertEqual(normalize_text(result.completed_sentence), "python documentation is useful")


if __name__ == "__main__":
    unittest.main()

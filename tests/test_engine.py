import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autocomplete.engine import get_best_k_completions, get_best_unique_completions
from autocomplete.index import build_index


class GetBestCompletionsTests(unittest.TestCase):
    def _build_test_index(self, temporary_path: Path, lines: list[str]) -> Path:
        corpus_path = temporary_path / "corpus"
        corpus_path.mkdir()
        (corpus_path / "sentences.txt").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
        index_path = temporary_path / "autocomplete.sqlite3"
        build_index(corpus_path, index_path)
        return index_path

    def test_returns_scored_autocomplete_data_for_a_typo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            index_path = self._build_test_index(
                temporary_path,
                ["To be or not to be, that is the question."],
            )

            results = get_best_k_completions("or knot", index_path)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].completed_sentence, "To be or not to be, that is the question.")
        self.assertEqual(results[0].source_text, "sentences.txt")
        self.assertEqual(results[0].offset, 1)
        self.assertEqual(results[0].score, 8)

    def test_sorts_by_score_then_alphabetically_and_keeps_only_five(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            index_path = self._build_test_index(
                temporary_path,
                [
                    "Zulu has this phrase.",
                    "Alpha has this phrase.",
                    "Gamma has this phrase.",
                    "Beta has this phrase.",
                    "Omega has this phrase.",
                    "Delta has this phrase.",
                ],
            )

            results = get_best_k_completions("this phrase", index_path)

        self.assertEqual(
            [result.completed_sentence for result in results],
            [
                "Alpha has this phrase.",
                "Beta has this phrase.",
                "Delta has this phrase.",
                "Gamma has this phrase.",
                "Omega has this phrase.",
            ],
        )
        self.assertTrue(all(result.score == 22 for result in results))

    def test_five_exact_matches_skip_approximate_candidate_scanning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            index_path = self._build_test_index(
                temporary_path,
                [f"Sentence {number} has to match." for number in range(5)],
            )

            with patch(
                "autocomplete.engine.iter_candidate_entries",
                side_effect=AssertionError("candidate scan should not run"),
            ):
                results = get_best_k_completions("to", index_path)

        self.assertEqual(len(results), 5)
        self.assertTrue(all(result.score == 4 for result in results))

    def test_returns_an_empty_list_for_an_empty_normalized_prefix(self) -> None:
        self.assertEqual(get_best_k_completions("!!!", "unused.sqlite3"), [])

    def test_rejects_an_unsupported_query_without_opening_the_index(self) -> None:
        self.assertEqual(get_best_k_completions("א", "unused.sqlite3"), [])

    def test_finds_a_five_character_query_missing_one_character(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            index_path = self._build_test_index(
                temporary_path,
                ["Python is useful.", "Completely unrelated."],
            )

            results = get_best_k_completions("pythn", index_path)

        self.assertEqual([result.completed_sentence for result in results], ["Python is useful."])
        self.assertEqual(results[0].score, 8)

    def test_heap_keeps_the_best_five_approximate_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            index_path = self._build_test_index(
                temporary_path,
                [
                    "Rots are mentioned.",
                    "Pots are mentioned.",
                    "Lots are mentioned.",
                    "Dots are mentioned.",
                    "Cuts are mentioned.",
                    "Cats are mentioned.",
                ],
            )

            results = get_best_k_completions("cots", index_path)

        self.assertEqual(
            [result.completed_sentence for result in results],
            [
                "Cats are mentioned.",
                "Cuts are mentioned.",
                "Dots are mentioned.",
                "Lots are mentioned.",
                "Pots are mentioned.",
            ],
        )

    def test_short_query_stops_before_lower_scoring_pattern_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            index_path = self._build_test_index(
                temporary_path,
                [f"cot{character} is mentioned." for character in "abcdef"],
            )

            from autocomplete.index import iter_glob_candidate_entries

            searched_pattern_groups = []

            def record_search(index, patterns):
                searched_pattern_groups.append(patterns)
                return iter_glob_candidate_entries(index, patterns)

            with patch(
                "autocomplete.engine.iter_glob_candidate_entries",
                side_effect=record_search,
            ):
                results = get_best_k_completions("cots", index_path)

        searched_patterns = {
            pattern
            for pattern_group in searched_pattern_groups
            for pattern in pattern_group
        }
        self.assertEqual(len(results), 5)
        self.assertTrue(all(result.score == 4 for result in results))
        self.assertNotIn("co?s", searched_patterns)
        self.assertNotIn("c?ts", searched_patterns)

    def test_three_character_query_uses_branch_and_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            index_path = self._build_test_index(
                temporary_path,
                [f"ca{character} is mentioned." for character in "abcdef"],
            )

            from autocomplete.index import iter_glob_candidate_entries

            searched_pattern_groups = []

            def record_search(index, patterns):
                searched_pattern_groups.append(patterns)
                return iter_glob_candidate_entries(index, patterns)

            with patch(
                "autocomplete.engine.iter_glob_candidate_entries",
                side_effect=record_search,
            ):
                results = get_best_k_completions("cat", index_path)

        searched_patterns = {
            pattern
            for pattern_group in searched_pattern_groups
            for pattern in pattern_group
        }
        self.assertEqual(len(results), 5)
        self.assertTrue(all(result.score == 1 for result in results))
        self.assertIn("ca?", searched_patterns)
        self.assertNotIn("c?t", searched_patterns)

    def test_unique_search_groups_duplicate_corpus_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            index_path = self._build_test_index(
                temporary_path,
                ["Python documentation.", "Python documentation.", "Python docs."],
            )

            results = get_best_unique_completions("python", index_path)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].completed_sentence, "Python documentation.")
        repeated = next(
            result
            for result in results
            if result.completed_sentence == "Python documentation."
        )
        self.assertEqual(repeated.occurrence_count, 2)

    def test_unique_search_prefers_popular_sentences_when_scores_tie(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            index_path = self._build_test_index(
                temporary_path,
                [
                    '# Create a "table of contents" file.',
                    "12 Table Of Contents",
                    "16.4. TABLE OF CONTENTS",
                    "4.2. Table of Contents",
                    "Another Table of Contents heading",
                    "Table of Contents",
                    "Table of Contents",
                    "Table of Contents",
                ],
            )

            results = get_best_unique_completions("Table of Contents", index_path)

        self.assertEqual(len(results), 5)
        self.assertEqual(results[0].completed_sentence, "Table of Contents")
        self.assertEqual(results[0].occurrence_count, 3)
        self.assertTrue(all(result.score == 34 for result in results))


if __name__ == "__main__":
    unittest.main()

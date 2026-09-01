"""Ranking, tie-breaking and result limits of the completion engine."""

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from autocomplete.engine import (
    DEFAULT_INDEX_PATH,
    get_best_k_completions,
    get_best_unique_completions,
)
from autocomplete.index import (
    iter_glob_candidate_entries,
    iter_glob_sentence_groups,
    ranked_one_edit_glob_groups,
)
from autocomplete.models import AutoCompleteData, GroupedAutoCompleteData
from tests.support import TemporaryCorpusTestCase


class RankedCompletionTests(TemporaryCorpusTestCase):
    def test_returns_at_most_five_results(self) -> None:
        index_path = self.build_index_from_lines(
            [f"Sentence {number} mentions python." for number in range(20)]
        )

        results = get_best_k_completions("python", index_path)

        self.assertEqual(len(results), 5)

    def test_returns_fewer_results_when_the_corpus_is_small(self) -> None:
        index_path = self.build_index_from_lines(
            ["Python is useful.", "Nothing in common."]
        )

        results = get_best_k_completions("python", index_path)

        self.assertEqual([result.completed_sentence for result in results], ["Python is useful."])

    def test_returns_an_empty_list_when_nothing_can_match(self) -> None:
        index_path = self.build_index_from_lines(["Totally different words here."])

        self.assertEqual(get_best_k_completions("zzzzzzzzzz", index_path), [])

    def test_ignores_input_that_normalizes_to_nothing(self) -> None:
        index_path = self.build_index_from_lines(["Python is useful."])

        for prefix in ["", "   ", "!!! ---", "\n\t"]:
            with self.subTest(prefix=prefix):
                self.assertEqual(get_best_k_completions(prefix, index_path), [])

    def test_ignores_input_outside_the_indexed_alphabet(self) -> None:
        index_path = self.build_index_from_lines(["Python is useful."])

        for prefix in ["שלום עולם", "café", "日本語"]:
            with self.subTest(prefix=prefix):
                self.assertEqual(get_best_k_completions(prefix, index_path), [])

    def test_accepts_the_index_path_as_a_string(self) -> None:
        index_path = self.build_index_from_lines(["Python is useful."])

        from_string = get_best_k_completions("python", str(index_path))
        from_path = get_best_k_completions("python", index_path)

        self.assertEqual(from_string, from_path)

    def test_orders_by_score_before_anything_else(self) -> None:
        index_path = self.build_index_from_lines(
            ["Zulu mentions python.", "Alpha mentions pythxn."]
        )

        results = get_best_k_completions("python", index_path)

        self.assertEqual(
            [result.completed_sentence for result in results],
            ["Zulu mentions python.", "Alpha mentions pythxn."],
        )
        self.assertGreater(results[0].score, results[1].score)

    def test_breaks_score_ties_case_insensitively_then_by_location(self) -> None:
        index_path = self.build_index_from(
            {
                "b.txt": "apple python.\nApple python.\n",
                "a.txt": "Apple python.\n",
            }
        )

        results = get_best_k_completions("python", index_path)

        self.assertEqual(
            [(result.completed_sentence, result.source_text, result.offset) for result in results],
            [
                ("Apple python.", "a.txt", 1),
                ("Apple python.", "b.txt", 2),
                ("apple python.", "b.txt", 1),
            ],
        )

    def test_reports_each_corpus_location_of_a_repeated_sentence(self) -> None:
        index_path = self.build_index_from(
            {"a.txt": "Repeated python line.\nRepeated python line.\n"}
        )

        results = get_best_k_completions("python", index_path)

        self.assertEqual(
            [(result.source_text, result.offset) for result in results],
            [("a.txt", 1), ("a.txt", 2)],
        )

    def test_returns_autocomplete_data_objects(self) -> None:
        index_path = self.build_index_from_lines(["Python is useful."])

        (result,) = get_best_k_completions("python", index_path)

        self.assertEqual(
            result,
            AutoCompleteData(
                completed_sentence="Python is useful.",
                source_text="sentences.txt",
                offset=1,
                score=12,
            ),
        )

    def test_scores_an_exact_match_at_two_points_per_character(self) -> None:
        index_path = self.build_index_from_lines(["Python is useful."])

        (result,) = get_best_k_completions("python is", index_path)

        self.assertEqual(result.score, 2 * len("python is"))

    def test_a_full_page_of_exact_matches_skips_approximate_search(self) -> None:
        index_path = self.build_index_from_lines(
            [f"Line {number} contains python." for number in range(9)]
        )

        results = get_best_k_completions("python", index_path)

        self.assertEqual(len(results), 5)
        self.assertEqual({result.score for result in results}, {12})

    def test_scans_every_pattern_group_when_all_matches_are_weak(self) -> None:
        """A full heap of low scores can never justify an early exit."""

        index_path = self.build_index_from_lines(
            [f"Ello number {number}." for number in range(6)]
        )

        results = get_best_k_completions("hello", index_path)

        # "hello" only matches by deleting its first character, the cheapest
        # possible explanation and the lowest-ranked pattern group.
        self.assertEqual(len(results), 5)
        self.assertEqual({result.score for result in results}, {-2})
        self.assertEqual(
            min(score for score, _patterns in ranked_one_edit_glob_groups("hello")),
            -2,
        )

    def test_stops_early_once_no_pattern_group_can_improve_the_results(self) -> None:
        index_path = self.build_index_from_lines(
            [f"Hello number {number}." for number in range(6)]
        )

        with patch(
            "autocomplete.engine.iter_glob_candidate_entries",
            wraps=iter_glob_candidate_entries,
        ) as glob_search:
            results = get_best_k_completions("hellp", index_path)

        self.assertEqual({result.score for result in results}, {7})
        self.assertLess(
            glob_search.call_count,
            len(ranked_one_edit_glob_groups("hellp")),
        )

    def test_uses_the_documented_default_index_path(self) -> None:
        self.assertEqual(DEFAULT_INDEX_PATH, Path("autocomplete.sqlite3"))

    def test_reports_a_missing_index_as_a_database_error(self) -> None:
        with self.assertRaises(sqlite3.OperationalError):
            get_best_k_completions("python", self.missing_index_path())


class UniqueCompletionTests(TemporaryCorpusTestCase):
    def test_groups_duplicate_locations_into_one_suggestion(self) -> None:
        index_path = self.build_index_from(
            {
                "a.txt": "Python is useful.\nPython is useful.\n",
                "b.txt": "Python is useful.\n",
            }
        )

        results = get_best_unique_completions("python", index_path)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].completed_sentence, "Python is useful.")
        self.assertEqual(results[0].occurrence_count, 3)
        self.assertEqual(results[0].score, 12)

    def test_returns_five_unique_sentences_for_a_repetitive_corpus(self) -> None:
        lines = []
        for number in range(8):
            lines.extend([f"Python line {number}."] * 4)
        index_path = self.build_index_from_lines(lines)

        results = get_best_unique_completions("python", index_path)

        self.assertEqual(len(results), 5)
        self.assertEqual(
            len({result.completed_sentence for result in results}),
            5,
        )

    def test_prefers_the_more_frequent_sentence_when_scores_tie(self) -> None:
        index_path = self.build_index_from_lines(
            ["Zebra python line."] * 3 + ["Alpha python line."]
        )

        results = get_best_unique_completions("python", index_path)

        self.assertEqual(
            [(result.completed_sentence, result.occurrence_count) for result in results],
            [("Zebra python line.", 3), ("Alpha python line.", 1)],
        )

    def test_ranks_a_typo_below_an_exact_match(self) -> None:
        index_path = self.build_index_from_lines(
            ["Documentation is useful.", "Documentxtion is useful."]
        )

        results = get_best_unique_completions("documentation", index_path)

        self.assertEqual(
            [result.completed_sentence for result in results],
            ["Documentation is useful.", "Documentxtion is useful."],
        )

    def test_returns_grouped_objects_with_a_usable_sentence_id(self) -> None:
        index_path = self.build_index_from_lines(["Python is useful."])

        (result,) = get_best_unique_completions("python", index_path)

        with closing(sqlite3.connect(index_path)) as connection:
            (stored_sentence,) = connection.execute(
                "SELECT original_sentence FROM sentence_groups WHERE id = ?",
                (result.sentence_id,),
            ).fetchone()

        self.assertIsInstance(result, GroupedAutoCompleteData)
        self.assertEqual(stored_sentence, result.completed_sentence)

    def test_scans_every_pattern_group_when_all_matches_are_weak(self) -> None:
        index_path = self.build_index_from_lines(
            [f"Ello number {number}." for number in range(6)]
        )

        results = get_best_unique_completions("hello", index_path)

        self.assertEqual(len(results), 5)
        self.assertEqual({result.score for result in results}, {-2})

    def test_stops_early_once_no_pattern_group_can_improve_the_results(self) -> None:
        index_path = self.build_index_from_lines(
            [f"Hello number {number}." for number in range(6)]
        )

        with patch(
            "autocomplete.engine.iter_glob_sentence_groups",
            wraps=iter_glob_sentence_groups,
        ) as glob_search:
            results = get_best_unique_completions("hellp", index_path)

        self.assertEqual({result.score for result in results}, {7})
        self.assertLess(
            glob_search.call_count,
            len(ranked_one_edit_glob_groups("hellp")),
        )

    def test_ignores_unsearchable_and_unsupported_input(self) -> None:
        index_path = self.build_index_from_lines(["Python is useful."])

        self.assertEqual(get_best_unique_completions("  !!  ", index_path), [])
        self.assertEqual(get_best_unique_completions("שלום", index_path), [])


if __name__ == "__main__":
    unittest.main()

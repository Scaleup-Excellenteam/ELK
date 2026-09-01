"""Penalty tables, one-edit scoring branches and an independent oracle."""

import random
import unittest

from autocomplete.scoring import (
    _removed_character_position,
    _single_substitution_position,
    _windows,
    best_match_score,
    best_normalized_match_score,
    insertion_or_deletion_penalty,
    substitution_penalty,
)


class PenaltyTableTests(unittest.TestCase):
    def test_substitution_penalty_decreases_until_it_reaches_its_floor(self) -> None:
        penalties = [substitution_penalty(position) for position in range(1, 9)]

        self.assertEqual(penalties, [5, 4, 3, 2, 1, 1, 1, 1])

    def test_insertion_or_deletion_penalty_decreases_until_its_floor(self) -> None:
        penalties = [
            insertion_or_deletion_penalty(position) for position in range(1, 9)
        ]

        self.assertEqual(penalties, [10, 8, 6, 4, 2, 2, 2, 2])

    def test_an_edit_costs_twice_as_much_as_a_replacement_early_on(self) -> None:
        for position in range(1, 6):
            with self.subTest(position=position):
                self.assertEqual(
                    insertion_or_deletion_penalty(position),
                    2 * substitution_penalty(position),
                )


class SingleEditHelperTests(unittest.TestCase):
    def test_reports_the_only_mismatching_position(self) -> None:
        self.assertEqual(_single_substitution_position("hello", "hallo"), 2)

    def test_reports_no_position_when_the_texts_are_identical(self) -> None:
        self.assertIsNone(_single_substitution_position("hello", "hello"))

    def test_reports_no_position_when_two_characters_differ(self) -> None:
        self.assertIsNone(_single_substitution_position("hello", "hallu"))

    def test_finds_where_a_character_can_be_removed(self) -> None:
        self.assertEqual(_removed_character_position("helxlo", "hello"), 4)

    def test_removing_a_repeated_character_reports_the_earliest_position(self) -> None:
        self.assertEqual(_removed_character_position("hello", "helo"), 4)

    def test_rejects_a_length_difference_other_than_one(self) -> None:
        self.assertIsNone(_removed_character_position("hello", "hello"))
        self.assertIsNone(_removed_character_position("hellox", "hell"))

    def test_rejects_a_pair_that_needs_more_than_one_removal(self) -> None:
        self.assertIsNone(_removed_character_position("abcdef", "azcef"))

    def test_windows_walks_every_substring_of_one_length(self) -> None:
        self.assertEqual(list(_windows("abcd", 2)), ["ab", "bc", "cd"])
        self.assertEqual(list(_windows("abcd", 4)), ["abcd"])
        self.assertEqual(list(_windows("abcd", 5)), [])


class SubstitutionScoreTests(unittest.TestCase):
    def test_scores_every_replacement_position_of_a_five_letter_query(self) -> None:
        sentence = "hello world"
        expected_scores = {
            "jello": 3,
            "hallo": 4,
            "hexlo": 5,
            "helwo": 6,
            "hellx": 7,
        }

        for query, expected_score in expected_scores.items():
            with self.subTest(query=query):
                self.assertEqual(
                    best_normalized_match_score(query, sentence),
                    expected_score,
                )

    def test_late_replacements_share_the_smallest_penalty(self) -> None:
        sentence = "abcdefghij"

        fifth_position = best_normalized_match_score("abcdxfg", sentence)
        sixth_position = best_normalized_match_score("abcdexg", sentence)
        seventh_position = best_normalized_match_score("abcdefx", sentence)

        self.assertEqual(fifth_position, 11)
        self.assertEqual(sixth_position, 11)
        self.assertEqual(seventh_position, 11)


class DeletionScoreTests(unittest.TestCase):
    """The query holds one character too many."""

    def test_scores_an_extra_character_by_its_position(self) -> None:
        sentence = "abcdefghij"
        expected_scores = {
            "zabcde": 0,
            "azbcde": 2,
            "abzcde": 4,
            "abczde": 6,
            "abcdze": 8,
        }

        for query, expected_score in expected_scores.items():
            with self.subTest(query=query):
                self.assertEqual(
                    best_normalized_match_score(query, sentence),
                    expected_score,
                )

    def test_a_trailing_extra_character_scores_as_a_replacement(self) -> None:
        # "abcdez" is one deletion away from "abcde" (score 8) but also one
        # replacement away from the window "abcdef", which is worth more.
        self.assertEqual(best_normalized_match_score("abcdez", "abcdefghij"), 9)


class InsertionScoreTests(unittest.TestCase):
    """The query is missing one character that the sentence contains."""

    def test_scores_a_missing_character_by_its_position(self) -> None:
        sentence = "abcdefghij"
        expected_scores = {
            "abdef": 4,
            "abcef": 6,
            "abcdf": 8,
        }

        for query, expected_score in expected_scores.items():
            with self.subTest(query=query):
                self.assertEqual(
                    best_normalized_match_score(query, sentence),
                    expected_score,
                )

    def test_a_missing_edge_character_is_scored_as_an_exact_match(self) -> None:
        # Dropping the first or last character of a window leaves a shorter
        # window, so the substring branch always wins those cases.
        self.assertEqual(best_normalized_match_score("ello", "hello world"), 8)
        self.assertEqual(best_normalized_match_score("hell", "hello world"), 8)

    def test_an_early_missing_character_scores_as_a_replacement(self) -> None:
        # "acdef" misses "b" (score 2) but only replaces the "b" of "bcdef".
        self.assertEqual(best_normalized_match_score("acdef", "abcdefghij"), 3)


class BestMatchScoreContractTests(unittest.TestCase):
    def test_an_exact_substring_scores_two_points_per_character(self) -> None:
        sentence = "the quick brown fox"

        for length in range(1, len(sentence) + 1):
            with self.subTest(length=length):
                self.assertEqual(
                    best_normalized_match_score(sentence[:length], sentence),
                    2 * length,
                )

    def test_rejects_a_sentence_needing_two_edits(self) -> None:
        self.assertIsNone(best_normalized_match_score("hxlly", "hello world"))

    def test_rejects_a_transposition(self) -> None:
        self.assertIsNone(best_normalized_match_score("hlelo", "hello world"))

    def test_rejects_a_query_much_longer_than_the_sentence(self) -> None:
        self.assertIsNone(best_normalized_match_score("hello world", "hello"))

    def test_returns_none_for_empty_input(self) -> None:
        self.assertIsNone(best_normalized_match_score("", "hello"))
        self.assertIsNone(best_normalized_match_score("hello", ""))
        self.assertIsNone(best_normalized_match_score("", ""))

    def test_normalizes_both_sides_before_comparing(self) -> None:
        self.assertEqual(
            best_match_score("  TO   BE!  ", "To be, or not to be."),
            best_normalized_match_score("to be", "to be or not to be"),
        )

    def test_a_score_may_be_negative_for_a_short_damaged_query(self) -> None:
        self.assertEqual(best_normalized_match_score("x", "hello"), -5)


def _reference_best_score(query: str, sentence: str) -> int | None:
    """Score by generating every one-edit variant and testing containment."""

    if not query or not sentence:
        return None

    if query in sentence:
        return 2 * len(query)

    alphabet = set(sentence)
    query_length = len(query)
    scores = []

    for position in range(1, query_length + 1):
        index = position - 1

        for character in alphabet:
            replaced = query[:index] + character + query[index + 1 :]
            if replaced in sentence:
                scores.append(
                    2 * (query_length - 1) - substitution_penalty(position)
                )

        shortened = query[:index] + query[index + 1 :]
        if shortened and shortened in sentence:
            scores.append(
                2 * (query_length - 1) - insertion_or_deletion_penalty(position)
            )

    for position in range(1, query_length + 2):
        index = position - 1
        for character in alphabet:
            lengthened = query[:index] + character + query[index:]
            if lengthened in sentence:
                scores.append(
                    2 * query_length - insertion_or_deletion_penalty(position)
                )

    return max(scores) if scores else None


class ScoringOracleTests(unittest.TestCase):
    """Cross-check the windowed scorer against brute-force edit generation."""

    def test_matches_generated_one_edit_variants_on_random_input(self) -> None:
        generator = random.Random(20240501)
        alphabet = "abcd "
        checked = 0

        for _ in range(400):
            sentence = "".join(
                generator.choice(alphabet) for _ in range(generator.randint(1, 14))
            )
            query = "".join(
                generator.choice(alphabet) for _ in range(generator.randint(1, 6))
            )

            with self.subTest(query=query, sentence=sentence):
                self.assertEqual(
                    best_normalized_match_score(query, sentence),
                    _reference_best_score(query, sentence),
                )
            checked += 1

        self.assertEqual(checked, 400)


if __name__ == "__main__":
    unittest.main()

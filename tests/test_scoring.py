import unittest

from autocomplete.scoring import best_match_score


class BestMatchScoreAppendixTests(unittest.TestCase):
    sentence = "To be or not to be, that is the question."

    def test_exact_match(self) -> None:
        self.assertEqual(best_match_score("To be", self.sentence), 10)

    def test_exact_match_ignores_case(self) -> None:
        self.assertEqual(best_match_score("or Not", self.sentence), 12)

    def test_exact_match_ignores_punctuation(self) -> None:
        self.assertEqual(best_match_score("be, that", self.sentence), 14)

    def test_substitution_at_position_one(self) -> None:
        self.assertEqual(best_match_score("2o be", self.sentence), 3)

    def test_substitution_at_position_four(self) -> None:
        self.assertEqual(best_match_score("to pe", self.sentence), 6)

    def test_deletes_an_extra_query_character(self) -> None:
        self.assertEqual(best_match_score("or knot", self.sentence), 8)

    def test_inserts_a_missing_query_character(self) -> None:
        self.assertEqual(best_match_score("or nt", self.sentence), 8)

    def test_rejects_more_than_one_edit(self) -> None:
        self.assertIsNone(best_match_score("not be", self.sentence))


class BestMatchScoreEdgeCaseTests(unittest.TestCase):
    def test_uses_the_best_match_when_a_sentence_has_multiple_candidates(self) -> None:
        self.assertEqual(best_match_score("cat", "cot and cat"), 6)

    def test_returns_none_for_an_empty_normalized_query(self) -> None:
        self.assertIsNone(best_match_score("!!!", "A sentence"))


if __name__ == "__main__":
    unittest.main()


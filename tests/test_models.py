import unittest

from autocomplete.models import AutoCompleteData


class AutoCompleteDataTests(unittest.TestCase):
    def test_keeps_the_original_sentence_and_source_location(self) -> None:
        suggestion = AutoCompleteData(
            completed_sentence="To be, or not to be.",
            source_text="plays/hamlet.txt",
            offset=17,
            score=10,
        )

        self.assertEqual(suggestion.completed_sentence, "To be, or not to be.")
        self.assertEqual(suggestion.source_text, "plays/hamlet.txt")
        self.assertEqual(suggestion.offset, 17)
        self.assertEqual(suggestion.score, 10)


if __name__ == "__main__":
    unittest.main()


import unittest

from autocomplete.normalization import normalize_text


class NormalizeTextTests(unittest.TestCase):
    def test_ignores_case_punctuation_and_repeated_spaces(self) -> None:
        self.assertEqual(
            normalize_text("  To BE,   or NOT!!  "),
            "to be or not",
        )

    def test_collapses_other_whitespace_between_words(self) -> None:
        self.assertEqual(normalize_text("to\tbe\ncontinued"), "to be continued")

    def test_removes_punctuation_without_changing_the_original(self) -> None:
        original = "Hello, world!"

        normalized = normalize_text(original)

        self.assertEqual(normalized, "hello world")
        self.assertEqual(original, "Hello, world!")

    def test_returns_empty_text_when_there_are_no_searchable_characters(self) -> None:
        self.assertEqual(normalize_text("... !!!"), "")


if __name__ == "__main__":
    unittest.main()


"""Character-domain rules applied to user input and corpus text."""

import string
import unittest

from autocomplete.normalization import is_supported_normalized_query, normalize_text


class NormalizeTextCharacterTests(unittest.TestCase):
    def test_removes_every_ascii_punctuation_character(self) -> None:
        self.assertEqual(normalize_text(string.punctuation), "")

    def test_keeps_digits_and_letters(self) -> None:
        self.assertEqual(normalize_text("Chapter 12: Section 3B"), "chapter 12 section 3b")

    def test_joins_words_split_by_removed_punctuation(self) -> None:
        self.assertEqual(normalize_text("state-of-the-art"), "stateoftheart")

    def test_treats_tabs_and_newlines_as_word_separators(self) -> None:
        self.assertEqual(normalize_text("one\ttwo\nthree\r\nfour"), "one two three four")

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        self.assertEqual(normalize_text("   padded text   "), "padded text")

    def test_returns_empty_text_for_whitespace_only_input(self) -> None:
        self.assertEqual(normalize_text(" \t\n "), "")

    def test_returns_empty_text_for_empty_input(self) -> None:
        self.assertEqual(normalize_text(""), "")

    def test_lower_cases_without_removing_non_ascii_letters(self) -> None:
        self.assertEqual(normalize_text("CAFÉ Ünicode"), "café ünicode")

    def test_is_idempotent(self) -> None:
        for text in ["To be, or not to be.", "  MIXED   Case!  ", "", "12,345"]:
            with self.subTest(text=text):
                once = normalize_text(text)
                self.assertEqual(normalize_text(once), once)


class SupportedQueryDomainTests(unittest.TestCase):
    def test_accepts_lower_case_letters_digits_and_spaces(self) -> None:
        self.assertTrue(
            is_supported_normalized_query(string.ascii_lowercase + string.digits + " ")
        )

    def test_accepts_empty_text(self) -> None:
        self.assertTrue(is_supported_normalized_query(""))

    def test_rejects_upper_case_text_that_was_never_normalized(self) -> None:
        self.assertFalse(is_supported_normalized_query("Hello"))

    def test_rejects_letters_outside_the_indexed_english_alphabet(self) -> None:
        for text in ["café", "שלום", "привет", "日本語", "emoji 🙂"]:
            with self.subTest(text=text):
                self.assertFalse(is_supported_normalized_query(text))

    def test_normalized_english_text_always_stays_in_the_domain(self) -> None:
        self.assertTrue(
            is_supported_normalized_query(
                normalize_text("To be, or NOT to be -- that is question 12!")
            )
        )


if __name__ == "__main__":
    unittest.main()

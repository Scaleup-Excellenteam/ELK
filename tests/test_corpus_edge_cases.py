"""Corpus reading rules for unusual files and lines."""

import unittest
from pathlib import Path

from autocomplete.corpus import CorpusEntry, iter_corpus_entries
from tests.support import TemporaryCorpusTestCase


class CorpusFileSelectionTests(TemporaryCorpusTestCase):
    def test_reads_only_text_files(self) -> None:
        corpus_path = self.write_corpus(
            {
                "kept.txt": "Indexed sentence.\n",
                "skipped.md": "Markdown sentence.\n",
                "skipped.csv": "csv,sentence\n",
                "notes/kept-too.txt": "Nested sentence.\n",
            }
        )

        entries = list(iter_corpus_entries(corpus_path))

        self.assertEqual(
            [entry.original_sentence for entry in entries],
            ["Indexed sentence.", "Nested sentence."],
        )

    def test_reports_nested_paths_with_forward_slashes(self) -> None:
        corpus_path = self.write_corpus({"books/part one/chapter.txt": "Deep line.\n"})

        entries = list(iter_corpus_entries(corpus_path))

        self.assertEqual(entries[0].source_text, "books/part one/chapter.txt")

    def test_yields_nothing_for_an_empty_directory(self) -> None:
        corpus_path = self.write_corpus({})

        self.assertEqual(list(iter_corpus_entries(corpus_path)), [])

    def test_yields_nothing_for_an_empty_file(self) -> None:
        corpus_path = self.write_corpus({"empty.txt": ""})

        self.assertEqual(list(iter_corpus_entries(corpus_path)), [])

    def test_accepts_a_path_object_and_a_string(self) -> None:
        corpus_path = self.write_corpus({"a.txt": "One line.\n"})

        from_path = list(iter_corpus_entries(corpus_path))
        from_string = list(iter_corpus_entries(str(corpus_path)))

        self.assertEqual(from_path, from_string)

    def test_rejects_a_file_used_as_a_corpus_root(self) -> None:
        corpus_path = self.write_corpus({"a.txt": "One line.\n"})

        with self.assertRaises(ValueError):
            list(iter_corpus_entries(corpus_path / "a.txt"))

    def test_rejects_a_missing_directory(self) -> None:
        with self.assertRaises(ValueError):
            list(iter_corpus_entries(self.temporary_path / "absent"))


class CorpusLineTests(TemporaryCorpusTestCase):
    def test_skips_unsearchable_lines_but_keeps_line_numbers(self) -> None:
        corpus_path = self.write_corpus(
            {
                "mixed.txt": (
                    "\n"
                    "   \n"
                    "!!! ---\n"
                    "First real sentence.\n"
                    "\n"
                    "Second real sentence.\n"
                )
            }
        )

        entries = list(iter_corpus_entries(corpus_path))

        self.assertEqual(
            [(entry.original_sentence, entry.offset) for entry in entries],
            [("First real sentence.", 4), ("Second real sentence.", 6)],
        )

    def test_keeps_the_original_text_next_to_its_normalized_form(self) -> None:
        corpus_path = self.write_corpus({"a.txt": "  To Be, Or Not To Be.  \n"})

        entry = next(iter_corpus_entries(corpus_path))

        self.assertEqual(
            entry,
            CorpusEntry(
                original_sentence="To Be, Or Not To Be.",
                normalized_sentence="to be or not to be",
                source_text="a.txt",
                offset=1,
            ),
        )

    def test_strips_carriage_returns_and_page_break_characters(self) -> None:
        corpus_path = self.write_corpus({"a.txt": "\x0cWindows line.\r\n\x00Padded.\x1f\n"})

        entries = list(iter_corpus_entries(corpus_path))

        self.assertEqual(
            [entry.original_sentence for entry in entries],
            ["Windows line.", "Padded."],
        )

    def test_keeps_control_characters_inside_a_sentence(self) -> None:
        corpus_path = self.write_corpus({"a.txt": "Inner\x0cbreak here.\n"})

        entry = next(iter_corpus_entries(corpus_path))

        self.assertEqual(entry.original_sentence, "Inner\x0cbreak here.")

    def test_reads_a_final_line_without_a_trailing_newline(self) -> None:
        corpus_path = self.write_corpus({"a.txt": "First line.\nLast line."})

        entries = list(iter_corpus_entries(corpus_path))

        self.assertEqual(
            [(entry.original_sentence, entry.offset) for entry in entries],
            [("First line.", 1), ("Last line.", 2)],
        )

    def test_reads_files_lazily(self) -> None:
        corpus_path = self.write_corpus(
            {"a.txt": "\n".join(f"Line number {number}." for number in range(1000))}
        )

        entries = iter_corpus_entries(corpus_path)
        first_entry = next(entries)

        self.assertEqual(first_entry.offset, 1)
        self.assertEqual(next(entries).offset, 2)


if __name__ == "__main__":
    unittest.main()

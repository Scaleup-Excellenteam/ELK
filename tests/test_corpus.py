import tempfile
import unittest
from pathlib import Path

from autocomplete.corpus import CorpusEntry, iter_corpus_entries


class IterCorpusEntriesTests(unittest.TestCase):
    def test_removes_pdf_page_break_control_characters_from_sentence_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pages.txt").write_text(
                "\x02\f  Clean visible sentence\v\n",
                encoding="utf-8",
            )

            entries = list(iter_corpus_entries(root))

        self.assertEqual(entries[0].original_sentence, "Clean visible sentence")

    def test_reads_nested_files_and_keeps_original_line_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            nested_directory = root / "plays"
            nested_directory.mkdir()
            (nested_directory / "hamlet.txt").write_text(
                "To BE, or not to be.\n\nThat is the question!\n",
                encoding="utf-8",
            )

            entries = list(iter_corpus_entries(root))

        self.assertEqual(
            entries,
            [
                CorpusEntry(
                    original_sentence="To BE, or not to be.",
                    normalized_sentence="to be or not to be",
                    source_text="plays/hamlet.txt",
                    offset=1,
                ),
                CorpusEntry(
                    original_sentence="That is the question!",
                    normalized_sentence="that is the question",
                    source_text="plays/hamlet.txt",
                    offset=3,
                ),
            ],
        )

    def test_reads_text_files_in_a_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "z.txt").write_text("Last file\n", encoding="utf-8")
            (root / "a.txt").write_text("First file\n", encoding="utf-8")

            sources = [entry.source_text for entry in iter_corpus_entries(root)]

        self.assertEqual(sources, ["a.txt", "z.txt"])

    def test_rejects_a_missing_corpus_directory(self) -> None:
        with self.assertRaisesRegex(ValueError, "Corpus directory does not exist"):
            list(iter_corpus_entries("directory-that-does-not-exist"))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

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

    def test_reads_text_files_directly_from_a_zip_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "Archive.zip"
            with ZipFile(archive_path, "w") as archive:
                archive.writestr("Archive/z.txt", "Last file\n")
                archive.writestr(
                    "Archive/nested/a.txt",
                    "First line\n\nSecond line\n",
                )
                archive.writestr("Archive/ignored.md", "Not part of the corpus\n")

            entries = list(iter_corpus_entries(archive_path))

        self.assertEqual(
            entries,
            [
                CorpusEntry("First line", "first line", "nested/a.txt", 1),
                CorpusEntry("Second line", "second line", "nested/a.txt", 3),
                CorpusEntry("Last file", "last file", "z.txt", 1),
            ],
        )

    def test_reads_a_zip_whose_text_files_have_no_wrapper_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "corpus.zip"
            with ZipFile(archive_path, "w") as archive:
                archive.writestr("sentences.txt", "A zipped sentence.\n")

            entries = list(iter_corpus_entries(archive_path))

        self.assertEqual(entries[0].source_text, "sentences.txt")
        self.assertEqual(entries[0].original_sentence, "A zipped sentence.")

    def test_rejects_a_missing_corpus_directory(self) -> None:
        with self.assertRaisesRegex(ValueError, "directory or ZIP archive"):
            list(iter_corpus_entries("directory-that-does-not-exist"))


if __name__ == "__main__":
    unittest.main()

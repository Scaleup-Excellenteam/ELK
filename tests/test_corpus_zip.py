"""Tests for reading a corpus straight out of a ZIP archive."""

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile, ZipInfo

from autocomplete.corpus import CorpusEntry, iter_corpus_entries


class ZipCorpusSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary_path = Path(temporary_directory.name)

    def write_archive(self, members: dict[str, str], name: str = "corpus.zip") -> Path:
        """Write ``{member name: file text}`` into a fresh ZIP archive."""

        archive_path = self.temporary_path / name
        with ZipFile(archive_path, "w") as archive:
            for member_name, text in members.items():
                archive.writestr(member_name, text)
        return archive_path

    def test_ignores_members_that_are_not_text_files(self) -> None:
        archive_path = self.write_archive(
            {
                "notes.md": "Markdown line\n",
                "data.json": '{"line": 1}\n',
                "sentences.txt": "A real sentence.\n",
            }
        )

        sources = [entry.source_text for entry in iter_corpus_entries(archive_path)]

        self.assertEqual(sources, ["sentences.txt"])

    def test_reads_text_files_whose_extension_is_upper_case(self) -> None:
        archive_path = self.write_archive({"SHOUTING.TXT": "Upper case member.\n"})

        entries = list(iter_corpus_entries(archive_path))

        self.assertEqual(
            entries,
            [CorpusEntry("Upper case member.", "upper case member", "SHOUTING.TXT", 1)],
        )

    def test_skips_directory_members(self) -> None:
        archive_path = self.temporary_path / "with-directories.zip"
        with ZipFile(archive_path, "w") as archive:
            archive.writestr(ZipInfo("plays/"), "")
            archive.writestr("plays/hamlet.txt", "To be.\n")

        sources = [entry.source_text for entry in iter_corpus_entries(archive_path)]

        self.assertEqual(sources, ["plays/hamlet.txt"])

    def test_yields_nothing_for_an_archive_without_text_files(self) -> None:
        archive_path = self.write_archive({"readme.md": "No corpus here\n"})

        self.assertEqual(list(iter_corpus_entries(archive_path)), [])

    def test_yields_nothing_for_an_empty_archive(self) -> None:
        archive_path = self.write_archive({})

        self.assertEqual(list(iter_corpus_entries(archive_path)), [])

    def test_reads_members_in_name_order_regardless_of_write_order(self) -> None:
        archive_path = self.write_archive(
            {
                "z.txt": "Last\n",
                "m/b.txt": "Middle second\n",
                "a.txt": "First\n",
                "m/a.txt": "Middle first\n",
            }
        )

        sources = [entry.source_text for entry in iter_corpus_entries(archive_path)]

        self.assertEqual(sources, ["a.txt", "m/a.txt", "m/b.txt", "z.txt"])

    def test_normalizes_backslash_separators_in_member_names(self) -> None:
        archive_path = self.write_archive({"plays\\hamlet.txt": "To be.\n"})

        sources = [entry.source_text for entry in iter_corpus_entries(archive_path)]

        self.assertEqual(sources, ["plays/hamlet.txt"])

    def test_strips_a_wrapper_directory_named_archive_in_any_case(self) -> None:
        for wrapper in ("Archive", "archive", "ARCHIVE"):
            with self.subTest(wrapper=wrapper):
                archive_path = self.write_archive(
                    {f"{wrapper}/sentences.txt": "A sentence.\n"},
                    name=f"{wrapper}-wrapped.zip",
                )

                sources = [
                    entry.source_text for entry in iter_corpus_entries(archive_path)
                ]

                self.assertEqual(sources, ["sentences.txt"])

    def test_strips_the_wrapper_directory_only_once(self) -> None:
        archive_path = self.write_archive({"Archive/archive/sentences.txt": "A line.\n"})

        sources = [entry.source_text for entry in iter_corpus_entries(archive_path)]

        self.assertEqual(sources, ["archive/sentences.txt"])

    def test_keeps_directories_whose_name_only_starts_with_archive(self) -> None:
        archive_path = self.write_archive({"archives/sentences.txt": "A line.\n"})

        sources = [entry.source_text for entry in iter_corpus_entries(archive_path)]

        self.assertEqual(sources, ["archives/sentences.txt"])

    def test_strips_a_backslash_written_wrapper_directory(self) -> None:
        archive_path = self.write_archive({"Archive\\sentences.txt": "A line.\n"})

        sources = [entry.source_text for entry in iter_corpus_entries(archive_path)]

        self.assertEqual(sources, ["sentences.txt"])

    def test_keeps_original_line_numbers_and_skips_blank_lines(self) -> None:
        archive_path = self.write_archive(
            {"sentences.txt": "First line\n\n   \nFourth line\n"}
        )

        entries = list(iter_corpus_entries(archive_path))

        self.assertEqual(
            [(entry.original_sentence, entry.offset) for entry in entries],
            [("First line", 1), ("Fourth line", 4)],
        )

    def test_reads_a_last_line_without_a_trailing_newline(self) -> None:
        archive_path = self.write_archive({"sentences.txt": "First\nUnterminated"})

        entries = list(iter_corpus_entries(archive_path))

        self.assertEqual([entry.original_sentence for entry in entries], ["First", "Unterminated"])

    def test_strips_page_break_control_characters_inside_an_archive(self) -> None:
        archive_path = self.write_archive(
            {"pages.txt": "\x02\f  Clean visible sentence\v\n"}
        )

        entries = list(iter_corpus_entries(archive_path))

        self.assertEqual(entries[0].original_sentence, "Clean visible sentence")

    def test_decodes_non_ascii_member_text_as_utf_8(self) -> None:
        archive_path = self.write_archive({"accents.txt": "Café notes\n"})

        entries = list(iter_corpus_entries(archive_path))

        self.assertEqual(entries[0].original_sentence, "Café notes")

    def test_normalizes_sentences_read_from_an_archive(self) -> None:
        archive_path = self.write_archive({"sentences.txt": "To BE,   or not to be!\n"})

        entries = list(iter_corpus_entries(archive_path))

        self.assertEqual(entries[0].normalized_sentence, "to be or not to be")

    def test_streams_entries_lazily_instead_of_reading_everything_first(self) -> None:
        archive_path = self.write_archive(
            {"sentences.txt": "".join(f"Line {number}\n" for number in range(1, 1001))}
        )

        entries = iter_corpus_entries(archive_path)
        first_entry = next(entries)
        entries.close()

        self.assertEqual(first_entry.original_sentence, "Line 1")

    def test_rejects_a_regular_file_that_is_not_a_zip_archive(self) -> None:
        plain_file = self.temporary_path / "sentences.txt"
        plain_file.write_text("Not an archive\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "directory or ZIP archive"):
            list(iter_corpus_entries(plain_file))

    def test_rejects_a_file_with_a_zip_extension_that_is_not_a_zip(self) -> None:
        fake_archive = self.temporary_path / "corpus.zip"
        fake_archive.write_text("Not really a ZIP\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "directory or ZIP archive"):
            list(iter_corpus_entries(fake_archive))

    def test_rejects_a_missing_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "directory or ZIP archive"):
            list(iter_corpus_entries(self.temporary_path / "absent.zip"))

    def test_accepts_the_archive_path_as_a_string(self) -> None:
        archive_path = self.write_archive({"sentences.txt": "A sentence.\n"})

        entries = list(iter_corpus_entries(str(archive_path)))

        self.assertEqual(entries[0].source_text, "sentences.txt")

    def test_prefers_the_directory_branch_when_a_directory_is_given(self) -> None:
        corpus_path = self.temporary_path / "corpus"
        corpus_path.mkdir()
        (corpus_path / "sentences.txt").write_text("From a directory.\n", encoding="utf-8")
        self.write_archive({"sentences.txt": "From an archive.\n"})

        entries = list(iter_corpus_entries(corpus_path))

        self.assertEqual([entry.original_sentence for entry in entries], ["From a directory."])


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from autocomplete.index import (
    _split_balanced,
    build_index,
    find_exact_matches,
    find_exact_sentence_groups,
    get_sentence_locations,
    iter_candidate_entries,
    ranked_one_edit_glob_groups,
)


class CorpusIndexTests(unittest.TestCase):
    def test_builds_the_index_directly_from_a_zip_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            archive_path = temporary_path / "Archive.zip"
            with ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "Archive/sentences.txt",
                    "A sentence built directly from ZIP.\n",
                )
            index_path = temporary_path / "autocomplete.sqlite3"

            stored_sentences = build_index(archive_path, index_path)
            matches = find_exact_matches(index_path, "directly from zip")

        self.assertEqual(stored_sentences, 1)
        self.assertEqual(matches[0].source_text, "sentences.txt")

    def test_builds_an_index_and_finds_a_normalized_substring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "corpus"
            corpus_path.mkdir()
            (corpus_path / "sentences.txt").write_text(
                "To be, or not to be.\nSomething completely different.\n",
                encoding="utf-8",
            )
            index_path = temporary_path / "autocomplete.sqlite3"

            stored_sentences = build_index(corpus_path, index_path)
            matches = find_exact_matches(index_path, "BE,   OR")

        self.assertEqual(stored_sentences, 2)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].original_sentence, "To be, or not to be.")
        self.assertEqual(matches[0].normalized_sentence, "to be or not to be")
        self.assertEqual(matches[0].source_text, "sentences.txt")
        self.assertEqual(matches[0].offset, 1)

    def test_sorts_equal_matches_alphabetically_and_applies_the_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "corpus"
            corpus_path.mkdir()
            (corpus_path / "sentences.txt").write_text(
                "Zulu has this phrase.\nAlpha has this phrase.\nBeta has this phrase.\n",
                encoding="utf-8",
            )
            index_path = temporary_path / "autocomplete.sqlite3"
            build_index(corpus_path, index_path)

            matches = find_exact_matches(index_path, "this phrase", limit=2)

        self.assertEqual(
            [match.original_sentence for match in matches],
            ["Alpha has this phrase.", "Beta has this phrase."],
        )

    def test_returns_no_matches_for_an_empty_normalized_query(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "corpus"
            corpus_path.mkdir()
            (corpus_path / "sentences.txt").write_text("A sentence.\n", encoding="utf-8")
            index_path = temporary_path / "autocomplete.sqlite3"
            build_index(corpus_path, index_path)

            matches = find_exact_matches(index_path, "!!!")

        self.assertEqual(matches, [])

    def test_stores_the_last_partially_filled_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "corpus"
            corpus_path.mkdir()
            (corpus_path / "sentences.txt").write_text(
                "First sentence.\nSecond sentence.\nThird sentence.\n",
                encoding="utf-8",
            )
            index_path = temporary_path / "autocomplete.sqlite3"

            stored_sentences = build_index(corpus_path, index_path, batch_size=2)
            matches = find_exact_matches(index_path, "sentence", limit=10)

        self.assertEqual(stored_sentences, 3)
        self.assertEqual(len(matches), 3)

    def test_groups_duplicate_sentences_and_keeps_every_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "corpus"
            corpus_path.mkdir()
            (corpus_path / "sentences.txt").write_text(
                "Repeated sentence.\nDifferent sentence.\nRepeated sentence.\n",
                encoding="utf-8",
            )
            index_path = temporary_path / "autocomplete.sqlite3"
            build_index(corpus_path, index_path, batch_size=2)

            groups = find_exact_sentence_groups(index_path, "repeated")
            locations = get_sentence_locations(index_path, groups[0][0])

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][1], "Repeated sentence.")
        self.assertEqual(groups[0][3], 2)
        self.assertEqual(locations, [("sentences.txt", 1), ("sentences.txt", 3)])

    def test_rejects_a_non_positive_batch_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "Batch size must be greater than zero"):
            build_index("unused", "unused.sqlite3", batch_size=0)

    def test_finds_a_candidate_when_one_query_half_contains_a_typo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "corpus"
            corpus_path.mkdir()
            (corpus_path / "sentences.txt").write_text(
                "To be or not to be.\nCompletely unrelated.\n",
                encoding="utf-8",
            )
            index_path = temporary_path / "autocomplete.sqlite3"
            build_index(corpus_path, index_path)

            candidates = list(iter_candidate_entries(index_path, "to pe or not"))

        self.assertEqual(
            [candidate.original_sentence for candidate in candidates],
            ["To be or not to be."],
        )

    def test_short_query_falls_back_to_every_stored_sentence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "corpus"
            corpus_path.mkdir()
            (corpus_path / "sentences.txt").write_text(
                "Cat.\nDog.\n",
                encoding="utf-8",
            )
            index_path = temporary_path / "autocomplete.sqlite3"
            build_index(corpus_path, index_path)

            candidates = list(iter_candidate_entries(index_path, "ct"))

        self.assertEqual(
            {candidate.original_sentence for candidate in candidates},
            {"Cat.", "Dog."},
        )

    def test_four_character_query_uses_one_edit_wildcard_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "corpus"
            corpus_path.mkdir()
            (corpus_path / "sentences.txt").write_text(
                "Cats are animals.\nCompletely unrelated.\n",
                encoding="utf-8",
            )
            index_path = temporary_path / "autocomplete.sqlite3"
            build_index(corpus_path, index_path)

            candidates = list(iter_candidate_entries(index_path, "cots"))

        self.assertIn("Cats are animals.", [candidate.original_sentence for candidate in candidates])

    def test_short_query_patterns_are_ranked_by_their_best_possible_score(self) -> None:
        groups = dict(ranked_one_edit_glob_groups("cots"))

        self.assertEqual(groups[6], ("cots?",))
        self.assertEqual(groups[4], ("cot?", "cot?s"))
        self.assertIn("co?s", groups[3])
        self.assertIn("c?ts", groups[2])

    def test_nine_character_query_survives_a_typo_in_the_left_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "corpus"
            corpus_path.mkdir()
            (corpus_path / "sentences.txt").write_text(
                "abcxefghi is a one-edit match.\nCompletely unrelated.\n",
                encoding="utf-8",
            )
            index_path = temporary_path / "autocomplete.sqlite3"
            build_index(corpus_path, index_path)

            candidates = list(iter_candidate_entries(index_path, "abcdefghi"))

        self.assertEqual(
            [candidate.original_sentence for candidate in candidates],
            ["abcxefghi is a one-edit match."],
        )

    def test_balanced_split_distributes_the_remainder(self) -> None:
        self.assertEqual(_split_balanced("abcdefghij", 3), ["abcd", "efg", "hij"])
        self.assertEqual(_split_balanced("abcdefghijk", 3), ["abcd", "efgh", "ijk"])

    def test_long_query_survives_a_typo_in_either_balanced_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "corpus"
            corpus_path.mkdir()
            (corpus_path / "sentences.txt").write_text(
                "abcXefghijkl has a typo in the left half.\n"
                "abcdefghijXl has a typo in the right half.\n"
                "Completely unrelated.\n",
                encoding="utf-8",
            )
            index_path = temporary_path / "autocomplete.sqlite3"
            build_index(corpus_path, index_path)

            candidates = list(iter_candidate_entries(index_path, "abcdefghijkl"))

        self.assertEqual(
            [candidate.original_sentence for candidate in candidates],
            [
                "abcXefghijkl has a typo in the left half.",
                "abcdefghijXl has a typo in the right half.",
            ],
        )


if __name__ == "__main__":
    unittest.main()

"""Index construction, stored schema and every candidate retrieval branch."""

import random
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from autocomplete.index import (
    _one_edit_glob_patterns,
    _split_balanced,
    build_index,
    find_exact_matches,
    find_exact_sentence_groups,
    get_sentence_locations,
    iter_candidate_entries,
    iter_candidate_sentence_groups,
    iter_glob_candidate_entries,
    iter_glob_sentence_groups,
    ranked_one_edit_glob_groups,
)
from autocomplete.normalization import normalize_text
from autocomplete.scoring import best_normalized_match_score
from tests.support import TemporaryCorpusTestCase


def _rows(index_path: Path, sql: str, parameters: tuple = ()) -> list[tuple]:
    with closing(sqlite3.connect(index_path)) as connection:
        return connection.execute(sql, parameters).fetchall()


class BuildIndexTests(TemporaryCorpusTestCase):
    def test_returns_the_number_of_stored_corpus_lines(self) -> None:
        index_path = self.build_index_from(
            {
                "a.txt": "Repeated sentence.\nRepeated sentence.\n",
                "b.txt": "Another sentence.\n",
            }
        )

        self.assertEqual(
            _rows(index_path, "SELECT COUNT(*) FROM sentence_locations")[0][0],
            3,
        )
        self.assertEqual(
            _rows(index_path, "SELECT COUNT(*) FROM sentence_groups")[0][0],
            2,
        )

    def test_stores_each_source_file_name_once(self) -> None:
        index_path = self.build_index_from(
            {
                "a.txt": "One.\nTwo.\nThree.\n",
                "nested/b.txt": "Four.\n",
            }
        )

        self.assertEqual(
            sorted(name for (name,) in _rows(index_path, "SELECT source_text FROM source_files")),
            ["a.txt", "nested/b.txt"],
        )

    def test_counts_duplicates_that_land_in_different_batches(self) -> None:
        lines = ["Repeated sentence."] * 7 + ["Unique sentence."]
        index_path = self.build_index_from_lines(lines, batch_size=2)

        counts = dict(
            _rows(
                index_path,
                "SELECT original_sentence, occurrence_count FROM sentence_groups",
            )
        )

        self.assertEqual(counts["Repeated sentence."], 7)
        self.assertEqual(counts["Unique sentence."], 1)
        self.assertEqual(
            _rows(index_path, "SELECT COUNT(*) FROM sentence_locations")[0][0],
            8,
        )

    def test_stores_the_same_data_for_every_batch_size(self) -> None:
        lines = [f"Sentence number {number}." for number in range(11)] + ["Sentence number 3."]
        expected = None

        for batch_size in (1, 2, 5, 11, 12, 5_000):
            with self.subTest(batch_size=batch_size):
                index_path = self.temporary_path / f"index-{batch_size}.sqlite3"
                build_index(
                    self.write_corpus({"a.txt": "\n".join(lines) + "\n"}),
                    index_path,
                    batch_size=batch_size,
                )
                stored = sorted(
                    _rows(
                        index_path,
                        "SELECT original_sentence, occurrence_count FROM sentence_groups",
                    )
                )

                if expected is None:
                    expected = stored
                self.assertEqual(stored, expected)

    def test_rejects_a_non_positive_batch_size(self) -> None:
        corpus_path = self.write_corpus({"a.txt": "One.\n"})

        for batch_size in (0, -1):
            with self.subTest(batch_size=batch_size):
                with self.assertRaises(ValueError):
                    build_index(corpus_path, self.temporary_path / "i.sqlite3", batch_size)

    def test_rebuilding_replaces_the_previous_corpus(self) -> None:
        index_path = self.build_index_from({"a.txt": "Original sentence.\n"})

        second_corpus = self.temporary_path / "corpus-two"
        second_corpus.mkdir()
        (second_corpus / "b.txt").write_text("Replacement sentence.\n", encoding="utf-8")
        build_index(second_corpus, index_path)

        self.assertEqual(
            [name for (name,) in _rows(index_path, "SELECT original_sentence FROM sentence_groups")],
            ["Replacement sentence."],
        )
        self.assertEqual(find_exact_matches(index_path, "original sentence"), [])

    def test_creates_the_expected_tables(self) -> None:
        index_path = self.build_index_from({"a.txt": "One.\n"})

        names = {
            name
            for (name,) in _rows(
                index_path,
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')",
            )
        }

        self.assertLessEqual(
            {
                "sentence_groups",
                "sentence_locations",
                "source_files",
                "sentence_search",
                "idx_sentence_locations_sentence_id",
            },
            names,
        )

    def test_an_empty_corpus_still_produces_a_searchable_index(self) -> None:
        index_path = self.build_index_from({"a.txt": "\n   \n"})

        self.assertEqual(find_exact_matches(index_path, "anything"), [])
        self.assertEqual(list(iter_candidate_entries(index_path, "anything")), [])


class FindExactMatchesTests(TemporaryCorpusTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.index_path = self.build_index_from(
            {
                "a.txt": "Python documentation is useful.\nUnrelated line.\n",
                "b.txt": "Python documentation is useful.\n",
            }
        )

    def test_normalizes_the_query_before_searching(self) -> None:
        matches = find_exact_matches(self.index_path, "  PYTHON,   Documentation!  ")

        self.assertEqual(len(matches), 2)
        self.assertEqual(
            [(match.source_text, match.offset) for match in matches],
            [("a.txt", 1), ("b.txt", 1)],
        )

    def test_returns_every_location_of_a_repeated_sentence(self) -> None:
        matches = find_exact_matches(self.index_path, "useful")

        self.assertEqual(
            {(match.source_text, match.offset) for match in matches},
            {("a.txt", 1), ("b.txt", 1)},
        )

    def test_applies_the_limit(self) -> None:
        self.assertEqual(len(find_exact_matches(self.index_path, "python", limit=1)), 1)

    def test_returns_nothing_for_a_non_positive_limit(self) -> None:
        self.assertEqual(find_exact_matches(self.index_path, "python", limit=0), [])
        self.assertEqual(find_exact_matches(self.index_path, "python", limit=-3), [])

    def test_returns_nothing_for_an_unsearchable_query(self) -> None:
        self.assertEqual(find_exact_matches(self.index_path, "   !!!   "), [])

    def test_finds_a_query_shorter_than_one_trigram(self) -> None:
        matches = find_exact_matches(self.index_path, "un")

        self.assertEqual(
            [match.original_sentence for match in matches],
            ["Unrelated line."],
        )

    def test_does_not_match_across_removed_punctuation_boundaries(self) -> None:
        self.assertEqual(find_exact_matches(self.index_path, "python2"), [])


class SentenceGroupQueryTests(TemporaryCorpusTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.index_path = self.build_index_from(
            {
                "a.txt": "Popular sentence.\nPopular sentence.\nRare sentence.\n",
                "b.txt": "Popular sentence.\n",
            }
        )

    def test_returns_one_row_per_unique_sentence_with_its_count(self) -> None:
        groups = find_exact_sentence_groups(self.index_path, "sentence")

        self.assertEqual(
            [(original, count) for _id, original, _normalized, count in groups],
            [("Popular sentence.", 3), ("Rare sentence.", 1)],
        )

    def test_orders_by_popularity_then_alphabetically(self) -> None:
        groups = find_exact_sentence_groups(self.index_path, "sentence", limit=1)

        self.assertEqual(groups[0][1], "Popular sentence.")

    def test_returns_nothing_for_a_non_positive_limit_or_empty_query(self) -> None:
        self.assertEqual(find_exact_sentence_groups(self.index_path, "sentence", limit=0), [])
        self.assertEqual(find_exact_sentence_groups(self.index_path, "!!!"), [])


class SentenceLocationTests(TemporaryCorpusTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.index_path = self.build_index_from(
            {
                "a.txt": "Repeated.\nOther.\nRepeated.\n",
                "b.txt": "Repeated.\n",
            }
        )
        self.sentence_id = _rows(
            self.index_path,
            "SELECT id FROM sentence_groups WHERE original_sentence = ?",
            ("Repeated.",),
        )[0][0]

    def test_lists_every_location_in_a_stable_order(self) -> None:
        locations = get_sentence_locations(self.index_path, self.sentence_id)

        self.assertEqual(locations, [("a.txt", 1), ("a.txt", 3), ("b.txt", 1)])

    def test_pages_through_the_locations(self) -> None:
        first_page = get_sentence_locations(self.index_path, self.sentence_id, limit=2)
        second_page = get_sentence_locations(
            self.index_path,
            self.sentence_id,
            limit=2,
            offset=2,
        )
        past_the_end = get_sentence_locations(
            self.index_path,
            self.sentence_id,
            limit=2,
            offset=99,
        )

        self.assertEqual(first_page, [("a.txt", 1), ("a.txt", 3)])
        self.assertEqual(second_page, [("b.txt", 1)])
        self.assertEqual(past_the_end, [])

    def test_returns_nothing_for_invalid_paging_arguments(self) -> None:
        self.assertEqual(get_sentence_locations(self.index_path, self.sentence_id, limit=0), [])
        self.assertEqual(get_sentence_locations(self.index_path, self.sentence_id, limit=-1), [])
        self.assertEqual(
            get_sentence_locations(self.index_path, self.sentence_id, offset=-1),
            [],
        )

    def test_returns_nothing_for_an_unknown_sentence(self) -> None:
        self.assertEqual(get_sentence_locations(self.index_path, 987_654), [])


class GlobPatternTests(unittest.TestCase):
    def test_describes_every_one_edit_form_of_a_query(self) -> None:
        self.assertEqual(
            _one_edit_glob_patterns("ab"),
            {"ab", "a", "b", "?b", "a?", "?ab", "a?b", "ab?"},
        )

    def test_always_contains_the_unedited_query(self) -> None:
        self.assertIn("hello", _one_edit_glob_patterns("hello"))

    def test_a_single_character_query_includes_the_empty_deletion(self) -> None:
        self.assertEqual(_one_edit_glob_patterns("a"), {"a", "", "?", "?a", "a?"})

    def test_ranked_groups_are_sorted_by_descending_best_score(self) -> None:
        groups = ranked_one_edit_glob_groups("hello")
        scores = [score for score, _patterns in groups]

        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len(scores), len(set(scores)))

    def test_ranked_groups_cover_every_edited_pattern_exactly_once(self) -> None:
        query = "abcd"
        ranked_patterns = [
            pattern for _score, patterns in ranked_one_edit_glob_groups(query) for pattern in patterns
        ]

        self.assertEqual(len(ranked_patterns), len(set(ranked_patterns)))
        self.assertEqual(
            set(ranked_patterns),
            _one_edit_glob_patterns(query) - {query},
        )

    def test_each_group_lists_its_patterns_in_a_stable_order(self) -> None:
        for _score, patterns in ranked_one_edit_glob_groups("abcd"):
            with self.subTest(patterns=patterns):
                self.assertEqual(list(patterns), sorted(patterns))

    def test_every_scoreable_sentence_matches_a_group_worth_at_least_its_score(
        self,
    ) -> None:
        """The bound the engine stops on: no group hides a better result."""

        generator = random.Random(90210)

        for _ in range(200):
            query = "".join(generator.choice("abc") for _ in range(generator.randint(1, 5)))
            sentence = "".join(
                generator.choice("abc") for _ in range(generator.randint(1, 10))
            )
            score = best_normalized_match_score(query, sentence)

            if score is None or query in sentence:
                continue

            reachable_scores = [
                maximum_score
                for maximum_score, patterns in ranked_one_edit_glob_groups(query)
                if any(_glob_contains(sentence, pattern) for pattern in patterns)
            ]

            with self.subTest(query=query, sentence=sentence):
                self.assertTrue(reachable_scores)
                self.assertGreaterEqual(max(reachable_scores), score)


def _glob_contains(text: str, pattern: str) -> bool:
    """Whether ``text`` contains ``pattern`` with ``?`` as any one character."""

    if not pattern:
        return True

    for start in range(len(text) - len(pattern) + 1):
        window = text[start : start + len(pattern)]
        if all(
            pattern_character in ("?", window_character)
            for pattern_character, window_character in zip(pattern, window)
        ):
            return True

    return False


class BalancedSplitTests(unittest.TestCase):
    def test_splits_evenly_when_the_length_divides(self) -> None:
        self.assertEqual(_split_balanced("abcdef", 2), ["abc", "def"])

    def test_gives_the_remainder_to_the_earlier_parts(self) -> None:
        self.assertEqual(_split_balanced("abcdefg", 2), ["abcd", "efg"])
        self.assertEqual(_split_balanced("abcdefg", 3), ["abc", "de", "fg"])

    def test_the_parts_always_rebuild_the_original_text(self) -> None:
        for text in ["", "a", "abcdefghij", "hello world"]:
            for part_count in range(1, 5):
                with self.subTest(text=text, part_count=part_count):
                    parts = _split_balanced(text, part_count)
                    self.assertEqual("".join(parts), text)
                    self.assertEqual(len(parts), part_count)


class CandidateRetrievalTests(TemporaryCorpusTestCase):
    SENTENCES = [
        "The quick brown fox jumps.",
        "Python documentation is useful.",
        "To be or not to be.",
        "Completely unrelated text.",
        "Another quick brown dog.",
    ]

    def setUp(self) -> None:
        super().setUp()
        self.index_path = self.build_index_from_lines(self.SENTENCES)

    def _candidate_sentences(self, query: str) -> set[str]:
        return {entry.original_sentence for entry in iter_candidate_entries(self.index_path, query)}

    def test_a_short_query_scans_every_stored_sentence(self) -> None:
        self.assertEqual(self._candidate_sentences("xyz"), set(self.SENTENCES))

    def test_a_five_character_query_uses_wildcard_patterns(self) -> None:
        self.assertIn("Python documentation is useful.", self._candidate_sentences("pythn"))
        self.assertNotIn("To be or not to be.", self._candidate_sentences("pythn"))

    def test_a_long_query_survives_a_typo_in_either_half(self) -> None:
        self.assertIn(
            "Python documentation is useful.",
            self._candidate_sentences("python docxmentation"),
        )
        self.assertIn(
            "Python documentation is useful.",
            self._candidate_sentences("pythxn documentation"),
        )

    def test_returns_nothing_for_an_unsearchable_query(self) -> None:
        self.assertEqual(list(iter_candidate_entries(self.index_path, "  !!  ")), [])
        self.assertEqual(list(iter_candidate_sentence_groups(self.index_path, "!!")), [])

    def test_sentence_group_retrieval_mirrors_entry_retrieval(self) -> None:
        for query in ["xyz", "pythn", "quik brown", "python documentation"]:
            with self.subTest(query=query):
                entry_sentences = self._candidate_sentences(query)
                group_sentences = {
                    original
                    for _id, original, _normalized, _count in iter_candidate_sentence_groups(
                        self.index_path, query
                    )
                }
                self.assertEqual(group_sentences, entry_sentences)

    def test_glob_retrieval_returns_nothing_without_patterns(self) -> None:
        self.assertEqual(list(iter_glob_candidate_entries(self.index_path, ())), [])
        self.assertEqual(list(iter_glob_sentence_groups(self.index_path, ())), [])

    def test_glob_retrieval_matches_a_wildcard_pattern(self) -> None:
        entries = list(iter_glob_candidate_entries(self.index_path, ("qu?ck",)))

        self.assertEqual(
            {entry.original_sentence for entry in entries},
            {"The quick brown fox jumps.", "Another quick brown dog."},
        )

    def test_candidate_retrieval_never_drops_a_scoreable_sentence(self) -> None:
        """Retrieval must be a superset of everything the scorer would accept."""

        generator = random.Random(4242)
        normalized_sentences = {
            sentence: normalize_text(sentence) for sentence in self.SENTENCES
        }

        for _ in range(120):
            source = normalize_text(generator.choice(self.SENTENCES))
            length = generator.randint(1, 12)
            start = generator.randrange(0, max(1, len(source) - length + 1))
            query = source[start : start + length]

            edit = generator.choice("sdi")
            position = generator.randrange(len(query))
            character = generator.choice("abcdefghijklmnopqrstuvwxyz ")
            if edit == "s":
                query = query[:position] + character + query[position + 1 :]
            elif edit == "d":
                query = query[:position] + query[position + 1 :]
            else:
                query = query[:position] + character + query[position:]

            if not query.strip():
                continue

            scoreable = {
                sentence
                for sentence, normalized in normalized_sentences.items()
                if best_normalized_match_score(normalize_text(query), normalized) is not None
            }

            with self.subTest(query=query):
                self.assertLessEqual(scoreable, self._candidate_sentences(query))


if __name__ == "__main__":
    unittest.main()

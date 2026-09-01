"""Compare the indexed engine with an exhaustive scan of the same corpus.

The engine reaches its five results through wildcard pattern groups, anchored
candidate retrieval and branch-and-bound early exits. These tests re-derive the
same answers by scoring every corpus line, so any shortcut that silently loses
a better result fails here.
"""

import random
import sqlite3
import unittest
from pathlib import Path

from autocomplete.corpus import iter_corpus_entries
from autocomplete.engine import (
    _group_ranking_key,
    _ranking_key,
    get_best_k_completions,
    get_best_unique_completions,
)
from autocomplete.index import build_index
from autocomplete.models import AutoCompleteData, GroupedAutoCompleteData
from autocomplete.normalization import normalize_text
from autocomplete.scoring import best_normalized_match_score
from tests.support import TemporaryCorpusTestCase


_WORDS = [
    "to", "be", "or", "not", "that", "is", "the", "question", "python",
    "code", "data", "index", "fast", "search", "alpha", "zulu", "corpus",
]


def _random_corpus(generator: random.Random, line_count: int) -> list[str]:
    lines = []
    for _ in range(line_count):
        words = [generator.choice(_WORDS) for _ in range(generator.randint(2, 7))]
        lines.append(" ".join(words).capitalize() + ".")
    return lines


def _random_queries(
    generator: random.Random,
    lines: list[str],
    per_length: int = 12,
) -> list[str]:
    """Take substrings of corpus lines and damage most of them by one edit."""

    queries = []

    for length in range(1, 13):
        for _ in range(per_length):
            normalized_line = normalize_text(generator.choice(lines))
            if len(normalized_line) < length:
                continue

            start = generator.randrange(0, len(normalized_line) - length + 1)
            query = normalized_line[start : start + length]

            if generator.random() < 0.6:
                position = generator.randrange(len(query))
                character = generator.choice("abcdefghijklmnopqrstuvwxyz ")
                edit = generator.choice("sdi")
                if edit == "s":
                    query = query[:position] + character + query[position + 1 :]
                elif edit == "d":
                    query = query[:position] + query[position + 1 :]
                else:
                    query = query[:position] + character + query[position:]

            if query.strip():
                queries.append(query)

    return queries


class ExhaustiveScanAgreementTests(TemporaryCorpusTestCase):
    def _scan_every_line(self, corpus_path: Path, query: str) -> list[AutoCompleteData]:
        normalized_query = normalize_text(query)
        scored = []

        for entry in iter_corpus_entries(corpus_path):
            score = best_normalized_match_score(normalized_query, entry.normalized_sentence)
            if score is not None:
                scored.append(
                    AutoCompleteData(
                        completed_sentence=entry.original_sentence,
                        source_text=entry.source_text,
                        offset=entry.offset,
                        score=score,
                    )
                )

        return sorted(scored, key=_ranking_key)[:5]

    def _scan_every_group(
        self,
        index_path: Path,
        query: str,
    ) -> list[GroupedAutoCompleteData]:
        normalized_query = normalize_text(query)

        with sqlite3.connect(index_path) as connection:
            groups = connection.execute(
                """
                SELECT id, original_sentence, normalized_sentence, occurrence_count
                FROM sentence_groups
                """
            ).fetchall()

        scored = []
        for sentence_id, original, normalized, occurrence_count in groups:
            score = best_normalized_match_score(normalized_query, normalized)
            if score is not None:
                scored.append(
                    GroupedAutoCompleteData(
                        sentence_id=sentence_id,
                        completed_sentence=original,
                        score=score,
                        occurrence_count=occurrence_count,
                    )
                )

        return sorted(scored, key=_group_ranking_key)[:5]

    def test_ranked_results_match_a_full_corpus_scan(self) -> None:
        generator = random.Random(20240502)
        lines = _random_corpus(generator, 120)
        corpus_path = self.write_corpus({"a.txt": "\n".join(lines) + "\n"})
        index_path = self.temporary_path / "autocomplete.sqlite3"
        build_index(corpus_path, index_path)

        queries = _random_queries(generator, lines)
        self.assertGreater(len(queries), 100)

        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(
                    get_best_k_completions(query, index_path),
                    self._scan_every_line(corpus_path, query),
                )

    def test_unique_results_match_a_full_sentence_group_scan(self) -> None:
        generator = random.Random(20240503)
        lines = _random_corpus(generator, 80)
        lines = lines + lines[:25]
        corpus_path = self.write_corpus({"a.txt": "\n".join(lines) + "\n"})
        index_path = self.temporary_path / "autocomplete.sqlite3"
        build_index(corpus_path, index_path)

        queries = _random_queries(generator, lines)
        self.assertGreater(len(queries), 100)

        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(
                    get_best_unique_completions(query, index_path),
                    self._scan_every_group(index_path, query),
                )

    def test_results_match_a_full_scan_across_several_files(self) -> None:
        generator = random.Random(20240504)
        lines = _random_corpus(generator, 60)
        corpus_path = self.write_corpus(
            {
                "one.txt": "\n".join(lines[:20]) + "\n",
                "nested/two.txt": "\n".join(lines[20:40]) + "\n",
                "nested/deeper/three.txt": "\n".join(lines[40:]) + "\n",
            }
        )
        index_path = self.temporary_path / "autocomplete.sqlite3"
        build_index(corpus_path, index_path)

        for query in _random_queries(generator, lines, per_length=4):
            with self.subTest(query=query):
                self.assertEqual(
                    get_best_k_completions(query, index_path),
                    self._scan_every_line(corpus_path, query),
                )


if __name__ == "__main__":
    unittest.main()

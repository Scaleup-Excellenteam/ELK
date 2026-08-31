"""Build and query the on-disk corpus index."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterator

from .corpus import CorpusEntry, iter_corpus_entries
from .normalization import normalize_text
from .scoring import insertion_or_deletion_penalty, substitution_penalty


_CREATE_SENTENCES_TABLE = """
CREATE TABLE sentences (
    id INTEGER PRIMARY KEY,
    original_sentence TEXT NOT NULL,
    normalized_sentence TEXT NOT NULL,
    source_text TEXT NOT NULL,
    offset INTEGER NOT NULL
)
"""

_CREATE_SEARCH_INDEX = """
CREATE VIRTUAL TABLE sentence_search USING fts5(
    normalized_sentence,
    content='sentences',
    content_rowid='id',
    tokenize='trigram'
)
"""

_INSERT_SENTENCE = """
INSERT INTO sentences (
    original_sentence,
    normalized_sentence,
    source_text,
    offset
) VALUES (?, ?, ?, ?)
"""


def build_index(
    source_root: str | Path,
    index_path: str | Path,
    batch_size: int = 5_000,
) -> int:
    """Build a fresh SQLite index and return the number of stored sentences."""

    if batch_size <= 0:
        raise ValueError("Batch size must be greater than zero")

    stored_sentences = 0
    pending_rows: list[tuple[str, str, str, int]] = []

    with closing(sqlite3.connect(index_path)) as connection:
        with connection:
            connection.execute("DROP TABLE IF EXISTS bigram_search")
            connection.execute("DROP TABLE IF EXISTS sentence_search")
            connection.execute("DROP TABLE IF EXISTS sentences")
            connection.execute(_CREATE_SENTENCES_TABLE)
            connection.execute(_CREATE_SEARCH_INDEX)

            for entry in iter_corpus_entries(source_root):
                pending_rows.append(
                    (
                        entry.original_sentence,
                        entry.normalized_sentence,
                        entry.source_text,
                        entry.offset,
                    )
                )

                if len(pending_rows) == batch_size:
                    connection.executemany(_INSERT_SENTENCE, pending_rows)
                    stored_sentences += len(pending_rows)
                    pending_rows.clear()

            if pending_rows:
                connection.executemany(_INSERT_SENTENCE, pending_rows)
                stored_sentences += len(pending_rows)

            connection.execute(
                "INSERT INTO sentence_search(sentence_search) VALUES ('rebuild')"
            )

    return stored_sentences


def find_exact_matches(
    index_path: str | Path,
    query: str,
    limit: int = 5,
) -> list[CorpusEntry]:
    """Return corpus lines containing the normalized query as a substring."""

    normalized_query = normalize_text(query)
    if not normalized_query or limit <= 0:
        return []

    with closing(sqlite3.connect(index_path)) as connection:
        rows = connection.execute(
            """
            SELECT
                sentences.original_sentence,
                sentences.normalized_sentence,
                sentences.source_text,
                sentences.offset
            FROM sentence_search
            JOIN sentences ON sentences.id = sentence_search.rowid
            WHERE sentence_search.normalized_sentence LIKE ?
            ORDER BY
                sentences.original_sentence COLLATE NOCASE,
                sentences.original_sentence,
                sentences.source_text,
                sentences.offset
            LIMIT ?
            """,
            (f"%{normalized_query}%", limit),
        ).fetchall()

    return [CorpusEntry(*row) for row in rows]


def iter_candidate_entries(
    index_path: str | Path,
    query: str,
) -> Iterator[CorpusEntry]:
    """Yield every corpus line that could match with at most one edit.

    Queries of six or more characters are split into two balanced anchors and
    require one match. With at most one edit, at least one half must remain
    unchanged. Queries of four or five characters use one-edit wildcard
    patterns. Queries of up to three characters retain a correctness-first
    full-table fallback.
    """

    normalized_query = normalize_text(query)
    if not normalized_query:
        return

    with closing(sqlite3.connect(index_path)) as connection:
        if len(normalized_query) <= 3:
            rows = connection.execute(
                """
                SELECT
                    original_sentence,
                    normalized_sentence,
                    source_text,
                    offset
                FROM sentences
                """
            )
        elif len(normalized_query) <= 5:
            patterns = _one_edit_glob_patterns(normalized_query)
            conditions = " OR ".join(
                "sentence_search.normalized_sentence GLOB ?" for _ in patterns
            )
            rows = connection.execute(
                f"""
                SELECT
                    sentences.original_sentence,
                    sentences.normalized_sentence,
                    sentences.source_text,
                    sentences.offset
                FROM sentence_search
                JOIN sentences ON sentences.id = sentence_search.rowid
                WHERE {conditions}
                """,
                tuple(f"*{pattern}*" for pattern in patterns),
            )
        else:
            left_anchor, right_anchor = _split_balanced(normalized_query, 2)

            rows = connection.execute(
                """
                WITH candidate_ids AS (
                    SELECT rowid
                    FROM sentence_search
                    WHERE normalized_sentence LIKE ?

                    UNION

                    SELECT rowid
                    FROM sentence_search
                    WHERE normalized_sentence LIKE ?
                )
                SELECT
                    sentences.original_sentence,
                    sentences.normalized_sentence,
                    sentences.source_text,
                    sentences.offset
                FROM candidate_ids
                JOIN sentences ON sentences.id = candidate_ids.rowid
                """,
                (f"%{left_anchor}%", f"%{right_anchor}%"),
            )

        for row in rows:
            yield CorpusEntry(*row)


def iter_glob_candidate_entries(
    index_path: str | Path,
    patterns: tuple[str, ...],
) -> Iterator[CorpusEntry]:
    """Yield corpus lines matching one of a group of short-query patterns."""

    if not patterns:
        return

    conditions = " OR ".join(
        "sentence_search.normalized_sentence GLOB ?" for _ in patterns
    )

    with closing(sqlite3.connect(index_path)) as connection:
        rows = connection.execute(
            f"""
            SELECT
                sentences.original_sentence,
                sentences.normalized_sentence,
                sentences.source_text,
                sentences.offset
            FROM sentence_search
            JOIN sentences ON sentences.id = sentence_search.rowid
            WHERE {conditions}
            """,
            tuple(f"*{pattern}*" for pattern in patterns),
        )

        for row in rows:
            yield CorpusEntry(*row)


def ranked_one_edit_glob_groups(query: str) -> list[tuple[int, tuple[str, ...]]]:
    """Group one-edit patterns by their best possible assignment score."""

    pattern_scores: dict[str, int] = {}
    query_length = len(query)

    def keep_best_score(pattern: str, score: int) -> None:
        pattern_scores[pattern] = max(score, pattern_scores.get(pattern, score))

    for zero_based_position in range(query_length):
        position = zero_based_position + 1

        substitution_pattern = (
            query[:zero_based_position] + "?" + query[zero_based_position + 1 :]
        )
        substitution_score = 2 * (query_length - 1) - substitution_penalty(position)
        keep_best_score(substitution_pattern, substitution_score)

        deletion_pattern = query[:zero_based_position] + query[zero_based_position + 1 :]
        deletion_score = 2 * (query_length - 1) - insertion_or_deletion_penalty(
            position
        )
        keep_best_score(deletion_pattern, deletion_score)

    for insertion_index in range(query_length + 1):
        position = insertion_index + 1
        insertion_pattern = query[:insertion_index] + "?" + query[insertion_index:]
        insertion_score = 2 * query_length - insertion_or_deletion_penalty(position)
        keep_best_score(insertion_pattern, insertion_score)

    grouped_patterns: dict[int, list[str]] = {}
    for pattern, score in pattern_scores.items():
        grouped_patterns.setdefault(score, []).append(pattern)

    return [
        (score, tuple(sorted(patterns)))
        for score, patterns in sorted(grouped_patterns.items(), reverse=True)
    ]


def _one_edit_glob_patterns(query: str) -> set[str]:
    """Describe all one-edit forms with ``?`` as one unknown character."""

    patterns = {query}

    for position in range(len(query)):
        patterns.add(query[:position] + query[position + 1 :])
        patterns.add(query[:position] + "?" + query[position + 1 :])

    for position in range(len(query) + 1):
        patterns.add(query[:position] + "?" + query[position:])

    return patterns


def _split_balanced(text: str, part_count: int) -> list[str]:
    """Split text into near-equal consecutive parts using ``divmod``."""

    base_size, remainder = divmod(len(text), part_count)
    parts = []
    start = 0

    for part_number in range(part_count):
        part_size = base_size + (1 if part_number < remainder else 0)
        end = start + part_size
        parts.append(text[start:end])
        start = end

    return parts

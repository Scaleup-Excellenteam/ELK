"""Build and query the on-disk corpus index."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterator

from .corpus import CorpusEntry, iter_corpus_entries
from .normalization import normalize_text
from .scoring import insertion_or_deletion_penalty, substitution_penalty


_CREATE_SENTENCE_GROUPS_TABLE = """
CREATE TABLE sentence_groups (
    id INTEGER PRIMARY KEY,
    original_sentence TEXT NOT NULL UNIQUE,
    normalized_sentence TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL
)
"""

_CREATE_SENTENCE_LOCATIONS_TABLE = """
CREATE TABLE sentence_locations (
    id INTEGER PRIMARY KEY,
    sentence_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    offset INTEGER NOT NULL,
    FOREIGN KEY (sentence_id) REFERENCES sentence_groups(id),
    FOREIGN KEY (source_id) REFERENCES source_files(id)
)
"""

_CREATE_SOURCE_FILES_TABLE = """
CREATE TABLE source_files (
    id INTEGER PRIMARY KEY,
    source_text TEXT NOT NULL UNIQUE
)
"""

_CREATE_LOCATIONS_INDEX = """
CREATE INDEX idx_sentence_locations_sentence_id
ON sentence_locations(sentence_id, source_id, offset)
"""

_CREATE_SEARCH_INDEX = """
CREATE VIRTUAL TABLE sentence_search USING fts5(
    normalized_sentence,
    content='sentence_groups',
    content_rowid='id',
    tokenize='trigram'
)
"""

_UPSERT_SENTENCE_GROUP = """
INSERT INTO sentence_groups (
    original_sentence,
    normalized_sentence,
    occurrence_count
) VALUES (?, ?, ?)
ON CONFLICT(original_sentence) DO UPDATE SET
    occurrence_count = occurrence_count + excluded.occurrence_count
"""

_INSERT_SENTENCE_LOCATION = """
INSERT INTO sentence_locations (
    sentence_id,
    source_id,
    offset
) VALUES (?, ?, ?)
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
    pending_entries: list[CorpusEntry] = []

    def store_batch(connection: sqlite3.Connection) -> None:
        nonlocal stored_sentences

        grouped_entries: dict[str, tuple[str, int]] = {}
        for entry in pending_entries:
            normalized_sentence, count = grouped_entries.get(
                entry.original_sentence,
                (entry.normalized_sentence, 0),
            )
            grouped_entries[entry.original_sentence] = (normalized_sentence, count + 1)

        connection.executemany(
            _UPSERT_SENTENCE_GROUP,
            (
                (original_sentence, normalized_sentence, count)
                for original_sentence, (normalized_sentence, count) in grouped_entries.items()
            ),
        )

        sentence_ids: dict[str, int] = {}
        original_sentences = list(grouped_entries)
        for chunk_start in range(0, len(original_sentences), 500):
            chunk = original_sentences[chunk_start : chunk_start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT id, original_sentence
                FROM sentence_groups
                WHERE original_sentence IN ({placeholders})
                """,
                chunk,
            )
            sentence_ids.update(
                (original_sentence, sentence_id)
                for sentence_id, original_sentence in rows
            )

        source_names = list(dict.fromkeys(entry.source_text for entry in pending_entries))
        connection.executemany(
            "INSERT OR IGNORE INTO source_files(source_text) VALUES (?)",
            ((source_name,) for source_name in source_names),
        )
        source_ids: dict[str, int] = {}
        for chunk_start in range(0, len(source_names), 500):
            chunk = source_names[chunk_start : chunk_start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            rows = connection.execute(
                f"SELECT id, source_text FROM source_files WHERE source_text IN ({placeholders})",
                chunk,
            )
            source_ids.update(
                (source_text, source_id) for source_id, source_text in rows
            )

        connection.executemany(
            _INSERT_SENTENCE_LOCATION,
            (
                (
                    sentence_ids[entry.original_sentence],
                    source_ids[entry.source_text],
                    entry.offset,
                )
                for entry in pending_entries
            ),
        )
        stored_sentences += len(pending_entries)
        pending_entries.clear()

    with closing(sqlite3.connect(index_path)) as connection:
        with connection:
            connection.execute("DROP TABLE IF EXISTS bigram_search")
            connection.execute("DROP TABLE IF EXISTS sentence_search")
            connection.execute("DROP TABLE IF EXISTS sentence_locations")
            connection.execute("DROP TABLE IF EXISTS sentence_groups")
            connection.execute("DROP TABLE IF EXISTS source_files")
            connection.execute("DROP TABLE IF EXISTS sentences")
            connection.execute(_CREATE_SENTENCE_GROUPS_TABLE)
            connection.execute(_CREATE_SOURCE_FILES_TABLE)
            connection.execute(_CREATE_SENTENCE_LOCATIONS_TABLE)
            connection.execute(_CREATE_LOCATIONS_INDEX)
            connection.execute(_CREATE_SEARCH_INDEX)

            for entry in iter_corpus_entries(source_root):
                pending_entries.append(entry)

                if len(pending_entries) == batch_size:
                    store_batch(connection)

            if pending_entries:
                store_batch(connection)

            connection.execute(
                "INSERT INTO sentence_search(sentence_search) VALUES ('rebuild')"
            )
            connection.execute("PRAGMA optimize")

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
                sentence_groups.original_sentence,
                sentence_groups.normalized_sentence,
                source_files.source_text,
                sentence_locations.offset
            FROM sentence_search
            JOIN sentence_groups ON sentence_groups.id = sentence_search.rowid
            JOIN sentence_locations
                ON sentence_locations.sentence_id = sentence_groups.id
            JOIN source_files ON source_files.id = sentence_locations.source_id
            WHERE sentence_search.normalized_sentence LIKE ?
            ORDER BY
                sentence_groups.original_sentence COLLATE NOCASE,
                sentence_groups.original_sentence,
                source_files.source_text,
                sentence_locations.offset
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
                    sentence_groups.original_sentence,
                    sentence_groups.normalized_sentence,
                    source_files.source_text,
                    sentence_locations.offset
                FROM sentence_groups
                JOIN sentence_locations
                    ON sentence_locations.sentence_id = sentence_groups.id
                JOIN source_files ON source_files.id = sentence_locations.source_id
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
                    sentence_groups.original_sentence,
                    sentence_groups.normalized_sentence,
                    source_files.source_text,
                    sentence_locations.offset
                FROM sentence_search
                JOIN sentence_groups ON sentence_groups.id = sentence_search.rowid
                JOIN sentence_locations
                    ON sentence_locations.sentence_id = sentence_groups.id
                JOIN source_files ON source_files.id = sentence_locations.source_id
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
                    sentence_groups.original_sentence,
                    sentence_groups.normalized_sentence,
                    source_files.source_text,
                    sentence_locations.offset
                FROM candidate_ids
                JOIN sentence_groups ON sentence_groups.id = candidate_ids.rowid
                JOIN sentence_locations
                    ON sentence_locations.sentence_id = sentence_groups.id
                JOIN source_files ON source_files.id = sentence_locations.source_id
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
                sentence_groups.original_sentence,
                sentence_groups.normalized_sentence,
                source_files.source_text,
                sentence_locations.offset
            FROM sentence_search
            JOIN sentence_groups ON sentence_groups.id = sentence_search.rowid
            JOIN sentence_locations
                ON sentence_locations.sentence_id = sentence_groups.id
            JOIN source_files ON source_files.id = sentence_locations.source_id
            WHERE {conditions}
            """,
            tuple(f"*{pattern}*" for pattern in patterns),
        )

        for row in rows:
            yield CorpusEntry(*row)


def find_exact_sentence_groups(
    index_path: str | Path,
    query: str,
    limit: int = 5,
) -> list[tuple[int, str, str, int]]:
    """Return unique sentence groups containing the normalized query."""

    normalized_query = normalize_text(query)
    if not normalized_query or limit <= 0:
        return []

    with closing(sqlite3.connect(index_path)) as connection:
        return connection.execute(
            """
            SELECT
                sentence_groups.id,
                sentence_groups.original_sentence,
                sentence_groups.normalized_sentence,
                sentence_groups.occurrence_count
            FROM sentence_search
            JOIN sentence_groups ON sentence_groups.id = sentence_search.rowid
            WHERE sentence_search.normalized_sentence LIKE ?
            ORDER BY
                sentence_groups.occurrence_count DESC,
                sentence_groups.original_sentence COLLATE NOCASE,
                sentence_groups.original_sentence
            LIMIT ?
            """,
            (f"%{normalized_query}%", limit),
        ).fetchall()


def iter_glob_sentence_groups(
    index_path: str | Path,
    patterns: tuple[str, ...],
) -> Iterator[tuple[int, str, str, int]]:
    """Yield unique sentence groups matching short-query wildcard patterns."""

    if not patterns:
        return

    conditions = " OR ".join(
        "sentence_search.normalized_sentence GLOB ?" for _ in patterns
    )
    with closing(sqlite3.connect(index_path)) as connection:
        rows = connection.execute(
            f"""
            SELECT
                sentence_groups.id,
                sentence_groups.original_sentence,
                sentence_groups.normalized_sentence,
                sentence_groups.occurrence_count
            FROM sentence_search
            JOIN sentence_groups ON sentence_groups.id = sentence_search.rowid
            WHERE {conditions}
            """,
            tuple(f"*{pattern}*" for pattern in patterns),
        )
        yield from rows


def iter_candidate_sentence_groups(
    index_path: str | Path,
    query: str,
) -> Iterator[tuple[int, str, str, int]]:
    """Yield unique sentence groups that may match a long query by one edit."""

    normalized_query = normalize_text(query)
    if not normalized_query:
        return

    with closing(sqlite3.connect(index_path)) as connection:
        if len(normalized_query) <= 3:
            rows = connection.execute(
                """
                SELECT id, original_sentence, normalized_sentence, occurrence_count
                FROM sentence_groups
                """
            )
        elif len(normalized_query) <= 5:
            patterns = tuple(_one_edit_glob_patterns(normalized_query))
            conditions = " OR ".join(
                "sentence_search.normalized_sentence GLOB ?" for _ in patterns
            )
            rows = connection.execute(
                f"""
                SELECT
                    sentence_groups.id,
                    sentence_groups.original_sentence,
                    sentence_groups.normalized_sentence,
                    sentence_groups.occurrence_count
                FROM sentence_search
                JOIN sentence_groups ON sentence_groups.id = sentence_search.rowid
                WHERE {conditions}
                """,
                tuple(f"*{pattern}*" for pattern in patterns),
            )
        else:
            left_anchor, right_anchor = _split_balanced(normalized_query, 2)
            rows = connection.execute(
                """
                WITH candidate_ids AS (
                    SELECT rowid FROM sentence_search WHERE normalized_sentence LIKE ?
                    UNION
                    SELECT rowid FROM sentence_search WHERE normalized_sentence LIKE ?
                )
                SELECT
                    sentence_groups.id,
                    sentence_groups.original_sentence,
                    sentence_groups.normalized_sentence,
                    sentence_groups.occurrence_count
                FROM candidate_ids
                JOIN sentence_groups ON sentence_groups.id = candidate_ids.rowid
                """,
                (f"%{left_anchor}%", f"%{right_anchor}%"),
            )
        yield from rows


def get_index_stats(index_path: str | Path) -> dict[str, int]:
    """Return row counts for the three core index tables."""

    with closing(sqlite3.connect(index_path)) as connection:
        sentence_count = connection.execute(
            "SELECT COUNT(*) FROM sentence_groups"
        ).fetchone()[0]
        source_count = connection.execute(
            "SELECT COUNT(*) FROM source_files"
        ).fetchone()[0]
        location_count = connection.execute(
            "SELECT COUNT(*) FROM sentence_locations"
        ).fetchone()[0]

    return {
        "sentence_count": sentence_count,
        "source_count": source_count,
        "location_count": location_count,
    }


def get_sentence_locations(
    index_path: str | Path,
    sentence_id: int,
    limit: int = 25,
    offset: int = 0,
) -> list[tuple[str, int]]:
    """Return one page of locations for a unique sentence."""

    if limit <= 0 or offset < 0:
        return []

    with closing(sqlite3.connect(index_path)) as connection:
        return connection.execute(
            """
            SELECT source_files.source_text, sentence_locations.offset
            FROM sentence_locations
            JOIN source_files ON source_files.id = sentence_locations.source_id
            WHERE sentence_id = ?
            ORDER BY sentence_locations.source_id, sentence_locations.offset
            LIMIT ? OFFSET ?
            """,
            (sentence_id, limit, offset),
        ).fetchall()


def get_sentence_group(
    index_path: str | Path,
    sentence_id: int,
) -> tuple[str, int] | None:
    """Return the canonical sentence and occurrence count for one group id."""

    with closing(sqlite3.connect(index_path)) as connection:
        row = connection.execute(
            """
            SELECT original_sentence, occurrence_count
            FROM sentence_groups
            WHERE id = ?
            """,
            (sentence_id,),
        ).fetchone()

    return row


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

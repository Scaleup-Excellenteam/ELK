"""Read corpus files and turn their lines into searchable records."""

from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path
from typing import Iterable, Iterator
from unicodedata import category
from zipfile import ZipFile, is_zipfile

from .normalization import normalize_text


@dataclass
class CorpusEntry:
    """A single non-empty corpus line in original and searchable forms."""

    original_sentence: str
    normalized_sentence: str
    source_text: str
    offset: int


def _strip_edge_noise(text: str) -> str:
    """Remove whitespace and invisible control characters from line edges."""

    start = 0
    end = len(text)
    while start < end and (text[start].isspace() or category(text[start]) == "Cc"):
        start += 1
    while end > start and (text[end - 1].isspace() or category(text[end - 1]) == "Cc"):
        end -= 1
    return text[start:end]


def _entries_from_lines(lines: Iterable[str], source_text: str) -> Iterator[CorpusEntry]:
    """Turn one text source into normalized corpus entries."""

    for line_number, line in enumerate(lines, start=1):
        # PDF-to-text corpora may leave page-break control characters
        # at the edges of otherwise valid sentences.
        original_sentence = _strip_edge_noise(line)
        normalized_sentence = normalize_text(original_sentence)

        if not normalized_sentence:
            continue

        yield CorpusEntry(
            original_sentence=original_sentence,
            normalized_sentence=normalized_sentence,
            source_text=source_text,
            offset=line_number,
        )


def iter_corpus_entries(source_root: str | Path) -> Iterator[CorpusEntry]:
    """Yield records from ``.txt`` files in a directory or ZIP archive."""

    root = Path(source_root)
    if root.is_dir():
        for text_file in sorted(root.rglob("*.txt")):
            relative_source = text_file.relative_to(root).as_posix()
            with text_file.open(encoding="utf-8") as lines:
                yield from _entries_from_lines(lines, relative_source)
        return

    if root.is_file() and is_zipfile(root):
        with ZipFile(root) as archive:
            text_members = sorted(
                (
                    member
                    for member in archive.infolist()
                    if not member.is_dir()
                    and Path(member.filename).suffix.casefold() == ".txt"
                ),
                key=lambda member: member.filename,
            )
            for member in text_members:
                source_text = member.filename.replace("\\", "/")
                if source_text.casefold().startswith("archive/"):
                    source_text = source_text.split("/", 1)[1]
                with archive.open(member) as binary_lines:
                    with TextIOWrapper(binary_lines, encoding="utf-8") as lines:
                        yield from _entries_from_lines(lines, source_text)
        return

    raise ValueError(
        f"Corpus source must be an existing directory or ZIP archive: {root}"
    )

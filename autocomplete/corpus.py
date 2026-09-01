"""Read corpus files and turn their lines into searchable records."""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from unicodedata import category

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


def iter_corpus_entries(source_root: str | Path) -> Iterator[CorpusEntry]:
    """Yield searchable records from every ``.txt`` file under a directory."""

    root = Path(source_root)
    if not root.is_dir():
        raise ValueError(f"Corpus directory does not exist: {root}")

    for text_file in sorted(root.rglob("*.txt")):
        relative_source = text_file.relative_to(root).as_posix()

        with text_file.open(encoding="utf-8") as lines:
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
                    source_text=relative_source,
                    offset=line_number,
                )

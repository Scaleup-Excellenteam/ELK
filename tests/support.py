"""Shared helpers for building throwaway corpora and indexes in tests."""

import tempfile
import unittest
from pathlib import Path

from autocomplete.index import build_index


class TemporaryCorpusTestCase(unittest.TestCase):
    """Base class that gives every test its own temporary directory."""

    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary_path = Path(temporary_directory.name)

    def write_corpus(self, files: dict[str, str]) -> Path:
        """Write ``{relative name: file text}`` under a fresh corpus root."""

        corpus_path = self.temporary_path / "corpus"
        corpus_path.mkdir(exist_ok=True)

        for relative_name, text in files.items():
            file_path = corpus_path / relative_name
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(text, encoding="utf-8")

        return corpus_path

    def build_index_from(self, files: dict[str, str], **build_options) -> Path:
        """Build an index over written corpus files and return its path."""

        corpus_path = self.write_corpus(files)
        index_path = self.temporary_path / "autocomplete.sqlite3"
        build_index(corpus_path, index_path, **build_options)
        return index_path

    def build_index_from_lines(
        self,
        lines: list[str],
        file_name: str = "sentences.txt",
        **build_options,
    ) -> Path:
        """Build an index over one file holding ``lines``."""

        return self.build_index_from(
            {file_name: "\n".join(lines) + "\n"},
            **build_options,
        )

    def missing_index_path(self) -> Path:
        """Return a path inside the temporary directory with no index on it."""

        return self.temporary_path / "not-built.sqlite3"

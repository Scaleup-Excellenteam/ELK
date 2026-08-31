"""Data objects returned by the autocomplete engine."""

from dataclasses import dataclass


@dataclass
class AutoCompleteData:
    """One autocomplete suggestion and the place it came from."""

    completed_sentence: str
    source_text: str
    offset: int
    score: int


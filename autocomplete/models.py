"""Data objects returned by the autocomplete engine."""

from dataclasses import dataclass


@dataclass
class AutoCompleteData:
    """One autocomplete suggestion and the place it came from."""

    completed_sentence: str
    source_text: str
    offset: int
    score: int


@dataclass
class GroupedAutoCompleteData:
    """One unique suggestion together with its number of corpus locations."""

    sentence_id: int
    completed_sentence: str
    score: int
    occurrence_count: int

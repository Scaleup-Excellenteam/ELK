"""Rules for converting user input and corpus lines to a searchable form."""

import string


_PUNCTUATION_TRANSLATION = str.maketrans("", "", string.punctuation)
_SUPPORTED_NORMALIZED_CHARACTERS = frozenset(
    string.ascii_lowercase + string.digits + " "
)


def normalize_text(text: str) -> str:
    """Normalize text according to the assignment's comparison rules.

    The original text is not modified. The returned copy is lower-case,
    contains no ASCII punctuation, and has exactly one space between words.
    """

    without_punctuation = text.lower().translate(_PUNCTUATION_TRANSLATION)
    return " ".join(without_punctuation.split())


def is_supported_normalized_query(text: str) -> bool:
    """Return whether normalized input belongs to the indexed English domain."""

    return all(character in _SUPPORTED_NORMALIZED_CHARACTERS for character in text)

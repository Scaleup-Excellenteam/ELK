"""Rules for converting user input and corpus lines to a searchable form."""

import string


_PUNCTUATION_TRANSLATION = str.maketrans("", "", string.punctuation)


def normalize_text(text: str) -> str:
    """Normalize text according to the assignment's comparison rules.

    The original text is not modified. The returned copy is lower-case,
    contains no ASCII punctuation, and has exactly one space between words.
    """

    without_punctuation = text.lower().translate(_PUNCTUATION_TRANSLATION)
    return " ".join(without_punctuation.split())


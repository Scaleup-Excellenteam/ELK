"""Match a query inside a sentence and calculate the assignment score."""

from .normalization import normalize_text


def substitution_penalty(position: int) -> int:
    return max(1, 6 - position)


def insertion_or_deletion_penalty(position: int) -> int:
    return max(2, 12 - 2 * position)


def _single_substitution_position(left: str, right: str) -> int | None:
    """Return the 1-based mismatch position when there is exactly one."""

    mismatch_position = None

    for position, (left_character, right_character) in enumerate(
        zip(left, right),
        start=1,
    ):
        if left_character != right_character:
            if mismatch_position is not None:
                return None
            mismatch_position = position

    return mismatch_position


def _removed_character_position(longer: str, shorter: str) -> int | None:
    """Return where one character can be removed from ``longer``."""

    if len(longer) != len(shorter) + 1:
        return None

    position = 0
    while position < len(shorter) and longer[position] == shorter[position]:
        position += 1

    if longer[position + 1 :] == shorter[position:]:
        return position + 1

    return None


def _windows(text: str, size: int):
    for start in range(len(text) - size + 1):
        yield text[start : start + size]


def best_normalized_match_score(
    normalized_query: str,
    normalized_sentence: str,
) -> int | None:
    """Score text that has already passed through ``normalize_text``."""

    if not normalized_query or not normalized_sentence:
        return None

    query_length = len(normalized_query)

    if normalized_query in normalized_sentence:
        return 2 * query_length

    best_score = None

    # Replacement: query and matching sentence window have the same length.
    for window in _windows(normalized_sentence, query_length):
        position = _single_substitution_position(normalized_query, window)
        if position is not None:
            score = 2 * (query_length - 1) - substitution_penalty(position)
            best_score = score if best_score is None else max(best_score, score)

    # Deletion: the query contains one extra character.
    if query_length > 1:
        for window in _windows(normalized_sentence, query_length - 1):
            position = _removed_character_position(normalized_query, window)
            if position is not None:
                score = 2 * (query_length - 1) - insertion_or_deletion_penalty(
                    position
                )
                best_score = score if best_score is None else max(best_score, score)

    # Insertion: the query is missing one character found in the sentence.
    for window in _windows(normalized_sentence, query_length + 1):
        position = _removed_character_position(window, normalized_query)
        if position is not None:
            score = 2 * query_length - insertion_or_deletion_penalty(position)
            best_score = score if best_score is None else max(best_score, score)

    return best_score


def best_match_score(query: str, sentence: str) -> int | None:
    """Normalize two strings and return their best one-edit substring score."""

    return best_normalized_match_score(
        normalize_text(query),
        normalize_text(sentence),
    )

"""Translate outgoing answers to Spanish before they reach a caller.

Every result the system produces (CLI suggestions, JSON/Protobuf API
responses) is translated here on its way out. Translation depends on a
free third-party endpoint, so any failure - no network, rate limiting, an
unexpected response - is swallowed and the original text is returned
instead of breaking the request it belongs to.
"""

import logging
from dataclasses import replace
from typing import TypeVar

from deep_translator import GoogleTranslator
from deep_translator.exceptions import BaseError as TranslationServiceError

logger = logging.getLogger(__name__)

DEFAULT_TARGET_LANGUAGE = "es"
_SOURCE_LANGUAGE = "en"

_HasCompletedSentence = TypeVar("_HasCompletedSentence")


def translate_text(text: str, target_language: str = DEFAULT_TARGET_LANGUAGE) -> str:
    """Translate ``text`` to ``target_language``, falling back to it unchanged.

    Blank input is returned as-is. Any error raised by the translation
    service is caught here so a translation outage degrades to showing the
    original (English) text rather than failing the caller's request.
    """

    if not text or not text.strip():
        return text

    try:
        translated = GoogleTranslator(
            source=_SOURCE_LANGUAGE,
            target=target_language,
        ).translate(text)
    except TranslationServiceError as error:
        logger.warning("Translation service rejected %r: %s", text, error)
        return text
    except Exception as error:  # noqa: BLE001 - a translation failure must not crash the request
        logger.warning("Translation of %r failed unexpectedly: %s", text, error)
        return text

    return translated if translated else text


def translate_results(
    results: list[_HasCompletedSentence],
    target_language: str = DEFAULT_TARGET_LANGUAGE,
) -> list[_HasCompletedSentence]:
    """Return copies of ``results`` with ``completed_sentence`` translated.

    Works for any result dataclass carrying a ``completed_sentence`` field
    (``AutoCompleteData`` and ``GroupedAutoCompleteData`` both do), so it
    covers every place the engine hands answers to an output layer.
    """

    return [
        replace(
            result,
            completed_sentence=translate_text(result.completed_sentence, target_language),
        )
        for result in results
    ]

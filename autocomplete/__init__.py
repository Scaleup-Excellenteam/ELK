"""Autocomplete project package."""

from .engine import get_best_k_completions
from .models import AutoCompleteData
from .normalization import normalize_text
from .scoring import best_match_score

__all__ = [
    "AutoCompleteData",
    "best_match_score",
    "get_best_k_completions",
    "normalize_text",
]

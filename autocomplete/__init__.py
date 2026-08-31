"""Autocomplete project package."""

from .models import AutoCompleteData
from .normalization import normalize_text

__all__ = ["AutoCompleteData", "normalize_text"]


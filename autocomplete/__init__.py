"""Autocomplete project package."""

from dotenv import load_dotenv

# Loads GEMINI_API_KEY (and any other local settings) from a .env file at the
# repository root, if one exists. Never commit that file — see .env.example.
load_dotenv()

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

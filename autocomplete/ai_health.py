"""AI-generated mission briefings from aggregate service metrics, via Gemini.

Mirrors what a satellite mission-control room does with telemetry: instead
of a human scanning a raw event table, a model is asked to say in plain
language whether anything looks anomalous.
"""

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from dotenv import load_dotenv


_PROJECT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_PROJECT_ENV_FILE)

_MODEL_NAME = "gemini-3.6-flash"
_API_KEY_ENV_VAR = "GEMINI_API_KEY"


@dataclass
class MissionBriefingResult:
    """A generated mission briefing, or an explanation of its unavailability."""

    available: bool
    summary: str


def _build_prompt(metrics: dict[str, Any]) -> str:
    return (
        "You are preparing a concise mission-control briefing for a ground-station "
        "autocomplete service. The input contains aggregate metrics only; no raw user "
        "queries are included. Use exactly this three-line format:\n"
        "Status: Healthy or Attention needed\n"
        "Observation: one short, concrete sentence\n"
        "Recommendation: one short, actionable sentence\n"
        "Use only the supplied numbers, do not invent causes, and say that more data "
        "is needed when the sample is too small.\n\n"
        f"Metrics: {json.dumps(metrics, sort_keys=True)}"
    )


def generate_mission_briefing(metrics: dict[str, Any]) -> MissionBriefingResult:
    """Ask Gemini to interpret aggregate metrics without receiving query text."""

    api_key = os.environ.get(_API_KEY_ENV_VAR)
    if not api_key:
        return MissionBriefingResult(
            available=False,
            summary=(
                f"Mission briefings are disabled: set the {_API_KEY_ENV_VAR} "
                "environment variable to enable them."
            ),
        )

    if not metrics.get("search_count"):
        return MissionBriefingResult(
            available=True,
            summary=(
                "Status: Healthy\n"
                "Observation: No completed searches have been measured yet.\n"
                "Recommendation: Run a few searches before generating a briefing."
            ),
        )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=_MODEL_NAME,
            contents=_build_prompt(metrics),
        )
        summary = (response.text or "").strip()
    except genai_errors.APIError as error:
        return MissionBriefingResult(
            available=False,
            summary=f"The mission briefing service is unavailable right now ({error}).",
        )
    except Exception as error:
        return MissionBriefingResult(
            available=False,
            summary=f"The mission briefing failed unexpectedly ({error}).",
        )

    if not summary:
        return MissionBriefingResult(
            available=False,
            summary="The mission briefing service returned an empty response.",
        )

    return MissionBriefingResult(available=True, summary=summary)

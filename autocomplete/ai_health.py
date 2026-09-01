"""AI-generated health summaries of recent server activity, via Gemini.

Mirrors what a satellite mission-control room does with telemetry: instead
of a human scanning a raw event table, a model is asked to say in plain
language whether anything looks anomalous.
"""

import os
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import errors as genai_errors

_MODEL_NAME = "gemini-3.6-flash"
_MAX_ENTRIES_IN_PROMPT = 100
_API_KEY_ENV_VAR = "GEMINI_API_KEY"


@dataclass
class HealthCheckResult:
    """The outcome of one AI health check: either a summary or why there isn't one."""

    available: bool
    summary: str


def _build_prompt(entries: list[dict[str, Any]]) -> str:
    lines = []
    for entry in entries[:_MAX_ENTRIES_IN_PROMPT]:
        details = {
            key: value for key, value in entry.items() if key not in {"timestamp", "event"}
        }
        lines.append(f"- {entry.get('timestamp')} {entry.get('event')} {details}")
    log_text = "\n".join(lines)

    return (
        "You are a monitoring assistant for a ground-station search service that "
        "relays commands to a satellite cluster. Below are the most recent request "
        "log entries, newest first. In at most 3 short sentences and plain language, "
        "say whether anything looks anomalous (rising latency, error spikes, repeated "
        "rejections) or whether things look healthy. Only use numbers that appear "
        "below; do not invent statistics.\n\n"
        f"{log_text}"
    )


def generate_health_summary(entries: list[dict[str, Any]]) -> HealthCheckResult:
    """Ask Gemini to summarize recent activity, or explain why it can't."""

    api_key = os.environ.get(_API_KEY_ENV_VAR)
    if not api_key:
        return HealthCheckResult(
            available=False,
            summary=(
                f"AI health checks are disabled: set the {_API_KEY_ENV_VAR} "
                "environment variable to enable them."
            ),
        )

    if not entries:
        return HealthCheckResult(available=True, summary="No activity has been logged yet.")

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=_MODEL_NAME,
            contents=_build_prompt(entries),
        )
        summary = (response.text or "").strip()
    except genai_errors.APIError as error:
        return HealthCheckResult(
            available=False,
            summary=f"The AI health service is unavailable right now ({error}).",
        )
    except Exception as error:
        return HealthCheckResult(
            available=False,
            summary=f"The AI health check failed unexpectedly ({error}).",
        )

    if not summary:
        return HealthCheckResult(
            available=False,
            summary="The AI health service returned an empty response.",
        )

    return HealthCheckResult(available=True, summary=summary)

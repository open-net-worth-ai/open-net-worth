"""Settings → AI → Background AI tasks: each background AI task's recent outcomes, with a plain
name and, when it is failing, a plain problem. A read-only projection of
`core.background_ai_health` (in memory, since the server started). Never raises."""

from __future__ import annotations

import logging

from backend.core import background_ai_health as health

logger = logging.getLogger(__name__)

# Friendly names for the common `source` keys; any other key reads as plain words, so a new
# background task shows up without a list to keep in step.
_LABELS = {
    "analysis": "Intel analysis",
    "news_screen": "News screening",
    "asset_brief": "Asset briefs",
    "intel_digest": "Intel digest",
    "intel_scan": "Intel scan",
    "intel_opportunity": "Intel ideas",
    "pulse_brief": "Pulse brief",
    "price_movement": "Price-move notes",
    "technicals": "Technical analysis",
    "watch_analyst": "Watchlist analysis",
    "watch_reader": "Watchlist reading",
    "chat_suggestions": "Chat suggestions",
    "statement_parse": "Statement reading",
    "statement_vision": "Statement reading (images)",
    "statement_metadata": "Statement details",
    "briefing": "Daily briefing",
}

_PROBLEMS = {
    "no_usable_answer": "The AI gave no usable answer, even after retrying",
    "rate_limited": "The AI provider rate-limited the request",
    "auth": "The AI provider rejected the sign-in or API key",
    "timeout": "The AI provider timed out",
}


def _label(source: str) -> str:
    return _LABELS.get(source) or source.replace("_", " ").strip().capitalize() or "Unknown task"


def _problem(kind: str | None, detail: str | None) -> str | None:
    if not kind:
        return None
    if kind == "refused":
        base = "The AI provider declined the request"
        return f"{base} ({detail})" if detail else base
    if kind in _PROBLEMS:
        return _PROBLEMS[kind]
    return f"Error ({detail})" if detail else "Error"


def background_ai_health() -> dict:
    """{since, tasks: [{source, label, status, calls_24h, failures_24h, last_success_at,
    last_failure_at, problem}]} — most troubled first."""
    try:
        rows = health.snapshot()
    except Exception:
        logger.warning("background_ai_health: snapshot failed", exc_info=True)
        rows = []
    tasks = [
        {
            "source": r["source"],
            "label": _label(r["source"]),
            "status": r["status"],
            "calls_24h": r["calls_24h"],
            "failures_24h": r["failures_24h"],
            "last_success_at": r["last_success_at"],
            "last_failure_at": r["last_failure_at"],
            "problem": _problem(r["last_failure_kind"], r["last_failure_detail"]),
        }
        for r in rows
    ]
    return {"since": health.started_at(), "tasks": tasks}

"""Topic-subject counterparts to analyst.py's asset-analysis helpers (INT3).

A pending-analysis row whose subject is "topic:{item_id}" (a watchlist topic news_spike —
see rss_poller_topics._process_topic) has no instrument_id/ticker to enrich with: there's no
position to size, no ticker-keyed feed resolver. This module holds the topic-shaped equivalents
of analyst.py's asset helpers (RSS freshen, context-gathering, event framing, signal creation) so
analyst.py's `_process_row` can branch on `is_topic` and stay a thin dispatcher, and so analyst.py
itself doesn't grow past the file-size convention. Split out rather than inlined; analyst.py
imports this module (no cycle — topic_analyst never imports analyst).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from backend.monitoring.analyst_prompt import entry_title, source_dicts
from backend.monitoring.text_sanitize import sanitize_untrusted_text

logger = logging.getLogger(__name__)


async def freshen_topic_rss(subject: str, payload: dict) -> None:
    """Free, guarded on-move RSS fetch for one watchlist topic — best-effort, mirrors
    analyst._freshen_rss. Rebuilds a minimal item dict from the pending-row payload (label +
    search_queries, carried through by rss_poller at enqueue time) rather than re-reading the
    watchlist, so the analyst stays a pure consumer of the pending-row + evidence cache."""
    try:
        from backend.monitoring.collect import gather_rss
        from backend.services.feeds import resolve_topic_feeds

        item_id = subject[len("topic:") :] if subject.startswith("topic:") else ""
        label = payload.get("label") or ""
        feeds = resolve_topic_feeds(
            {
                "id": item_id,
                "value": label,
                "label": label,
                "search_queries": payload.get("search_queries") or [],
            }
        )
        if feeds:
            await gather_rss(feeds, read_back=False)
    except Exception as exc:
        logger.debug("analyst: RSS freshen failed for topic %s: %s", subject, type(exc).__name__)


def topic_context(payload: dict) -> tuple[str, dict]:
    """WATCH CONTEXT + investor profile for a topic-subject row — the topic counterpart to
    analyst._gather_position_context. A topic isn't a holding, so there is no position to size;
    the watchlist item's own user-authored note is the closest analogue (sanitized the same way
    profile notes are, since it rides the prompt outside the untrusted-news block). Best-effort:
    a profile-read failure never blocks the analysis."""
    profile: dict = {}
    try:
        from backend.api.profile import read_profile

        profile = read_profile() or {}
    except Exception as exc:
        logger.debug("analyst: profile read failed for topic context: %s", type(exc).__name__)

    note = sanitize_untrusted_text((payload.get("note") or "").strip(), 300)
    ctx = "Watchlist topic — not a portfolio holding."
    if note:
        ctx += f" User note: {note}"
    return ctx, profile


def topic_event_framing(label: str) -> tuple[str, str, str]:
    """The topic-shaped (event_line, why_question, context_header) triple analyst._event_framing
    dispatches to — a topic has no ticker, so the wording drops it entirely."""
    event_line = f"{label} has a surge of news coverage"
    question = (
        "  why: the SPECIFIC development driving the coverage (name it); "
        "if it's only generic chatter, say so\n"
    )
    return event_line, question, "WATCH CONTEXT"


def create_topic_spike_signal(
    row: dict, parsed: dict, evidence_watermark: str, evidence: list[dict] | None = None
) -> dict:
    """Build a new 'topic' type signal for a topic news_spike row (no prior signal existed).

    Mirrors analyst._create_news_spike_signal's shape with ticker="" (a topic is never a
    ticker) — the Intel feed already renders type='topic' signals (intel_agent's own scans
    create them), so no frontend change is needed. `evidence_watermark` (INT14b security fix) is
    the timestamp captured immediately BEFORE this analysis's evidence read — see
    analyst._update_signal's docstring for why it must be the pre-read time, not `now`. Never
    called for a `noise`/`no_change` verdict (those create no story); the first entry is titled
    with the model's headline, "New development" only when it gave none.
    `evidence` (ADR 2026-08-28) is the same collapsed evidence list the synthesis call read —
    carried onto the first entry's `id`/`sources` exactly like the asset-signal creator does;
    there is no prior timeline yet, so `duplicate_of_id`/`contradicts_id` never have anything to
    resolve against on this path."""
    payload = row.get("payload") or {}
    label = payload.get("label") or "Topic"
    why = (parsed.get("why") or "")[:300]
    actions = [str(a)[:200] for a in (parsed.get("actions") or [])[:1]]
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": row.get("signal_id") or f"news-topic-{row.get('subject', '')}",
        "type": "topic",
        "label": label,
        "ticker": "",
        "status": "monitoring",
        "current_summary": why,
        "portfolio_impact": (parsed.get("impact") or "")[:200],
        "action_items": actions,
        "timeline": [
            {
                "id": uuid4().hex,
                "date": now,
                "title": entry_title(parsed, "New development"),
                "summary": why,
                "sources": source_dicts(evidence or []),
            }
        ],
        "analyzed": True,
        "created_at": now,
        "updated_at": now,
        "evidence_watermark": evidence_watermark,
    }

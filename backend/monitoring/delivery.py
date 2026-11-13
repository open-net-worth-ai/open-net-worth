"""The Intel delivery seam — the ONE place the ``intel_delivery_decider`` flag is read.

The three Intel senders (the analyst's news push, price alerts / crash-watch, the watch
analyst's explained alert) each reach the point where today they would push. They call
``route(candidate_factory, legacy)`` there instead:

* flag off — ``legacy()`` runs, the sender's own push function, byte for byte as before;
* flag on — the candidate becomes a ``news_deliveries`` row and Lucius decides now / later /
  skip (``monitoring.delivery_decider``). Nothing is pushed here.

A candidate is created exactly where a push would have fired, after every upstream rail
(duplicate, silent materiality, stale, ungrounded …), so this path can never push MORE than
today. If the flag cannot be read, no channel is configured, or the row cannot be written, the
sender's own push runs — a failure here falls back to today's behaviour, never to silence.

Candidate fields: ``source`` (analysis | price_alert | crash_watch | watch_alert), ``route_key``
(the ``notify_routes`` kind), ``channels`` (only when the alert owns them — price alerts),
``story_id``, ``subject``, ``ticker``, ``label``, ``headline``, ``why``, ``impact``, ``actions``,
``news`` (a crash's cached headlines and, when run, a web search's findings),
``sources`` (server-built ``{url, title, domain}``), ``materiality``, ``user_set`` (a level the
user set — price alerts, crash-watch), ``legacy_message`` (today's rendered text) and
``created_at``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from backend.services import notify_routes

logger = logging.getLogger(__name__)


def delivery_active() -> bool:
    from backend.services.feature_flags import enabled

    return enabled("intel_delivery_decider")


async def route(
    candidate_factory: Callable[[], dict], legacy: Callable[[], Awaitable[None]]
) -> None:
    """``candidate_factory`` builds the candidate and runs only with the flag on, so with it off
    nothing but ``legacy()`` executes; an error building it falls through to ``legacy()`` too."""
    try:
        active = await asyncio.to_thread(delivery_active)
    except Exception as exc:
        logger.warning("delivery: flag unreadable, sending as before: %s", type(exc).__name__)
        active = False
    if not active:
        await legacy()
        return
    try:
        candidate = candidate_factory()
        channels = candidate.get("channels") or await asyncio.to_thread(
            notify_routes.channels_for, candidate["route_key"]
        )
    except Exception as exc:
        logger.warning("delivery: could not build candidate, sending as before: %s", exc)
        await legacy()
        return
    if not channels:
        await legacy()
        return
    try:
        from backend.services.news_delivery import apply_delivery_change

        await asyncio.to_thread(apply_delivery_change, "submit", candidate=candidate)
    except Exception as exc:
        logger.warning("delivery: could not record candidate, sending as before: %s", exc)
        await legacy()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def analysis_candidate(
    label: str,
    ticker: str,
    parsed: dict,
    *,
    sources: list[dict],
    story_id: str,
    subject: str,
    materiality: str,
    legacy_message: str,
) -> dict:
    return {
        "source": "analysis",
        "route_key": notify_routes.NEWS,
        "story_id": story_id,
        "subject": subject,
        "ticker": ticker,
        "label": label,
        "headline": (parsed.get("headline") or "").strip() or label,
        "why": parsed.get("why") or "",
        "impact": parsed.get("impact") or "",
        "actions": list(parsed.get("actions") or []),
        "sources": sources,
        "materiality": materiality,
        "user_set": False,
        "legacy_message": legacy_message,
        "created_at": _now_iso(),
    }


def signal_candidate(
    signal: dict,
    channels: list[str],
    *,
    crash_watch: bool,
    legacy_message: str,
    news: str = "",
    sources: list[dict] | None = None,
) -> dict:
    """A fired price alert or crash-watch story. Its channels belong to the alert itself. ``news``
    is what Intel has on the asset right now (a crash carries it); ``sources`` its links."""
    label = (signal.get("label") or "").replace(" — price alert", "")
    iid = signal.get("instrument_id") or ""
    return {
        "source": "crash_watch" if crash_watch else "price_alert",
        "route_key": notify_routes.PRICE_ALERTS,
        "channels": list(channels),
        "story_id": signal.get("id") or "",
        "subject": f"asset:{iid}" if iid else (signal.get("ticker") or ""),
        "ticker": signal.get("ticker") or "",
        "label": label,
        "headline": label,
        "why": signal.get("current_summary") or "",
        "impact": signal.get("portfolio_impact") or "",
        "actions": list(signal.get("action_items") or []),
        "news": news,
        "sources": list(sources or []),
        "materiality": "",
        "user_set": True,
        "legacy_message": legacy_message,
        "created_at": _now_iso(),
    }


def watch_candidate(ticker: str, story: dict, explained: dict, *, legacy_message: str) -> dict:
    iid = story.get("instrument_id") or ""
    return {
        "source": "watch_alert",
        "route_key": notify_routes.WATCH_ALERTS,
        "story_id": story.get("id") or "",
        "subject": f"asset:{iid}" if iid else ticker,
        "ticker": ticker,
        "label": story.get("label") or ticker,
        "headline": explained.get("what_happened") or "",
        "why": explained.get("why") or "",
        "impact": explained.get("what_it_means") or "",
        "actions": list(explained.get("worth_weighing") or []),
        "sources": list(explained.get("sources") or []),
        "materiality": "",
        "user_set": False,
        "legacy_message": legacy_message,
        "created_at": _now_iso(),
    }

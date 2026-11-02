"""Sending for the delivery decider (``monitoring.delivery_decider``).

A message goes out through the same Notifier and zero-delivery policy every Intel push uses
(``push_retry``), and an item is marked told only after a send that reached at least one channel.
A roundup is the exception to the retry queue: its items stay waiting after a failed send and are
rolled up again, so a queued retry could deliver the same roundup twice.
"""

from __future__ import annotations

import asyncio
import logging

from backend.monitoring import push_retry
from backend.services import notify_routes
from backend.services.news_delivery import apply_delivery_change

logger = logging.getLogger(__name__)


async def notify_once(
    message: str, channels: list[str], label: str, ticker: str = "", *, retry: bool = True
) -> bool:
    """Send; True when at least one channel delivered. ``retry=False`` leaves a failed send to the
    caller instead of queuing it (every channel off is still reported in the app)."""
    from backend.services.notify import notify

    try:
        delivered = await notify(message, channels=channels)
    except Exception as exc:
        logger.warning("delivery: notify failed for %s: %s", ticker or label, type(exc).__name__)
        delivered = {}
    logger.info("delivery: pushed via Notifier to %s — %s", channels, delivered)
    await push_retry.handle_push_result(delivered, message, channels, label, ticker, retry=retry)
    return push_retry.delivered_ok(delivered)


def record_banner(row: dict) -> None:
    """An analysis the person was told shows on the desktop feed, as a pushed analysis does today."""
    if row.get("source") != "analysis" or not row.get("story_id"):
        return
    try:
        from backend.services import alert_feed

        headline = row.get("headline") or ""
        label = row.get("label") or ""
        alert_feed.record_analysis_push(
            row["story_id"],
            title=f"{label}: {headline}" if headline else f"Analysis — {label}",
            summary=row.get("why") or "",
        )
    except Exception as exc:
        logger.warning("delivery: desktop alert record failed: %s", type(exc).__name__)


async def mark_told(row: dict, told_via: str, message_sent: str) -> None:
    told = await asyncio.to_thread(
        apply_delivery_change,
        "mark_told",
        delivery_id=row["id"],
        told_via=told_via,
        message_sent=message_sent,
    )
    if told:
        await asyncio.to_thread(record_banner, row)


def channels_for(row: dict) -> list[str]:
    return list(row.get("channels") or []) or notify_routes.channels_for(row["route_key"])


async def send_now(row: dict, message: str) -> None:
    channels = await asyncio.to_thread(channels_for, row)
    if not channels:
        logger.warning("delivery: %s has no channel to go to", row["id"])
        return
    if await notify_once(message, channels, row.get("label") or "", row.get("ticker") or ""):
        await mark_told(row, "now", message)


def _roundup_channels(rows: list[dict]) -> list[str]:
    """The news route; when it has none, the routes the waiting items were meant for."""
    news = notify_routes.channels_for(notify_routes.NEWS)
    return news or list(dict.fromkeys(c for r in rows for c in channels_for(r)))


async def send_roundup(rows: list[dict], message: str) -> bool:
    channels = await asyncio.to_thread(_roundup_channels, rows)
    if not channels:
        logger.warning("delivery: roundup has no channel to go to")
        return False
    if not await notify_once(message, channels, "Roundup", retry=False):
        return False
    for row in rows:
        summary = (row.get("headline") or row.get("why") or "")[:200]
        await mark_told(row, "roundup", f"(roundup) {summary}")
    return True

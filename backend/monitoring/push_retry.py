"""Shared zero-delivery push retry — the fix for the 2026-07-16 dropped-push incident.

``services.notify.notify()`` stays deliberately best-effort (its OTHER callers —
reminders, digests, ... — depend on that contract) so the "zero delivery is a
failure" interpretation lives here, at the two call sites that push analyst/alert
content the user is relying on to actually arrive: ``monitoring.analyst._push_analysis``
and ``monitoring.price_monitor._push_external_alert``. When EVERY configured channel
reports zero delivery (``sent==0``, ``error``, or ``skipped``), the rendered message is
queued in ``PendingPushStore`` (a durable DB table, not an in-memory timer — survives a
dev-server hot-reload or any process restart) and retried with bounded backoff by
whichever scheduler tick calls ``drain_pending_pushes`` — currently the ~60s
``analysis_sweep`` job, which runs unconditionally regardless of pending analyses, so a
queued row from either producer gets drained promptly.

At-least-once delivery, deliberately: a crash between a successful ``notify()`` call
and ``store.remove()`` re-sends the same message on the next tick (the row is still
"due" until removed). A rare duplicate push is the accepted tradeoff over the
alternative — remove-then-notify — which could lose the message entirely on the same
crash. This mirrors the incident this fixes: a lost alert is worse than a duplicate one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# 60s / 5min / 15min — three bounded retry attempts before giving up.
RETRY_DELAYS_SEC = (60, 300, 900)

# Cap on rows retried per drain call — mirrors analyst.run_analysis_sweep's own
# _PER_SWEEP_CAP: a burst of due rows must not make one scheduler tick unbounded.
_MAX_DRAIN_PER_TICK = 20


def delivered_ok(delivered: dict) -> bool:
    """True if at least one channel actually delivered (``sent`` truthy).

    A channel reporting ``error``/``skipped``/``sent: 0`` does not count — this is the
    "zero delivery across every channel" check the incident's silent-drop bypassed."""
    return any((result or {}).get("sent") for result in delivered.values())


def _all_channels_off(delivered: dict) -> bool:
    """Every target was skipped as unregistered — the channel was turned off or removed.
    A retry can't reach a channel that isn't there, so it is reported now instead."""
    return bool(delivered) and all(
        (result or {}).get("skipped") == "unknown_channel" for result in delivered.values()
    )


def _channel_names(channels: list[str]) -> str:
    try:
        from backend.services.connectors import notifiable_channels

        labels = {c["id"]: c["label"] for c in notifiable_channels()}
    except Exception:
        labels = {}
    names = [labels.get(c) or c.capitalize() for c in channels]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


async def report_undelivered(channels: list[str], label: str, *, gave_up: bool) -> None:
    """Tell the user, in the app, that a push reached none of its channels. Best-effort."""
    try:
        from backend.services.notify import notify

        what = f"“{label}”" if label else "An alert"
        where = _channel_names(channels) or "your alert channels"
        body = (
            f"{what} couldn't be sent to {where} after {len(RETRY_DELAYS_SEC)} tries. "
            "It's still in Intel."
            if gave_up
            else f"{what} wasn't sent to {where} because the channel is off or removed. "
            "It's still in Intel. Turn the channel back on in Connectors, or change the "
            "alert's notifications."
        )
        await notify(body, channels=["in_app"], title="Alert not delivered")
    except Exception as exc:
        logger.warning("push_retry: undelivered notice failed: %s", type(exc).__name__)


async def handle_push_result(
    delivered: dict,
    message: str,
    channels: list[str],
    label: str,
    ticker: str = "",
    *,
    retry: bool = True,
) -> None:
    """The one zero-delivery policy for both producers: delivered → nothing; every channel
    off or removed → tell the user now; otherwise queue a bounded retry (``retry=False`` for a
    caller that re-sends the content itself and would otherwise deliver it twice)."""
    if delivered_ok(delivered):
        return
    if _all_channels_off(delivered):
        logger.warning("push_retry: every channel is off for %s — %s", ticker or label, channels)
        await report_undelivered(channels, label, gave_up=False)
        return
    if retry:
        enqueue_push_retry(message, channels, label, ticker)


def enqueue_push_retry(message: str, channels: list[str], label: str, ticker: str = "") -> None:
    """Persist a zero-delivery push for bounded retry. Best-effort: never raises —
    a queueing failure must not make the caller's own push path raise."""
    try:
        from backend.db.pending_push_store import PendingPushStore

        next_attempt_at = (
            datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAYS_SEC[0])
        ).isoformat()
        PendingPushStore().add(message, channels, label, ticker, next_attempt_at)
    except Exception as exc:
        logger.warning(
            "push_retry: failed to enqueue retry for %s: %s", ticker or label, type(exc).__name__
        )


async def drain_pending_pushes(source: str) -> None:
    """Retry every due row (capped at ``_MAX_DRAIN_PER_TICK`` per call), each attempt
    up to ``len(RETRY_DELAYS_SEC)`` total.

    ``source`` is a short log tag identifying the caller (e.g. "analyst") so a
    recovered/exhausted log line is traceable to whichever tick drained it — the queue
    itself is shared across producers (analyst + price_monitor), so this need only be
    wired into ONE scheduler tick. Each row is isolated in its own try/except — a single
    malformed row (e.g. missing "id") is logged and skipped, never aborting the rest of
    the tick's due rows."""
    from backend.db.pending_push_store import PendingPushStore
    from backend.services.notify import notify

    store = PendingPushStore()
    now_iso = datetime.now(timezone.utc).isoformat()
    due_rows = store.due(now_iso)[:_MAX_DRAIN_PER_TICK]
    for row in due_rows:
        try:
            await _drain_one_row(store, source, row, notify)
        except Exception as exc:
            logger.warning(
                "%s: push retry row processing failed (id=%s): %s",
                source,
                row.get("id", "?"),
                type(exc).__name__,
            )


async def _drain_one_row(store, source: str, row: dict, notify) -> None:
    """Process one due row: retry, then recover/reschedule/exhaust it."""
    row_id = row["id"]
    channels = row.get("channels") or []
    tag = row.get("ticker") or row.get("label") or "?"
    try:
        delivered = await notify(row.get("message") or "", channels=channels)
    except Exception as exc:
        logger.warning("%s: push retry errored for %s: %s", source, tag, type(exc).__name__)
        delivered = {}
    logger.info("%s: retried push via Notifier to %s — %s", source, channels, delivered)

    if delivered_ok(delivered):
        logger.info(
            "%s: push recovered for %s after %d attempt(s)",
            source,
            tag,
            row.get("attempts", 0) + 1,
        )
        store.remove(row_id)
        return

    attempts = row.get("attempts", 0) + 1
    if attempts >= len(RETRY_DELAYS_SEC):
        logger.warning(
            "%s: push delivery exhausted after %d attempts for %s", source, attempts, tag
        )
        store.remove(row_id)
        await report_undelivered(channels, row.get("label") or tag, gave_up=True)
    else:
        next_attempt_at = (
            datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAYS_SEC[attempts])
        ).isoformat()
        store.reschedule(row_id, attempts, next_attempt_at)

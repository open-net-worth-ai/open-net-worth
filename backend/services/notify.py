"""
Notifier — channel-agnostic outbound notifications (P2-SK2 §B.5).

``notify(message, channels=None)`` fans a *pre-formatted* message out to every
registered notification channel (or only the named ones), returning a per-channel
result dict. Channels register themselves on the ``core.channels`` seam: the
Telegram channel lives in ``connectors/`` (registered when the connector boots),
the **in-app** channel is registered by this module at import so a headless /
Telegram-less instance always has ≥1 working channel.

Layering: ``services → core.channels`` (down) and ``services → db.NotificationLogStore``
(down) only — never ``services → connectors`` (the Telegram channel reaches us via
the seam). No money math, no currency assumptions: the message body is passed
through verbatim to each channel's own formatter (the caller in ``services/`` is the
only place money is formatted), so a body like ``S$1,234.56`` / ``€100`` arrives
unmodified.

Channel isolation: each channel is awaited inside its own try/except, so a raising
channel is reported (``{"error": "..."}``) but never propagates — the other channels
still deliver (mirrors ``bot.notify``'s best-effort contract).
"""

from __future__ import annotations

import inspect
import logging

from backend.core.channels import (
    register_channel,
    registered_channels,
    registered_document_channels,
)

logger = logging.getLogger(__name__)


async def notify(message: str, channels: list[str] | None = None, title: str | None = None) -> dict:
    """Fan ``message`` out to the named ``channels`` (or all registered ones).

    Returns ``{channel_name: result_dict}``. An unknown channel name is skipped
    (not an error) and reported as ``{"skipped": "unknown_channel"}``. A channel
    that raises is isolated and reported as ``{"error": "<msg>"}``.

    ``title`` is the notification's subject line, shown only by a channel with a real title
    slot (the in-app signal feed). A plain-text channel gets the message alone, so the
    message must stand on its own: pasting the title above it read as a stray heading line
    ("Capture", blank line, "Booked: …") on Telegram. Only an empty message falls back to
    the title, so nothing is ever sent blank.
    """
    available = registered_channels()
    targets = channels if channels is not None else list(available)
    subject = (title or "").strip()
    body = message if message.strip() else subject
    results: dict[str, dict] = {}
    for name in targets:
        fn = available.get(name)
        if fn is None:
            results[name] = {"skipped": "unknown_channel"}
            continue
        try:
            if subject and _takes_title(fn):
                # `Channel` is typed one-arg, but _takes_title has just PROVEN by inspection
                # that this channel has a real title slot — a runtime fact the static type
                # cannot carry. Widening `Channel` instead would drop arity checking for
                # every register_channel() call site to fix one deliberate exception.
                results[name] = await fn(message, title=subject)  # type: ignore[call-arg]
            else:
                results[name] = await fn(body)
        except Exception as exc:  # per-channel isolation — never propagate
            logger.warning("notify: channel %s failed: %s", name, exc)
            results[name] = {"error": str(exc)}
    return results


def external_channels() -> list[str]:
    """Every registered channel except the in-app one — for a caller that has already written
    its own in-app row and must not have ``notify`` write a second."""
    return [name for name in registered_channels() if name != "in_app"]


# Telegram's caption ceiling; a longer message travels as its own text ahead of the file.
_MAX_CAPTION_CHARS = 1024


async def notify_document(
    artifact_id: str, message: str, channels: list[str], title: str | None = None
) -> dict:
    """Deliver a stored artifact as a FILE to each named channel, with ``message`` as caption.

    A message too long for a caption is sent first as ordinary text, then the file follows
    under its title. A channel with no file support is reported ``unsupported`` — never
    silently downgraded to text, so the caller can say the report was not attached.
    """
    from backend.services import artifacts as artifacts_service

    meta = artifacts_service.get_meta(artifact_id)
    content = artifacts_service.read_bytes(artifact_id)
    # The caption is the sender's own words, standing alone as in notify(); the title, then
    # the artifact's title, are only fallbacks for a send with no message at all.
    subject = (title or "").strip()
    caption = message.strip() or subject or str(meta.get("title") or "")
    documents = registered_document_channels()
    results: dict[str, dict] = {}
    for name in channels:
        fn = documents.get(name)
        if fn is None:
            results[name] = {"skipped": "unsupported"}
            continue
        if not artifacts_service.claim_delivery(artifact_id, name):
            results[name] = {"skipped": "already_sent_this_turn"}
            continue
        try:
            channel_caption = caption
            if len(caption) > _MAX_CAPTION_CHARS:
                await notify(message, channels=[name], title=title)
                channel_caption = (subject or str(meta.get("title") or ""))[:_MAX_CAPTION_CHARS]
            results[name] = await fn(content, str(meta["filename"]), channel_caption)
        except Exception as exc:  # per-channel isolation — never propagate
            logger.warning("notify_document: channel %s failed: %s", name, exc)
            results[name] = {"error": str(exc)}
    return results


def _takes_title(fn) -> bool:
    """Does this channel have a real title slot, or must the title ride in the body?

    Asked of the function rather than kept in a list, so a connector opts in by adding the
    parameter and nothing here has to know about it.
    """
    try:
        return "title" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


# ── In-app channel (always registered, headless-safe) ─────────────────────────


async def _in_app_send(message: str, title: str | None = None) -> dict:
    """Surface a notification in the app's notification log (/api/notification-log).

    Appends a ``type="notification"`` unread row so a Telegram-less instance still
    sees scheduled reminders / alerts. Verbatim message — no formatting, no money math.
    """
    from backend.db import NotificationLogStore

    signal = NotificationLogStore().append(
        {
            "type": "notification",
            "title": (title or "").strip() or "Notification",
            "summary": message,
            "trigger": "notify",
        }
    )
    return {"sent": 1, "signal_id": signal.get("id")}


# Register the in-app channel at import so there is always ≥1 channel (AC-9). The
# Telegram channel registers itself from connectors/ when the connector boots.
register_channel("in_app", _in_app_send)

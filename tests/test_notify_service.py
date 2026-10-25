"""
Unit tests for the Notifier (P2-SK2 §B.5): channel fan-out + per-channel isolation,
no-channel → in-app only, channels= targeting + unknown-channel skip, and verbatim
(multi-currency) message passthrough.

Channels are registered on the core.channels seam; each test snapshots + restores the
registry so registrations stay isolated. The in-app channel writes to NotificationLogStore, which
is DB-backed (plan 4d task 2) — db_isolated redirects it to a fresh, empty database. No
LLM, no network.

Run:
    .venv/bin/python3 -m pytest tests/test_notify_service.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.core import channels
from backend.services.notify import notify


@pytest.fixture
def clean_registry(monkeypatch):
    """Start each test with an empty channel registry; restore afterwards."""
    saved = channels.registered_channels()
    # Empty the live dict in place so module-level references see it.
    channels._CHANNELS.clear()
    yield
    channels._CHANNELS.clear()
    channels._CHANNELS.update(saved)


@pytest.fixture
def isolated_signals(tmp_path, monkeypatch, db_isolated):
    return tmp_path


# ── fan-out + isolation (AC-8) ──────────────────────────────────────────────────


def test_fan_out_isolates_a_raising_channel(clean_registry):
    async def ok_chan(msg):
        return {"sent": 2}

    async def bad_chan(msg):
        raise RuntimeError("telegram down")

    channels.register_channel("telegram", bad_chan)
    channels.register_channel("in_app", ok_chan)

    result = asyncio.run(notify("hello"))
    # The raising channel is reported, not propagated; the other still delivered.
    assert result["in_app"] == {"sent": 2}
    assert "error" in result["telegram"]
    assert "telegram down" in result["telegram"]["error"]


# ── no Telegram configured → in-app only (AC-9) ─────────────────────────────────


def test_no_telegram_still_delivers_via_in_app(isolated_signals):
    # The real (import-registered) in_app channel is the only one present here.
    from backend.db import NotificationLogStore

    result = asyncio.run(notify("headless reminder"))
    assert result["in_app"]["sent"] == 1
    # A notification signal was appended (unread) so the app surfaces it.
    sigs = NotificationLogStore().query_signals(type="notification")
    assert len(sigs) == 1
    assert sigs[0]["summary"] == "headless reminder"
    assert sigs[0]["read"] is False


# ── channels= targeting + unknown-channel skip (AC-10) ──────────────────────────


def test_channels_targeting_and_unknown_skip(clean_registry):
    calls = {"a": 0, "b": 0}

    async def chan_a(msg):
        calls["a"] += 1
        return {"sent": 1}

    async def chan_b(msg):
        calls["b"] += 1
        return {"sent": 1}

    channels.register_channel("a", chan_a)
    channels.register_channel("b", chan_b)

    # Target only "a" + a non-existent "zzz".
    result = asyncio.run(notify("x", channels=["a", "zzz"]))
    assert calls == {"a": 1, "b": 0}  # b never targeted
    assert result["a"] == {"sent": 1}
    assert result["zzz"] == {"skipped": "unknown_channel"}


# ── verbatim multi-currency passthrough (AC-11) ─────────────────────────────────


def test_message_passed_through_verbatim(clean_registry):
    seen = {}

    async def capture(msg):
        seen["msg"] = msg
        return {"sent": 1}

    channels.register_channel("cap", capture)
    body = "Spending: S$1,234.56 here, €100 there — net +S$50.00"
    asyncio.run(notify(body, channels=["cap"]))
    # No money math, no currency normalization — the body arrives byte-identical.
    assert seen["msg"] == body


def test_in_app_message_verbatim(isolated_signals):
    from backend.db import NotificationLogStore

    body = "Charged €100 and S$1,234.56"
    asyncio.run(notify(body, channels=["in_app"]))
    sigs = NotificationLogStore().query_signals(type="notification")
    assert sigs[0]["summary"] == body


# ── the title parameter (moved from the retired notify AI-tool's own test file —
# ── these test notify()'s own title routing, unrelated to which AI tool calls it) ──


def test_a_channel_with_a_title_slot_gets_the_title_separately(clean_registry):
    seen = {}

    async def titled(message: str, title: str | None = None):
        seen["call"] = (title, message)
        return {"sent": 1}

    channels.register_channel("titled", titled)
    asyncio.run(notify("45.8 units", channels=["titled"], title="Overdue vest: ONWAI"))

    assert seen["call"] == ("Overdue vest: ONWAI", "45.8 units")


def test_a_plain_text_channel_gets_the_message_alone(clean_registry):
    """The title pasted above the message read as a stray heading line on Telegram."""
    seen = []

    async def plain(message: str):
        seen.append(message)
        return {"sent": 1}

    channels.register_channel("plain", plain)
    asyncio.run(notify("Booked: GRAB, 181,400 VND", channels=["plain"], title="Capture"))

    assert seen == ["Booked: GRAB, 181,400 VND"]


def test_an_empty_message_falls_back_to_the_title(clean_registry):
    seen = []

    async def plain(message: str):
        seen.append(message)
        return {"sent": 1}

    channels.register_channel("plain", plain)
    asyncio.run(notify("  ", channels=["plain"], title="Overdue vest: ONWAI"))

    assert seen == ["Overdue vest: ONWAI"]


def test_no_title_leaves_the_message_verbatim(clean_registry):
    seen = []

    async def plain(message: str):
        seen.append(message)
        return {"sent": 1}

    channels.register_channel("plain", plain)
    asyncio.run(notify("S$1,234.56 due", channels=["plain"]))

    assert seen == ["S$1,234.56 due"]


def test_a_blank_title_is_treated_as_no_title(clean_registry):
    seen = []

    async def plain(message: str):
        seen.append(message)
        return {"sent": 1}

    channels.register_channel("plain", plain)
    asyncio.run(notify("body", channels=["plain"], title="   "))

    assert seen == ["body"]


def test_the_in_app_feed_uses_the_title_instead_of_the_word_Notification(isolated_signals):
    from backend.db import NotificationLogStore

    delivered = asyncio.run(notify("45.8 units", channels=["in_app"], title="Overdue vest: ONWAI"))
    signal_id = delivered["in_app"]["signal_id"]
    row = next(r for r in NotificationLogStore().all() if r["id"] == signal_id)

    assert (row["title"], row["summary"]) == ("Overdue vest: ONWAI", "45.8 units")


def test_an_untitled_notification_still_falls_back_to_the_generic_label(isolated_signals):
    from backend.db import NotificationLogStore

    delivered = asyncio.run(notify("body", channels=["in_app"]))
    row = next(
        r for r in NotificationLogStore().all() if r["id"] == delivered["in_app"]["signal_id"]
    )

    assert row["title"] == "Notification"


# ── the per-channel AI tools (telegram_send_message/webhook_send_message) ───────
# ── each targets exactly ONE hardcoded channel — no allowlist parameter exists ──


async def test_telegram_send_message_targets_only_telegram(clean_registry):
    calls = {"telegram": 0, "webhook": 0}

    async def telegram_chan(msg):
        calls["telegram"] += 1
        return {"sent": 1}

    async def webhook_chan(msg):
        calls["webhook"] += 1
        return {"sent": 1}

    channels.register_channel("telegram", telegram_chan)
    channels.register_channel("webhook", webhook_chan)

    from backend.ai.tools import execute_tool

    raw, _ = await execute_tool("telegram_send_message", {"message": "hi"})

    assert json.loads(raw)["ok"] is True
    assert calls == {"telegram": 1, "webhook": 0}


async def test_webhook_send_message_targets_only_webhook(clean_registry):
    calls = {"telegram": 0, "webhook": 0}

    async def telegram_chan(msg):
        calls["telegram"] += 1
        return {"sent": 1}

    async def webhook_chan(msg):
        calls["webhook"] += 1
        return {"sent": 1}

    channels.register_channel("telegram", telegram_chan)
    channels.register_channel("webhook", webhook_chan)

    from backend.ai.tools import execute_tool

    raw, _ = await execute_tool("webhook_send_message", {"message": "hi"})

    assert json.loads(raw)["ok"] is True
    assert calls == {"telegram": 0, "webhook": 1}

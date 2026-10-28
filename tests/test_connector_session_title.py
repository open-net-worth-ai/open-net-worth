"""A connector conversation gets a stable name from its channel and start time.

Owner decision, 2026-08-22: a Telegram thread should NOT be named after its first message.
A web chat is topic-scoped, so its first message is a fair title; a Telegram chat is one
continuous thread whose opening line is usually a greeting — a real user conversation
began with ``Hello``, which says nothing about a month of history.

Before this, nothing named a connector conversation at all: the backend never set a title
on that path, and the frontend — the only thing that generates titles — filters connector
rows out of its list (``ChatPanel``/``ConversationBrowser`` drop any row carrying
``channel``). Every Telegram conversation stayed "New conversation" permanently.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.ai import assistant
from backend.connectors.session_title import connector_session_title, ensure_session_titled


def test_the_title_is_channel_then_start_time():
    when = datetime(2026, 8, 22, 6, 29, tzinfo=timezone.utc).astimezone()
    title = connector_session_title("telegram", when)
    assert title.startswith("Telegram • ")
    assert when.strftime("%-d %b %Y, %H:%M") in title


def test_the_title_never_contains_the_chat_id():
    """The ``chat_id → session_id`` map is a SECRET_TABLES member that
    ``services.backup.write_sanitised_copy`` strips from a downloaded archive, while
    sessions themselves are portable. A chat id in the title would ride into the very
    archive that strips the mapping."""
    title = connector_session_title("telegram", datetime.now(timezone.utc))
    assert "5550001111" not in title
    assert not any(ch.isdigit() and len(part) > 6 for part in title.split() for ch in part), (
        f"a long digit run looks like a chat id: {title!r}"
    )


def test_an_untitled_session_is_named_from_its_own_created_at(db_isolated):
    sid = "sess-title-created"
    started = datetime.now(timezone.utc) - timedelta(days=3)
    assistant.upsert_session_meta(sid, title="")
    assistant._store().upsert_meta(sid, {"created_at": started.isoformat()})

    ensure_session_titled("telegram", sid)

    meta = assistant._session_meta_entry(sid) or {}
    assert meta["title"] == connector_session_title("telegram", started), (
        "must use the session's real start time, not 'now'"
    )
    assert meta.get("channel") == "telegram"


def test_a_named_session_is_never_renamed(db_isolated):
    """Stability is the whole point — including over an operator's own rename."""
    sid = "sess-title-stable"
    assistant.upsert_session_meta(sid, title="Tax questions")
    ensure_session_titled("telegram", sid)
    assert (assistant._session_meta_entry(sid) or {})["title"] == "Tax questions"


def test_naming_is_idempotent(db_isolated):
    sid = "sess-title-idem"
    assistant.upsert_session_meta(sid, title="")
    ensure_session_titled("telegram", sid)
    first = (assistant._session_meta_entry(sid) or {})["title"]
    ensure_session_titled("telegram", sid)
    assert (assistant._session_meta_entry(sid) or {})["title"] == first


def test_a_naming_failure_never_breaks_anything(db_isolated, monkeypatch):
    monkeypatch.setattr(
        assistant, "_session_meta_entry", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    ensure_session_titled("telegram", "sess-boom")  # must not raise


# ── the live path ─────────────────────────────────────────────────────────────────────


class _Provider:
    async def stream_chat(self, messages, tools, system, model, effort="medium", **kw):
        yield {"type": "text_delta", "text": "ok"}
        yield {"type": "done", "stop_reason": "end_turn"}


class _Bot:
    def __init__(self):
        self.set_message_reaction = AsyncMock()
        self.send_chat_action = AsyncMock()

    async def send_message(self, chat_id, text, **kw):
        return SimpleNamespace(message_id=1)


@pytest.mark.asyncio
async def test_a_real_telegram_turn_names_its_conversation(db_isolated, monkeypatch):
    from backend.connectors import session_map
    from backend.connectors.telegram import handlers
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"enabled": True, "streaming": False, "suggestions": False})
    monkeypatch.setattr(handlers, "is_allowed", lambda cid: True)
    monkeypatch.setattr(handlers, "_policy_allows_turn", lambda cid: True)
    monkeypatch.setattr(assistant, "get_provider", lambda **_k: _Provider())

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=5150),
        effective_user=SimpleNamespace(username="owner", first_name="Owner"),
        message=SimpleNamespace(text="Hello", message_id=1, reply_to_message=None),
    )
    await handlers.handle_message(update, SimpleNamespace(bot=_Bot()))

    sid = session_map.get_or_create_session("telegram", "5150")
    title = (assistant._session_meta_entry(sid) or {}).get("title", "")
    assert title.startswith("Telegram • "), f"conversation was not named: {title!r}"
    assert title != "Hello", "a Telegram thread must not be named after its first message"

"""Telegram setup in one pass: paste token → Save → message the bot → Approve.

Each test pins one piece that used to break that pass:
  * tapping Start under Pairing recorded nothing, so Pending approvals stayed empty;
  * a token Telegram refuses saved as if it worked;
  * the form had no bot name to link to and no way to tell the bot was up;
  * an approved person was never told they were in.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.connectors.telegram import botapi, handlers
from backend.db.pairing_store import PairingStore
from backend.db.telegram_config_store import TelegramConfigStore


def _update(chat_id: int, text: str):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(username="ada", first_name="Ada", last_name="Lovelace"),
        message=SimpleNamespace(text=text, message_id=7, reply_text=AsyncMock()),
    )


def _bot():
    return SimpleNamespace(
        send_message=AsyncMock(),
        set_message_reaction=AsyncMock(),
        send_chat_action=AsyncMock(),
    )


@pytest.fixture
def policy(monkeypatch):
    from backend.connectors.chat import run as chat_run

    chat_run.reset_rate_limit()
    chat_run._seen.clear()
    turns = {"n": 0}

    async def _no_turn(*_a, **_k):
        turns["n"] += 1

    monkeypatch.setattr(chat_run, "process_message", _no_turn)

    def _set(name: str):
        TelegramConfigStore().update({"bot_token": "1:t", "enabled": True, "access_policy": name})
        return turns

    return _set


def _sent_texts(bot) -> list[str]:
    return [c.kwargs.get("text", "") for c in bot.send_message.call_args_list]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [handlers.handle_start, handlers.handle_help])
async def test_start_under_pairing_records_a_request_and_replies_the_code(policy, command):
    turns = policy("pairing")
    bot = _bot()
    update = _update(4242, "/start")

    await command(update, SimpleNamespace(bot=bot, args=[]))

    pending = PairingStore().list_pending("telegram")
    assert [(p["sender_id"], p["sender_label"]) for p in pending] == [
        ("4242", "Ada Lovelace (@ada)")
    ]
    replies = _sent_texts(bot)
    assert len(replies) == 1 and pending[0]["code"] in replies[0]
    assert "Connectors" in replies[0]
    update.message.reply_text.assert_not_called()  # not the old "send this number" dead end
    assert turns["n"] == 0


@pytest.mark.asyncio
async def test_start_under_allowlist_still_hands_over_the_chat_id(policy):
    policy("allowlist")
    update = _update(4242, "/start")

    await handlers.handle_start(update, SimpleNamespace(bot=_bot(), args=[]))

    assert PairingStore().list_pending("telegram") == []
    assert "4242" in update.message.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_a_plain_message_pairing_request_names_the_sender(policy):
    policy("pairing")
    bot = _bot()

    await handlers.handle_message(_update(5151, "hi"), SimpleNamespace(bot=bot))

    (row,) = PairingStore().list_pending("telegram")
    assert row["sender_label"] == "Ada Lovelace (@ada)"
    assert row["code"] in _sent_texts(bot)[0]


# ── the save endpoint ─────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ONW_DISABLE_TELEGRAM", "1")
    from backend.api.chat_pairing_api import router as pairing_router
    from backend.api.integrations import router

    app = FastAPI()
    app.include_router(router)
    app.include_router(pairing_router)
    return TestClient(app)


def _check_token_answers(monkeypatch, answer):
    async def _check(_token):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(botapi, "check_token", _check)


def test_a_token_telegram_refuses_is_not_saved(client, monkeypatch):
    _check_token_answers(monkeypatch, botapi.BotTokenRejected("Telegram didn't accept it"))

    res = client.put("/api/telegram/config", json={"bot_token": "nope", "enabled": True})

    assert res.status_code == 400
    assert "didn't accept" in res.json()["detail"]
    assert client.get("/api/telegram/config").json()["has_bot_token"] is False


def test_a_valid_token_records_the_bot_username(client, monkeypatch):
    _check_token_answers(monkeypatch, "acme_lucius_bot")

    body = client.put("/api/telegram/config", json={"bot_token": "1:ok", "enabled": True}).json()

    assert body["bot_username"] == "acme_lucius_bot"
    assert client.get("/api/telegram/config").json()["bot_username"] == "acme_lucius_bot"
    # A later save that leaves the token alone keeps the name.
    kept = client.put("/api/telegram/config", json={"access_policy": "pairing"}).json()
    assert kept["bot_username"] == "acme_lucius_bot"


def test_a_new_token_telegram_could_not_confirm_forgets_the_old_name(client, monkeypatch):
    _check_token_answers(monkeypatch, "acme_lucius_bot")
    client.put("/api/telegram/config", json={"bot_token": "1:ok", "enabled": True})
    _check_token_answers(monkeypatch, None)  # Telegram unreachable

    body = client.put("/api/telegram/config", json={"bot_token": "2:other"}).json()

    assert body["has_bot_token"] is True  # an offline save still lands
    assert body["bot_username"] == ""


def test_config_reports_whether_the_bot_is_up(client):
    assert client.get("/api/telegram/config").json()["connection"] == "off"


def test_approving_tells_the_person_they_are_in(client, monkeypatch):
    from backend.connectors.telegram import bot as telegram_bot

    TelegramConfigStore().update({"bot_token": "1:t", "enabled": True, "access_policy": "pairing"})
    PairingStore().record("telegram", "4242", sender_label="Ada")
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(telegram_bot, "send_text", sent)

    res = client.post(
        "/api/chat-pairing/approve", json={"connector": "telegram", "sender_id": "4242"}
    )

    assert res.status_code == 200 and res.json()["welcomed"] is True
    assert sent.call_args.args[0] == "4242"
    assert "You're in" in sent.call_args.args[1]
    assert "4242" in TelegramConfigStore().allowed_chat_ids()


def test_an_undeliverable_welcome_never_fails_the_approval(client, monkeypatch):
    from backend.connectors.telegram import bot as telegram_bot

    TelegramConfigStore().update({"bot_token": "1:t", "enabled": True, "access_policy": "pairing"})
    PairingStore().record("telegram", "4242")
    monkeypatch.setattr(telegram_bot, "send_text", AsyncMock(return_value=False))

    res = client.post(
        "/api/chat-pairing/approve", json={"connector": "telegram", "sender_id": "4242"}
    )

    assert res.status_code == 200 and res.json()["welcomed"] is False
    assert "4242" in TelegramConfigStore().allowed_chat_ids()


# ── the check itself ──────────────────────────────────────────────────────────


def _fake_bot(monkeypatch, *, username="acme_lucius_bot", raises=None):
    seen = {}

    class _Bot:
        def __init__(self, token, **urls):
            seen.update(token=token, **urls)
            self.username = username

        async def __aenter__(self):
            if raises:
                raise raises
            return self

        async def __aexit__(self, *_exc):
            return False

    import telegram

    monkeypatch.setattr(telegram, "Bot", _Bot)
    return seen


@pytest.mark.real_telegram_token_check
@pytest.mark.asyncio
async def test_check_token_returns_the_bots_username_from_the_configured_api(monkeypatch):
    seen = _fake_bot(monkeypatch)
    monkeypatch.setattr(botapi, "API_BASE", "http://127.0.0.1:1")

    assert await botapi.check_token("1:ok") == "acme_lucius_bot"
    assert seen["base_url"] == "http://127.0.0.1:1/bot"


@pytest.mark.real_telegram_token_check
@pytest.mark.asyncio
async def test_check_token_refuses_what_telegram_refuses(monkeypatch):
    from telegram.error import InvalidToken

    _fake_bot(monkeypatch, raises=InvalidToken("Not Found"))

    with pytest.raises(botapi.BotTokenRejected):
        await botapi.check_token("bad")


@pytest.mark.real_telegram_token_check
@pytest.mark.asyncio
async def test_check_token_treats_an_unreachable_telegram_as_unknown(monkeypatch):
    from telegram.error import NetworkError

    _fake_bot(monkeypatch, raises=NetworkError("down"))

    assert await botapi.check_token("1:ok") is None

"""Approved chat IDs show who they are: read live from Telegram, never stored."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.connectors.telegram import bot as telegram_bot
from backend.connectors.telegram import chat_names
from backend.db.telegram_config_store import TelegramConfigStore

ADA = SimpleNamespace(first_name="Ada", last_name="Lovelace", username="ada")
GROUP = SimpleNamespace(first_name=None, last_name=None, username=None, title="ACME family")


@pytest.fixture(autouse=True)
def _fresh_cache():
    chat_names._cache.clear()
    chat_names._cache_bot = None
    yield
    chat_names._cache.clear()


def _bot(chats: dict[int, object]):
    async def get_chat(chat_id):
        if chat_id not in chats:
            raise RuntimeError("Chat not found")
        return chats[chat_id]

    return SimpleNamespace(get_chat=AsyncMock(side_effect=get_chat))


@pytest.mark.parametrize(
    ("who", "label"),
    [
        (ADA, "Ada Lovelace (@ada)"),
        (SimpleNamespace(first_name="Ada", last_name="", username=""), "Ada"),
        (SimpleNamespace(first_name="", last_name="", username="ada"), "@ada"),
        (GROUP, "ACME family"),
        (None, ""),
    ],
)
def test_display_label(who, label):
    assert chat_names.display_label(who) == label


async def test_names_what_telegram_knows_and_leaves_out_the_rest():
    bot = _bot({111: ADA, -100222: GROUP})
    labels = await chat_names.chat_labels(bot, ["111", "-100222", "333", "not-an-id"])
    assert labels == {"111": "Ada Lovelace (@ada)", "-100222": "ACME family"}


async def test_a_second_read_is_served_from_memory():
    bot = _bot({111: ADA})
    await chat_names.chat_labels(bot, ["111"])
    await chat_names.chat_labels(bot, ["111"])
    assert bot.get_chat.await_count == 1


async def test_a_different_bot_does_not_reuse_the_old_bots_names():
    await chat_names.chat_labels(_bot({111: ADA}), ["111"])
    assert await chat_names.chat_labels(_bot({}), ["111"]) == {}


async def test_no_running_bot_names_nothing():
    assert await chat_names.chat_labels(None, ["111"]) == {}


def test_the_endpoint_names_the_saved_allowlist(monkeypatch):
    from backend.api.integrations import router

    TelegramConfigStore().update({"bot_token": "1:t", "allowed_chat_ids": "111, 333"})
    monkeypatch.setattr(telegram_bot, "_app", SimpleNamespace(bot=_bot({111: ADA})))
    app = FastAPI()
    app.include_router(router)

    body = TestClient(app).get("/api/telegram/chats").json()

    assert body == {"labels": {"111": "Ada Lovelace (@ada)"}}
    stored = TelegramConfigStore().public()
    assert "Ada" not in repr(stored)


def test_the_endpoint_names_ids_on_screen_before_they_are_saved(monkeypatch):
    from backend.api.integrations import router

    TelegramConfigStore().update({"bot_token": "1:t", "allowed_chat_ids": ""})
    monkeypatch.setattr(telegram_bot, "_app", SimpleNamespace(bot=_bot({111: ADA})))
    app = FastAPI()
    app.include_router(router)

    body = TestClient(app).get("/api/telegram/chats", params={"ids": "111,abc"}).json()

    assert body == {"labels": {"111": "Ada Lovelace (@ada)"}}


def test_changing_who_can_chat_does_not_restart_the_bot(monkeypatch):
    from backend.api.integrations import router

    TelegramConfigStore().update({"bot_token": "1:t", "enabled": True, "allowed_chat_ids": "111"})
    reload = AsyncMock(return_value=True)
    monkeypatch.setattr(telegram_bot, "reload_best_effort", reload)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    client.put(
        "/api/telegram/config",
        json={"enabled": True, "allowed_chat_ids": "111, 222", "access_policy": "allowlist"},
    )
    assert reload.await_count == 0
    assert TelegramConfigStore().allowed_chat_ids() == ["111", "222"]

    client.put("/api/telegram/config", json={"enabled": False})
    assert reload.await_count == 1

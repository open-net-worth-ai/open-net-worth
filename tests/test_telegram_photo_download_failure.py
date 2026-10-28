"""A Telegram photo whose download fails must never vanish silently.

Owner report (2026-10-28): a receipt photo got the 👀 reaction and then nothing. ``get_file``
timed out, the call sat outside every ``try``, and the bot had no error handler — so the
exception went to stderr only, no attachment or user message was stored, and the follow-up
"?" reached Lucius with no context. A transient timeout now retries once; a second failure
tells the sender to resend; and any handler exception lands in app.log's ``backend`` logger.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import TimedOut

from backend.connectors.telegram import bot as tg_bot
from backend.connectors.telegram import handlers


class _Bot:
    def __init__(self, get_file_effects):
        self.set_message_reaction = AsyncMock()
        self.send_chat_action = AsyncMock()
        self.get_file = AsyncMock(side_effect=get_file_effects)
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return SimpleNamespace(message_id=len(self.sent))


def _file():
    return SimpleNamespace(download_as_bytearray=AsyncMock(return_value=b"\x89PNG"))


def _photo_update():
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=4242),
        effective_user=SimpleNamespace(username="owner", first_name="Owner"),
        message=SimpleNamespace(
            photo=[SimpleNamespace(file_id="f1", file_unique_id="u1", file_size=10)],
            caption="Find this transaction and attach",
            message_id=508,
            media_group_id=None,
            reply_to_message=None,
        ),
    )


@pytest.fixture
def tg(db_isolated, monkeypatch):
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"enabled": True, "streaming": False, "suggestions": False})
    monkeypatch.setattr(handlers, "_policy_allows_turn", lambda cid: True)
    monkeypatch.setattr(handlers, "_DOWNLOAD_RETRY_DELAY", 0)
    seen: list[dict] = []

    async def _spy(connector, channel_id, session_id, text, effort_default="high", **kw):
        seen.append({"text": text, "attachment_refs": kw.get("attachment_refs")})

    monkeypatch.setattr(handlers, "process_message", _spy)
    yield seen


@pytest.mark.asyncio
async def test_a_timed_out_photo_download_is_retried_once(tg):
    bot = _Bot([TimedOut(), _file()])

    await handlers.handle_photo(_photo_update(), SimpleNamespace(bot=bot))

    assert bot.get_file.await_count == 2
    assert len(tg) == 1, "the retried photo never reached Lucius"
    assert tg[0]["text"] == "Find this transaction and attach"
    assert tg[0]["attachment_refs"] and tg[0]["attachment_refs"][0].startswith("att_")


@pytest.mark.asyncio
async def test_a_photo_that_cannot_be_downloaded_tells_the_sender_to_resend(tg):
    bot = _Bot([TimedOut(), TimedOut()])

    await handlers.handle_photo(_photo_update(), SimpleNamespace(bot=bot))

    assert tg == [], "no turn may start without the photo"
    assert len(bot.sent) == 1
    assert "send it again" in bot.sent[0]


@pytest.mark.asyncio
async def test_handler_errors_are_logged_under_the_backend_logger(caplog):
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        err = exc
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=4242))

    with caplog.at_level(logging.ERROR, logger="backend"):
        await handlers.handle_error(update, SimpleNamespace(error=err))

    rec = next(r for r in caplog.records if r.name.startswith("backend.connectors.telegram"))
    assert rec.exc_info and rec.exc_info[1] is err
    assert "4242" in rec.getMessage()


def test_the_bot_registers_the_error_handler_and_logs_into_app_log():
    app = tg_bot._build_app("123456:TEST-token")

    assert handlers.handle_error in app.error_handlers
    assert tg_bot.log.name.startswith("backend.")

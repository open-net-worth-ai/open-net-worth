"""Live tool activity on a chat channel, behind a per-channel toggle.

Owner, 2026-08-23: *"do the tool call streaming too, but keep it behind a config."*

Telegram showed only `typing…` for a whole turn: `pump_channel_turn` handled six event
types and `tool_use_start` was not one of them, so an eight-tool answer looked identical to
a stall. The web client has drawn these all along (`chatReducer.ts` handles `tool_use_start`
/ `tool_done`); the connector simply dropped them.

Design notes worth pinning:

  * a SEPARATE message from the prose stream — the two interleave (talk, call tools, talk
    again), and editing one message for both would make the answer flicker;
  * DELETED the moment real output lands, so the finished conversation reads exactly as it
    did before this existed;
  * the label is `display.label`, resolved server-side in `ai/tools/display.py`, so the
    phone and the web app share one vocabulary rather than each inventing its own;
  * asked of the ADAPTER (duck-typed, like `supports_streaming`), so the pump stays
    channel-neutral and a connector without the toggle simply opts out.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import backend.ai.assistant as assistant
from backend.connectors.telegram import handlers
from backend.db.telegram_config_store import TelegramConfigStore


class _ToolProvider:
    """One round that calls two tools, then a plain-text answer."""

    def __init__(self):
        self.calls = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium", **kw):
        idx = self.calls
        self.calls += 1
        if idx == 0:
            yield {"type": "tool_use_start", "id": "t1", "name": "get_net_worth", "input": {}}
            yield {"type": "tool_use", "id": "t1", "name": "get_net_worth", "input": {}}
            yield {"type": "done", "stop_reason": "tool_use"}
        elif idx == 1:
            yield {"type": "tool_use_start", "id": "t2", "name": "list_accounts", "input": {}}
            yield {"type": "tool_use", "id": "t2", "name": "list_accounts", "input": {}}
            yield {"type": "done", "stop_reason": "tool_use"}
        else:
            yield {"type": "text_delta", "text": "Your net worth is SGD 1."}
            yield {"type": "done", "stop_reason": "end_turn"}


class _Bot:
    """Records sends, edits and deletes so the status message is observable."""

    def __init__(self):
        self.set_message_reaction = AsyncMock()
        self.send_chat_action = AsyncMock()
        self.sent: list[dict] = []
        self.edits: list[str] = []
        self.deleted: list[int] = []
        self.token = ""  # no usable token -> the rich path falls back, never networks

    async def send_message(self, chat_id, text, **kw):
        self.sent.append({"text": text, **kw})
        return SimpleNamespace(message_id=len(self.sent))

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        self.edits.append(text)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(int(message_id))


def _update(chat_id: int, text: str):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(username="owner", first_name="Owner"),
        message=SimpleNamespace(text=text, message_id=1, reply_to_message=None),
    )


@pytest.fixture
def tg(db_isolated, monkeypatch):
    TelegramConfigStore().update({"enabled": True, "streaming": True, "suggestions": False})
    monkeypatch.setattr(handlers, "is_allowed", lambda cid: True)
    monkeypatch.setattr(handlers, "_policy_allows_turn", lambda cid: True)
    monkeypatch.setattr(assistant, "get_provider", lambda **_k: _ToolProvider())

    async def _fake_execute(name, inp, *a, **k):
        return '{"ok": true}', None

    monkeypatch.setattr(assistant, "execute_tool", _fake_execute)
    yield


def test_the_toggle_defaults_on_and_round_trips(db_isolated):
    store = TelegramConfigStore()
    assert store.tool_stream_enabled() is True
    store.set_tool_stream(False)
    assert store.tool_stream_enabled() is False
    store.set_tool_stream(True)
    assert store.tool_stream_enabled() is True


@pytest.mark.asyncio
async def test_tool_activity_is_shown_then_cleared(tg):
    bot = _Bot()
    await handlers.handle_message(_update(9301, "what's my net worth?"), SimpleNamespace(bot=bot))

    shown = "\n".join([m["text"] for m in bot.sent] + bot.edits)
    assert "get_net_worth" in shown or "net worth" in shown.lower(), (
        f"the first tool call was never surfaced; sent={[m['text'][:60] for m in bot.sent]}"
    )
    assert bot.deleted, "the status message must be removed once the answer lands"
    final = bot.sent[-1]["text"]
    assert "Your net worth is SGD 1." in final or "SGD 1" in final


@pytest.mark.asyncio
async def test_with_the_toggle_OFF_nothing_extra_is_sent(tg):
    TelegramConfigStore().set_tool_stream(False)
    bot = _Bot()
    await handlers.handle_message(_update(9302, "what's my net worth?"), SimpleNamespace(bot=bot))

    assert not bot.deleted, "no status message should have been created to delete"
    assert not any("⚙️" in m["text"] for m in bot.sent), "no tool activity may be shown"
    assert any("SGD 1" in m["text"] for m in bot.sent), "the answer still arrives"


@pytest.mark.asyncio
async def test_a_connector_without_the_toggle_opts_out(tg):
    """The pump is channel-neutral: an adapter with no ``tool_stream_enabled`` simply
    doesn't get the feature, rather than erroring or defaulting it on."""
    from backend.connectors import processor

    class _Bare:
        connector = "bare"
        supports_streaming = True

    assert processor._tool_stream_on(_Bare()) is False

    class _Broken:
        connector = "broken"

        def tool_stream_enabled(self):
            raise RuntimeError("config unreadable")

    assert processor._tool_stream_on(_Broken()) is False, "a config failure must not raise"


def test_toolstream_is_in_the_telegram_command_menu():
    """`BOT_COMMANDS` is what `set_my_commands` publishes — the Menu button and the
    autocomplete list. A command handler that is registered but missing here works only for
    someone who already knows to type it, which is nobody. Missed on the first pass (owner
    caught it), so it is pinned."""
    from backend.connectors.telegram.handlers import BOT_COMMANDS

    names = [c for c, _ in BOT_COMMANDS]
    assert "toolstream" in names, f"/toolstream is not in the Telegram menu: {names}"


def test_every_menu_command_has_a_registered_handler():
    """The other half of the same mistake: a menu entry with no handler is a dead button."""
    import inspect

    from backend.connectors.telegram import handlers as h
    from backend.connectors.telegram.handlers import BOT_COMMANDS

    for name, _desc in BOT_COMMANDS:
        fn = getattr(h, f"handle_{name}", None)
        assert fn is not None and inspect.iscoroutinefunction(fn), (
            f"/{name} is offered in the menu but has no handle_{name} coroutine"
        )

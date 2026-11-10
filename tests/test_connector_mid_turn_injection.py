"""A message sent while a connector turn is running joins THAT turn, as it does on web.

Owner, 2026-08-23: *"let's do mid turn messages support -> basically it's supporting
multiple message, right like web?"*

Before this, the connector path had no running-turn check at all — `executing_turn_id`
appears in `api/chat.py` and nowhere under `connectors/`. A second Telegram message hit the
per-session `asyncio.Lock` in `assistant.run`, waited, and then ran as its OWN turn
afterwards. Nothing was lost, but a correction ("no, do X instead") could never reach the
turn it was correcting: by the time it ran, the first turn had already finished doing the
thing.

Routing rules mirrored from `api/chat.py` exactly, because both feed the SAME queue and the
SAME drain (`assistant._drain_injected_messages`, keyed by session id):

  * gated on the same `chat_multi_turn_enabled` profile flag — the DRAIN is gated on it
    (`assistant.py`), so enqueuing without it would be a black hole;
  * keyed by SESSION, never by channel — an untrusted shared channel keys sessions
    per-sender (`channel_id:sender_id`), so a channel-keyed check would queue one sender's
    message onto another sender's session;
  * re-checked after enqueueing, so a turn that ends in the gap still gets its message
    delivered rather than leaving it stranded.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import backend.ai.assistant as assistant
from backend.ai import pending_injection
from backend.connectors import turn_registry
from backend.connectors.telegram import handlers


class _Bot:
    def __init__(self):
        self.set_message_reaction = AsyncMock()
        self.send_chat_action = AsyncMock()
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return SimpleNamespace(message_id=len(self.sent))


def _update(chat_id: int, text: str, mid: int = 1):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(username="owner", first_name="Owner"),
        message=SimpleNamespace(text=text, message_id=mid, reply_to_message=None),
    )


@pytest.fixture
def tg(db_isolated, monkeypatch):
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"enabled": True, "streaming": False, "suggestions": False})
    monkeypatch.setattr(handlers, "is_allowed", lambda cid: True)
    monkeypatch.setattr(handlers, "_policy_allows_turn", lambda cid: True)
    pending_injection._reset_for_tests()
    turn_registry.reset()
    yield
    pending_injection._reset_for_tests()
    turn_registry.reset()


def _multi_turn(monkeypatch, enabled: bool) -> None:
    from backend.services import profile as profile_service

    real = profile_service.read_profile

    def _patched():
        return {**real(), "chat_multi_turn_enabled": enabled}

    monkeypatch.setattr(profile_service, "read_profile", _patched)


def _session_for(chat_id: int) -> str:
    from backend.connectors import session_map

    return session_map.get_or_create_session("telegram", str(chat_id))


@pytest.mark.asyncio
async def test_a_message_sent_mid_turn_is_queued_not_spawned(tg, monkeypatch):
    _multi_turn(monkeypatch, True)
    chat_id = 7101
    sid = _session_for(chat_id)

    started = asyncio.Event()
    release = asyncio.Event()
    turns_run: list[str] = []

    async def _slow_process(connector, channel_id, session_id, text, *a, **k):
        turns_run.append(text)
        turn_registry.register_active(
            "telegram", channel_id, asyncio.current_task(), session_id=session_id
        )
        started.set()
        try:
            await release.wait()
        finally:
            turn_registry.clear_active(
                "telegram", channel_id, asyncio.current_task(), session_id=session_id
            )

    from backend.connectors.chat import run as chat_run

    monkeypatch.setattr(chat_run, "process_message", _slow_process)

    bot = _Bot()
    first = asyncio.create_task(
        handlers.handle_message(_update(chat_id, "summarise my spending"), SimpleNamespace(bot=bot))
    )
    await started.wait()

    # ...second message arrives while the first turn is still running. Bounded: on the
    # pre-fix code this call BLOCKS inside the stubbed turn instead of returning, so a bare
    # await would hang the suite rather than fail it.
    try:
        await asyncio.wait_for(
            handlers.handle_message(
                _update(chat_id, "actually just June", 2), SimpleNamespace(bot=bot)
            ),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        release.set()
        await first
        pytest.fail("the follow-up spawned a second turn instead of being queued")

    queued = pending_injection.drain(sid)
    assert [q.text for q in queued] == ["actually just June"], (
        f"the follow-up must join the running turn, not start a second one; ran={turns_run}"
    )
    assert turns_run == ["summarise my spending"], "no second turn may spawn"

    release.set()
    await first


@pytest.mark.asyncio
async def test_with_multi_turn_OFF_the_old_behaviour_is_kept(tg, monkeypatch):
    """The DRAIN is gated on the same flag, so enqueuing while it is off would strand the
    message with nothing to consume it. Off means: queue nothing, behave as before."""
    _multi_turn(monkeypatch, False)
    chat_id = 7102
    sid = _session_for(chat_id)

    started = asyncio.Event()
    release = asyncio.Event()
    turns_run: list[str] = []

    async def _slow_process(connector, channel_id, session_id, text, *a, **k):
        turns_run.append(text)
        turn_registry.register_active(
            "telegram", channel_id, asyncio.current_task(), session_id=session_id
        )
        started.set()
        try:
            await release.wait()
        finally:
            turn_registry.clear_active(
                "telegram", channel_id, asyncio.current_task(), session_id=session_id
            )

    from backend.connectors.chat import run as chat_run

    monkeypatch.setattr(chat_run, "process_message", _slow_process)

    bot = _Bot()
    first = asyncio.create_task(
        handlers.handle_message(_update(chat_id, "one"), SimpleNamespace(bot=bot))
    )
    await started.wait()
    second = asyncio.create_task(
        handlers.handle_message(_update(chat_id, "two", 2), SimpleNamespace(bot=bot))
    )
    await asyncio.sleep(0)

    assert pending_injection.drain(sid) == [], "nothing may be queued while the drain is off"

    release.set()
    await first
    await second
    assert turns_run == ["one", "two"], "the second message still runs, as its own turn"


@pytest.mark.asyncio
async def test_an_idle_session_starts_a_normal_turn(tg, monkeypatch):
    _multi_turn(monkeypatch, True)
    chat_id = 7103
    sid = _session_for(chat_id)
    ran: list[str] = []

    async def _process(connector, channel_id, session_id, text, *a, **k):
        ran.append(text)

    from backend.connectors.chat import run as chat_run

    monkeypatch.setattr(chat_run, "process_message", _process)

    await handlers.handle_message(_update(chat_id, "hello"), SimpleNamespace(bot=_Bot()))
    assert ran == ["hello"]
    assert pending_injection.drain(sid) == []


def test_the_running_check_is_keyed_by_SESSION_not_by_channel():
    """An untrusted shared channel keys sessions per-sender, so two senders share a
    channel_id but hold different sessions. A channel-keyed check would queue sender B's
    message onto sender A's running turn — into a session with nothing to drain it."""
    turn_registry.reset()

    class _T:
        def done(self):
            return False

    task = _T()
    turn_registry.register_active("slack", "C123", task, session_id="C123:userA")

    assert turn_registry.is_running_for_session("C123:userA") is True
    assert turn_registry.is_running_for_session("C123:userB") is False, (
        "sender B has their own session and no running turn"
    )
    turn_registry.reset()


@pytest.mark.asyncio
async def test_end_to_end_a_follow_up_joins_the_running_turn(tg, monkeypatch):
    """The REAL pump, not a stubbed `process_message`.

    Proves the production wiring: `pump_channel_turn` registers the turn under its SESSION
    id, so the router can see it. Without that registration the routing check is dead code
    in production while every stubbed test still passes.
    """
    _multi_turn(monkeypatch, True)
    chat_id = 7104
    sid = _session_for(chat_id)

    seen_by_model: list[str] = []
    gate = asyncio.Event()

    class _Provider:
        def __init__(self):
            self.calls = 0

        async def stream_chat(self, messages, tools, system, model, effort="medium", **kw):
            self.calls += 1
            for m in messages:
                c = m.get("content")
                if m.get("role") == "user":
                    text = (
                        c
                        if isinstance(c, str)
                        else "".join(
                            b.get("text", "")
                            for b in c
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    )
                    seen_by_model.append(text)
            if self.calls == 1:
                await gate.wait()  # hold the turn open so a second message can arrive
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "stop_reason": "end_turn"}

    monkeypatch.setattr(assistant, "get_provider", lambda **_k: _Provider())

    bot = _Bot()
    first = asyncio.create_task(
        handlers.handle_message(_update(chat_id, "summarise my spending"), SimpleNamespace(bot=bot))
    )
    for _ in range(200):  # let the turn reach the provider and register itself
        await asyncio.sleep(0.01)
        if turn_registry.is_running_for_session(sid):
            break
    assert turn_registry.is_running_for_session(sid), "the real pump never registered the session"

    await asyncio.wait_for(
        handlers.handle_message(
            _update(chat_id, "actually just June", 2), SimpleNamespace(bot=bot)
        ),
        timeout=5.0,
    )
    assert [q.text for q in pending_injection.drain(sid)] == ["actually just June"]

    gate.set()
    await first

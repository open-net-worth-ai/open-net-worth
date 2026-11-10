"""profile.chat_multi_turn_enabled — default OFF ("one turn only", owner 2026-08-10).

Two enforcement layers:
1. api/chat.py refuses (409) a message sent while a turn is running, when OFF —
   never enqueues it. The frontend disables Send while busy so this is a backstop, not
   the primary UX.
2. assistant.py's own mid-turn drain is ALSO gated (redundant once (1) holds, since
   nothing reaches the queue while OFF, but kept as belt-and-braces).

Never queuing anything while OFF is what makes this safe without any auto-delivery
mechanism — there's nothing left for a later turn to forget to pick back up. ADR
docs/adr/2026-08-10-multi-turn-chat-defaults-off.md.

test_post_while_not_live_spawns_normally_and_folds_in_residue in
test_mid_turn_injection.py exercises the residue-recovery path (ON path) directly; this
file pins both layers of the OFF path. Same hermetic harness: scripted provider stubs,
no network.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.pending_injection as pending_injection
import backend.ai.turns as turns
import backend.api.chat as chat_mod
import backend.services.profile as profile_service
from backend.api.chat import ChatRequest
from tests._asgi import make_request


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch, db_isolated):
    # Sessions + PendingPlanStore are DB-backed now (Wave 5, JSON->DB) — db_isolated
    # isolates both tables; no more _sessions_dir / _path files to monkeypatch.
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)

    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)
    yield
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()


def _cleanup(sid: str) -> None:
    assistant.SESSIONS.pop(sid, None)
    assistant._SESSION_LOCKS.pop(sid, None)


async def _drain(gen):
    events = []
    async for ev in gen:
        events.append(ev)
    return events


class _TextThenTextProvider:
    """Every round answers with plain text and would normally end the turn."""

    def __init__(self):
        self.calls = 0

    async def stream_chat(self, *a, **kw):
        self.calls += 1
        yield {"type": "text_delta", "text": f"round{self.calls}"}
        yield {"type": "done", "stop_reason": "end_turn"}


async def test_multi_turn_off_leaves_the_queue_untouched_and_ends_the_turn_normally(
    monkeypatch,
):
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _TextThenTextProvider())
    monkeypatch.setattr(profile_service, "read_profile", lambda: {"chat_multi_turn_enabled": False})

    sid = "test-mt-off"
    _cleanup(sid)
    pending_injection.enqueue(sid, "client-1", "one more thing")
    try:
        events = await _drain(assistant.run(sid, [{"role": "user", "content": "hi"}], "s"))
    finally:
        _cleanup(sid)

    # No drain happened: no message_delivered, no second round, one clean turn_done.
    assert [e for e in events if e.get("type") == "message_delivered"] == []
    turn_done = [e for e in events if e.get("type") == "turn_done"]
    assert len(turn_done) == 1

    history = assistant.get_or_create_session(sid)[1]
    assistant_entries = [h for h in history if h["role"] == "assistant"]
    assert len(assistant_entries) == 1, "OFF must not continue the turn for the queue"
    # Compared field-by-field, not by dict equality: every persisted message now also
    # carries a durable `ts` (see assistant._stamp_message_times), so an exact-shape
    # assertion pins a field this test was never about.
    assert assistant_entries[0]["role"] == "assistant"
    assert assistant_entries[0]["content"] == [{"type": "text", "text": "round1"}]

    # The message is still sitting in the queue — untouched, not lost. `drain` is
    # destructive, so this is the LAST assertion in the test.
    assert pending_injection.has_pending(sid) is True
    queued = pending_injection.drain(sid)
    assert [m.text for m in queued] == ["one more thing"]


async def test_multi_turn_on_still_drains_mid_turn_c3(monkeypatch):
    """Same scenario, setting ON: restores the original acceptance-C3 behavior
    (test_mid_turn_injection.py's own C3 test) — pinned again here so a regression in the
    NEW gate can't silently disable the OLD behavior for anyone who has it on."""
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _TextThenTextProvider())
    monkeypatch.setattr(profile_service, "read_profile", lambda: {"chat_multi_turn_enabled": True})

    sid = "test-mt-on"
    _cleanup(sid)
    pending_injection.enqueue(sid, "client-2", "one more thing")
    try:
        events = await _drain(assistant.run(sid, [{"role": "user", "content": "hi"}], "s"))
    finally:
        _cleanup(sid)

    delivered = [e for e in events if e.get("type") == "message_delivered"]
    assert len(delivered) == 1 and delivered[0]["message_id"] == "client-2"
    turn_done = [e for e in events if e.get("type") == "turn_done"]
    assert len(turn_done) == 1, "the turn must finalize exactly once, not per round"

    history = assistant.get_or_create_session(sid)[1]
    assistant_entries = [h for h in history if h["role"] == "assistant"]
    assert len(assistant_entries) == 2, "the queue must continue the SAME turn for another round"


async def test_defaults_to_multi_turn_disabled_when_the_profile_key_is_absent(monkeypatch):
    """A profile saved before this setting existed has no key for it at all — must
    default OFF (the real default, api/profile.py's DEFAULT_PROFILE), not crash and not
    silently drain a queue the frontend never should have let form in the first place."""
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _TextThenTextProvider())
    monkeypatch.setattr(profile_service, "read_profile", lambda: {})  # key absent entirely

    sid = "test-mt-default"
    _cleanup(sid)
    pending_injection.enqueue(sid, "client-3", "one more thing")
    try:
        events = await _drain(assistant.run(sid, [{"role": "user", "content": "hi"}], "s"))
    finally:
        _cleanup(sid)

    assert [e for e in events if e.get("type") == "message_delivered"] == []
    history = assistant.get_or_create_session(sid)[1]
    assert len([h for h in history if h["role"] == "assistant"]) == 1
    assert pending_injection.has_pending(sid) is True


async def test_post_while_busy_is_refused_and_never_enqueued_when_off(monkeypatch):
    """The HTTP-level backstop (defense in depth — the frontend disables Send before
    this is normally reached): POST /api/chat while a turn is running gets a 409, and
    the message is never handed to pending_injection at all."""
    monkeypatch.setattr(profile_service, "read_profile", lambda: {"chat_multi_turn_enabled": False})

    reached = asyncio.Event()
    proceed = asyncio.Event()

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        reached.set()
        await proceed.wait()
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)

    sid = "test-mt-refused"
    req1 = ChatRequest(session_id=sid, messages=[{"role": "user", "content": "hi"}])
    resp1 = await chat_mod.chat(req1, make_request())
    assert resp1.status_code == 202
    await asyncio.wait_for(reached.wait(), timeout=1)

    req2 = ChatRequest(
        session_id=sid,
        messages=[{"role": "user", "content": "actually also check the budget"}],
        client_message_id="client-refused-1",
    )
    with pytest.raises(HTTPException) as exc_info:
        await chat_mod.chat(req2, make_request())
    assert exc_info.value.status_code == 409
    assert pending_injection.has_pending(sid) is False

    proceed.set()
    assert await turns.adrain_for_tests(sid, timeout=1)

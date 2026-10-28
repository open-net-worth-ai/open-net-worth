"""A turn's ``thinking_delta`` content must survive into the persisted transcript.

Regression: ``assistant.run``'s turn-finalization only ever appended a ``text`` content
block from ``assistant_text`` — ``thinking_delta`` events were forwarded live (so a
streaming client could show them for that one turn) and then unconditionally discarded,
never reaching ``history``/storage. Real on this instance's own claude_cli sessions
(zero ``thinking``-type blocks across a real 17-message transcript despite genuine,
billed thinking tokens) — provider-agnostic, not specific to that transport. Same
hermetic harness as test_chat_multi_turn_enabled.py: a scripted provider stub, no
network.
"""

from __future__ import annotations

import pytest

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.pending_injection as pending_injection
import backend.ai.turns as turns
import backend.services.profile as profile_service


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, db_isolated):
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(profile_service, "read_profile", lambda: {"chat_multi_turn_enabled": False})

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


class _ThinkingThenTextProvider:
    """One round: thinking deltas, then a text answer, then done."""

    async def stream_chat(self, *a, **kw):
        yield {"type": "thinking_delta", "text": "Let me work through "}
        yield {"type": "thinking_delta", "text": "this step by step."}
        yield {"type": "text_delta", "text": "Here's the answer."}
        yield {"type": "done", "stop_reason": "end_turn"}


class _TextOnlyProvider:
    """No thinking at all — the no-op case must not append an empty block."""

    async def stream_chat(self, *a, **kw):
        yield {"type": "text_delta", "text": "plain answer"}
        yield {"type": "done", "stop_reason": "end_turn"}


async def test_thinking_delta_is_persisted_as_a_thinking_content_block(monkeypatch):
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _ThinkingThenTextProvider())

    sid = "test-thinking-persist"
    _cleanup(sid)
    try:
        events = await _drain(assistant.run(sid, [{"role": "user", "content": "hi"}], "s"))
    finally:
        pass

    # The live stream still carries thinking_delta for a client watching in real time.
    assert [e["text"] for e in events if e.get("type") == "thinking_delta"] == [
        "Let me work through ",
        "this step by step.",
    ]

    history = assistant.get_or_create_session(sid)[1]
    assistant_entries = [h for h in history if h["role"] == "assistant"]
    assert len(assistant_entries) == 1
    content = assistant_entries[0]["content"]

    # Thinking precedes text, matching the real API's own content-block ordering —
    # and it must be the ACCUMULATED thinking text, not dropped or partial.
    assert content == [
        {"type": "thinking", "thinking": "Let me work through this step by step."},
        {"type": "text", "text": "Here's the answer."},
    ]
    _cleanup(sid)


async def test_no_thinking_appends_no_thinking_block(monkeypatch):
    """A turn with no thinking_delta events must not grow a spurious empty block."""
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _TextOnlyProvider())

    sid = "test-no-thinking"
    _cleanup(sid)
    try:
        await _drain(assistant.run(sid, [{"role": "user", "content": "hi"}], "s"))
    finally:
        pass

    history = assistant.get_or_create_session(sid)[1]
    assistant_entries = [h for h in history if h["role"] == "assistant"]
    assert assistant_entries[0]["content"] == [{"type": "text", "text": "plain answer"}]
    _cleanup(sid)

"""A turn ends for a confirm card only when a card was emitted, never on a ``propose_`` name.

``propose_paper_orders`` records a paper Autopilot review's proposals and shows no card. The
loop used to end any turn that called a ``propose_*`` tool, so every review stopped before it
wrote its memo (2026-11-22). Hermetic: a scripted provider and a faked ``execute_tool``.
"""

from __future__ import annotations

import json

import pytest

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.turns as turns
import backend.services.profile as profile_service


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, db_isolated):
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(profile_service, "read_profile", lambda: {})
    monkeypatch.setattr(
        assistant,
        "_active_tools",
        lambda: [
            *assistant.TOOL_SCHEMAS,
            {"name": "propose_paper_orders", "input_schema": {"type": "object"}},
        ],
    )
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(False)
    yield
    live_sessions._reset_for_tests()
    turns._turns.clear()
    turns._by_session.clear()


class _Provider:
    def __init__(self):
        self.calls = 0

    async def stream_chat(self, *a, **kw):
        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "tool_use",
                "id": "t0",
                "name": "propose_paper_orders",
                "input": {"proposals": []},
            }
            yield {"type": "done", "stop_reason": "tool_use"}
        else:
            yield {"type": "text_delta", "text": "now the memo"}
            yield {"type": "done", "stop_reason": "end_turn"}


async def test_a_propose_tool_with_no_card_keeps_the_turn_going(monkeypatch):
    provider = _Provider()

    async def _execute(name, args, tool_use_id=None):
        return json.dumps({"ok": True, "proposals": []}), None

    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(assistant, "execute_tool", _execute)

    sid = "test-propose-no-card"
    try:
        events = [
            ev async for ev in assistant.run(sid, [{"role": "user", "content": "review"}], "s")
        ]
    finally:
        assistant.SESSIONS.pop(sid, None)
        assistant._SESSION_LOCKS.pop(sid, None)

    assert provider.calls == 2, "the turn ended on the propose_ name instead of continuing"
    assert not any(e.get("type") == "proposed_change" for e in events)
    assert any(
        e.get("type") == "text_delta" and "now the memo" in e.get("text", "") for e in events
    )

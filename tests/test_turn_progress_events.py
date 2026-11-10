"""``turn_progress`` — the live output-token total behind the working indicator.

The chat panel shows "↓ 1.4k tokens" while a turn is running. That number has to be the
SERVER's accumulated figure: the alternative considered was estimating it client-side from
streamed characters, which renders a guess in the same place, same format, as a
measurement — with nothing to tell the reader which one they are looking at.

Providers report usage once per model round, so this is one small event per round, not per
delta. These tests pin the contract the frontend reads (`chatReducer.turnProgress.test.ts`
covers the other side).

Hermetic: scripted provider stubs, no network, no LLM — same pattern as
``tests/test_mid_turn_injection.py``.
"""

from __future__ import annotations

import pytest

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.pending_injection as pending_injection
import backend.ai.turns as turns
import backend.services.profile as profile_service


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(profile_service, "read_profile", lambda: {"chat_multi_turn_enabled": True})

    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)


def _cleanup(sid: str) -> None:
    assistant.SESSIONS.pop(sid, None)
    assistant._SESSION_LOCKS.pop(sid, None)


async def _drain(gen):
    events = []
    async for ev in gen:
        events.append(ev)
    return events


class _UsageReportingProvider:
    """Reports usage the way a real provider does: once at the end of each round."""

    def __init__(self, per_round_output: list[int]):
        self._per_round = list(per_round_output)
        self.calls = 0

    async def stream_chat(self, *a, **kw):
        out = self._per_round[self.calls] if self.calls < len(self._per_round) else 0
        self.calls += 1
        yield {"type": "text_delta", "text": "answer"}
        yield {
            "type": "usage",
            "input_tokens": 10,
            "output_tokens": out,
            "cost_usd": 0.0,
        }
        yield {"type": "done", "stop_reason": "end_turn"}


async def test_turn_progress_is_emitted_with_the_running_output_total(monkeypatch):
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _UsageReportingProvider([128]))
    sid = "test-progress-1"
    _cleanup(sid)
    try:
        events = await _drain(assistant.run(sid, [{"role": "user", "content": "hi"}], "s"))
    finally:
        _cleanup(sid)

    progress = [e for e in events if e.get("type") == "turn_progress"]
    assert progress, "the working indicator has no token source without this event"
    assert progress[-1]["output_tokens"] == 128


async def test_turn_progress_accumulates_across_rounds_rather_than_reporting_deltas(
    monkeypatch,
):
    """The frontend takes the LAST value as the total, so this must accumulate.

    Emitting per-round deltas here would make the indicator read 900 for a turn that had
    produced 1,300 — a number that looks precise and is simply wrong.
    """
    provider = _UsageReportingProvider([400, 900])
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)

    sid = "test-progress-2"
    _cleanup(sid)
    # A queued message forces a SECOND model round: the loop re-checks the queue instead of
    # exiting (acceptance C3), which is exactly the multi-round shape being asserted.
    pending_injection.enqueue(sid, "client-1", "and one more thing")
    try:
        events = await _drain(assistant.run(sid, [{"role": "user", "content": "hi"}], "s"))
    finally:
        _cleanup(sid)
        pending_injection._reset_for_tests()

    progress = [e["output_tokens"] for e in events if e.get("type") == "turn_progress"]
    assert len(progress) >= 2, f"expected one per model round, got {progress}"
    assert progress[0] == 400
    assert progress[-1] == 1300, "must be the running total, not the round's own 900"
    assert progress == sorted(progress), "a running total never decreases mid-turn"

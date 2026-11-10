"""Text after a call streams AFTER the call's row — never before it, split off its line.

Live 2026-10-31 (chat, claude_cli): a lone "W" sat between the Get portfolio and List accounts
rows; the saved reply was clean. The round wrote two calls with a lead-in between them, and
one CLI delta carried the end of the first call AND the lead-in's first letter:

    …}</tool_call>\n\nW  |  hile I'm at it, the accounts:\n<tool_call>…

The provider yielded that delta's text before the first call's ``tool_use_start``, so
``LiveLineFilter`` held "W" as the start of a line and the call's flush released it as if it
had come before the call. The rest of the line ended in a colon and was dropped as a lead-in
at the second call — leaving only its first letter on screen. ``drop_call_preamble`` drops the
whole line from the SAVED text, which is why a reload was fine.
"""

from __future__ import annotations

import sys

import pytest

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.models.claude_cli_provider as ccp
import backend.ai.pending_injection as pending_injection
import backend.ai.tools  # noqa: F401 — fills the registry the filter reads
import backend.ai.turns as turns

FIRST = '<tool_call>{"name": "calculate", "input": {"expressions": ["1 + 1"]}}</tool_call>'
SECOND = '<tool_call>{"name": "calculate", "input": {"expressions": ["2 + 2"]}}</tool_call>'
ANSWER = "Both sums are done: 2 and 4."


def _delta(text: str) -> dict:
    return {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    }


def _round(*deltas: str) -> list[dict]:
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "duration_ms": 1,
        "num_turns": 1,
        "total_cost_usd": 0.0,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    return [
        {"type": "system", "subtype": "init", "apiKeySource": "none", "tools": []},
        *(_delta(d) for d in deltas),
        result,
    ]


@pytest.fixture
def _turn(monkeypatch, db_isolated):
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(ccp.claude_live_session, "sessions_enabled", lambda: False)
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)
    yield
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()


async def _run(monkeypatch, *rounds: list[dict]) -> tuple[list[dict], list[dict]]:
    it = iter(rounds)
    provider = ccp.ClaudeCliProvider(cli_path=sys.executable)

    async def _events(*_a, **_k):
        for ev in next(it):
            yield ev

    monkeypatch.setattr(provider, "_events", _events)
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)
    sid = "test-text-after-call"
    try:
        events = [ev async for ev in assistant.run(sid, [{"role": "user", "content": "sum"}], "s")]
    finally:
        saved = assistant.get_or_create_session(sid)[1]
        assistant.SESSIONS.pop(sid, None)
        assistant._SESSION_LOCKS.pop(sid, None)
    return events, saved


def _between_rows(events: list[dict]) -> list[str]:
    """The text the chat shows after each tool row, before the next one (or the round's end)."""
    gaps: list[str] = []
    for ev in events:
        if ev["type"] == "tool_use_start":
            gaps.append("")
        elif ev["type"] == "text_delta" and gaps:
            gaps[-1] += ev["text"]
    return gaps


def _before_first_row(events: list[dict]) -> str:
    first = next(i for i, e in enumerate(events) if e["type"] == "tool_use_start")
    return "".join(e["text"] for e in events[:first] if e["type"] == "text_delta")


@pytest.mark.parametrize(
    ("deltas", "lead_in"),
    [
        ((f"{FIRST}\n\nW", "hile I'm at it, the second one:", SECOND), ""),
        (
            (f"{FIRST}\n\nW", "hile I'm at it, the second one:\n", SECOND),
            "While I'm at it, the second one:",
        ),
        ((f"{FIRST}\nW", "hile I'm at it:", f"\n{SECOND}"), "While I'm at it:"),
        ((f"\n\n{FIRST}\n\nNow the", " second one:", SECOND), ""),
    ],
)
async def test_a_lead_in_split_across_deltas_is_never_cut_above_the_row(
    monkeypatch, _turn, deltas, lead_in
):
    events, saved = await _run(monkeypatch, _round(*deltas), _round(ANSWER))

    assert [e["name"] for e in events if e["type"] == "tool_use_start"] == ["calculate"] * 2
    assert _before_first_row(events).strip() == ""
    gaps = _between_rows(events)
    assert gaps[0].strip() == lead_in, gaps
    assert gaps[1].strip() == ANSWER, gaps
    assert saved[-1]["content"] == [{"type": "text", "text": ANSWER}]


@pytest.mark.parametrize("cut", [1, 2, 7])
async def test_prose_after_a_call_shows_whole_after_its_row(monkeypatch, _turn, cut):
    tail = "That settles the first sum, and the second follows from it directly."
    events, _ = await _run(
        monkeypatch, _round(f"{FIRST}\n\n{tail[:cut]}", f"{tail[cut:]}\n", SECOND), _round(ANSWER)
    )
    assert _before_first_row(events).strip() == ""
    assert _between_rows(events)[0].strip() == tail

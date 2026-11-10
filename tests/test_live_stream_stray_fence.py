"""A stray markdown fence never reaches the chat — live, not only once the turn is saved.

Persona QA round 9 (2026-10-14): a Lucius reply streamed as an EMPTY code box followed by ONE
monospace line, cut at the panel edge; the saved message was clean. The round that called
`calculate` wrote its call bare and then closed a fence it never opened:

    <tool_call>{"name": "calculate", ...}</tool_call>
    ```

`ToolCallScanner` hands that trailing fence on as text, `LiveLineFilter` held it as a short line
and released it at the round's end, AFTER the tool row — so the next round's answer was appended
to it, glued: "```You have S$788 ...". Markdown reads everything after the backticks as the
fence's info string, so the first paragraph vanished and the second became a code block.
`strip_tool_syntax` already removed that fence from the SAVED text, which is why a reload was fine.

The live stream and the saved text must agree, so the filter now holds a bare opening fence until
it closes and drops it (as `strip_tool_syntax` does) when the round ends with it still open.
"""

from __future__ import annotations

import json
import random
import re
import sys

import pytest

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.models.claude_cli_provider as ccp
import backend.ai.pending_injection as pending_injection
import backend.ai.tools  # noqa: F401 — fills the registry the filter reads
import backend.ai.turns as turns
from backend.ai.models.cli_tool_protocol import ToolCallScanner
from backend.ai.tool_text import LiveLineFilter, drop_call_preamble, strip_tool_syntax

FIRST = (
    "You have S$788 left across the two, Tom: S$500 in Groceries (untouched so far) and "
    "S$288 in Dining out, which is your S$300 less today's S$12 lunch."
)
SECOND = (
    "Those are the only amounts envelope-funded for food and fun. The S$420 in Ready to "
    "Assign is unassigned, so you could top either envelope up from it."
)
ANSWER = f"{FIRST}\n\n{SECOND}"
CALL = '<tool_call>{"name": "calculate", "input": {"expressions": ["500 + 288"]}}</tool_call>'


def _stream(raw: str, *, chunk: int = 7, has_calls: bool = False) -> str:
    f = LiveLineFilter()
    out = "".join(f.feed(raw[i : i + chunk]) for i in range(0, len(raw), chunk))
    return out + f.flush(has_calls=has_calls)


# ── the filter ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("chunk", [1, 3, 7, 1000])
def test_a_fence_the_round_ends_on_is_never_shown(chunk):
    assert _stream("\n```", chunk=chunk, has_calls=True).strip() == ""
    assert _stream("Here you go:\n\n```\n", chunk=chunk).strip() == "Here you go:"


@pytest.mark.parametrize("chunk", [1, 3, 7, 1000])
def test_a_reply_that_opens_on_an_unclosed_fence_is_shown_without_it(chunk):
    live = _stream(f"```\n\n{ANSWER}", chunk=chunk)
    assert live == ANSWER
    assert live == strip_tool_syntax(f"```\n\n{ANSWER}")


@pytest.mark.parametrize("chunk", [1, 5, 1000])
def test_real_code_is_still_shown_whole(chunk):
    code = "Run this:\n\n```python\nprint(1)\n```\n\nThen:\n\n```\nls -la\n```\n\nDone."
    assert _stream(code, chunk=chunk) == code
    lead = "```\nls -la\n```\n\nDone."
    assert _stream(lead, chunk=chunk) == lead


def test_a_lone_fence_never_glues_onto_the_next_round():
    r1 = _stream("\n```", has_calls=True)
    assert "`" not in r1


# ── live == saved, over the shapes a CLI round takes ─────────────────────────────────


def _through_scanner(raw: str, chunk: int) -> tuple[str, bool]:
    """What the chat shows for one round: scanner → filter, flushed the way assistant.run does."""
    scanner, live, emitted, out = ToolCallScanner(), LiveLineFilter(), 0, ""
    for i in range(0, len(raw), chunk):
        for visible in scanner.feed(raw[i : i + chunk]):
            out += live.feed(visible)
        while emitted < len(scanner.calls):
            out += live.flush(has_calls=True)
            emitted += 1
    out += live.feed(scanner.flush())
    return out + live.flush(has_calls=scanner.has_calls), scanner.has_calls


_ROUNDS = [
    CALL,
    CALL + "\n```",
    CALL + "\n\n```\n",
    "```\n" + CALL + "\n```",
    "```json\n" + CALL + "\n```\n",
    "Let me add those up:\n```\n" + CALL + "\n```",
    ANSWER,
    "```\n\n" + ANSWER,
    "\n```\n" + ANSWER,
    ANSWER + "\n```",
    ANSWER + "\n\n```\n",
    "```\n```\n\n" + ANSWER,
    "Here:\n\n```\nplain output\n```\n\n" + ANSWER,
]


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.mark.parametrize("raw", _ROUNDS)
def test_what_streams_is_what_is_saved(raw):
    rng = random.Random(7)
    for _ in range(25):
        live, has_calls = _through_scanner(raw, rng.choice([1, 2, 5, 11, 40]))
        saved = drop_call_preamble(strip_tool_syntax(raw), has_calls=has_calls)
        assert _flat(live) == _flat(saved), (raw, live)
        assert not re.search(r"```[^\n`\s]", live), live


# ── end to end: the CLI transport through assistant.run ──────────────────────────────


def _delta(text: str) -> dict:
    return {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    }


def _round(raw: str, *, chunk: int = 9) -> list[dict]:
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
        *(_delta(raw[i : i + chunk]) for i in range(0, len(raw), chunk)),
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


def _chat_blocks(events: list[dict]) -> list[str]:
    """The text blocks the client builds: a tool row closes the block being appended to."""
    blocks: list[str] = []
    open_block = False
    for ev in events:
        if ev["type"] == "text_delta":
            if open_block:
                blocks[-1] += ev["text"]
            else:
                blocks.append(ev["text"])
                open_block = True
        elif ev["type"] == "tool_use_start":
            open_block = False
    return blocks


@pytest.mark.parametrize("closing", ["\n```", "\n\n```\n", "```"])
async def test_the_answer_after_a_called_tool_never_starts_with_a_stray_fence(
    monkeypatch, _turn, closing
):
    rounds = iter([_round(CALL + closing), _round(ANSWER)])
    provider = ccp.ClaudeCliProvider(cli_path=sys.executable)

    async def _events(*_a, **_k):
        for ev in next(rounds):
            yield ev

    monkeypatch.setattr(provider, "_events", _events)
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)

    sid = "test-stray-fence-live"
    try:
        events = [
            ev
            async for ev in assistant.run(
                sid, [{"role": "user", "content": "how much is left?"}], "s"
            )
        ]
    finally:
        saved = assistant.get_or_create_session(sid)[1]
        assistant.SESSIONS.pop(sid, None)
        assistant._SESSION_LOCKS.pop(sid, None)

    blocks = [b.strip() for b in _chat_blocks(events) if b.strip()]
    assert blocks == [ANSWER], blocks
    assert "```" not in json.dumps(events)
    assert saved[-1]["content"] == [{"type": "text", "text": ANSWER}]

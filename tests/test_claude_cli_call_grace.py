"""A CLI round that has written its tool calls is cut after a grace period and they run.

Live 2026-10-19 (run_6955df3357ac): an Automation round wrote ``update_run_task`` and
``email_search_messages``, then the CLI kept streaming for the whole 300 s ceiling without
a ``result`` event — both calls were marked failed and no mail was read. The calls a round
has already written are run once the newest is ``ONW_CLAUDE_CLI_CALL_GRACE`` seconds old,
and what streamed after it is logged.
"""

from __future__ import annotations

import asyncio
import logging
import stat
import textwrap
import time

import pytest

from backend.ai.models import claude_cli_provider as ccp
from backend.ai.models import claude_live_session as cls
from backend.ai.models.claude_cli_provider import ClaudeCliProvider
from backend.core.errors import ProviderTimeoutError

_CALL_A = '<tool_call>{"name": "update_run_task", "input": {"id": "1"}}</tool_call>'
_CALL_B = '<tool_call>{"name": "email_search_messages", "input": {"since": "1d"}}</tool_call>'


@pytest.fixture(autouse=True)
def _one_second_grace(monkeypatch):
    monkeypatch.setenv("ONW_CLAUDE_CLI_CALL_GRACE", "1")


def _init() -> dict:
    return {"type": "system", "subtype": "init", "apiKeySource": "none"}


def _delta(text: str) -> dict:
    return {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    }


def _result() -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _fake_cli(tmp_path, steps: list, tail: str):
    """Per stdin line: play ``steps`` (an event, or a float = sleep), then ``tail``:
    ``runaway`` streams text forever, ``silent`` says nothing, ``none`` waits for input."""
    script = tmp_path / "claude"
    script.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env python3
            import json, sys, time
            def out(ev):
                sys.stdout.write(json.dumps(ev) + "\\n")
                sys.stdout.flush()
            for line in sys.stdin:
                for step in {steps!r}:
                    if isinstance(step, float):
                        time.sleep(step)
                    else:
                        out(step)
                if {tail!r} == "runaway":
                    for _ in range(1200):
                        out({_delta("Found 5 emails and logged them. ")!r})
                        time.sleep(0.05)
                elif {tail!r} == "silent":
                    time.sleep(60)
            """
        ).strip()
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


async def _turn(tmp_path, monkeypatch, sessions: bool, steps: list, tail: str) -> list[dict]:
    monkeypatch.setenv("ONW_CLAUDE_CLI_SESSIONS", "1" if sessions else "0")
    provider = ClaudeCliProvider(cli_path=str(_fake_cli(tmp_path, steps, tail)))

    async def run() -> list[dict]:
        return [
            ev
            async for ev in provider.stream_chat(
                messages=[{"role": "user", "content": "scan the mailbox"}],
                tools=[],
                system="",
                model="haiku",
            )
        ]

    try:
        return await asyncio.wait_for(run(), timeout=30)
    finally:
        for session in cls.pool().all():
            await session.close()


def _calls(events: list[dict]) -> list[str]:
    return [e["name"] for e in events if e["type"] == "tool_use"]


_MODES = pytest.mark.parametrize("sessions", [True, False], ids=["live-session", "one-shot"])


@_MODES
async def test_a_round_that_keeps_writing_after_its_calls_is_cut_and_they_run(
    tmp_path, monkeypatch, caplog, sessions
):
    caplog.set_level(logging.WARNING, logger=ccp.__name__)
    started = time.monotonic()
    events = await _turn(
        tmp_path, monkeypatch, sessions, [_init(), _delta(_CALL_A + _CALL_B)], "runaway"
    )
    assert time.monotonic() - started < 10
    assert _calls(events) == ["update_run_task", "email_search_messages"]
    assert events[-1] == {"type": "done", "stop_reason": "tool_use"}
    assert any(e["type"] == "usage" for e in events)
    cut = [r.getMessage() for r in caplog.records if "no result event" in r.getMessage()]
    assert cut and "text_delta=" in cut[0] and "Found 5 emails" in cut[0]


@_MODES
async def test_a_round_that_goes_quiet_after_its_calls_is_cut_and_they_run(
    tmp_path, monkeypatch, sessions
):
    events = await _turn(tmp_path, monkeypatch, sessions, [_init(), _delta(_CALL_A)], "silent")
    assert _calls(events) == ["update_run_task"]


@_MODES
async def test_every_call_written_within_the_grace_is_kept(tmp_path, monkeypatch, sessions):
    steps = [_init(), _delta(_CALL_A), 0.6, _delta(_CALL_B)]
    events = await _turn(tmp_path, monkeypatch, sessions, steps, "runaway")
    assert _calls(events) == ["update_run_task", "email_search_messages"]


@_MODES
async def test_a_call_still_being_written_is_never_cut_in_half(tmp_path, monkeypatch, sessions):
    half = len(_CALL_B) // 2
    steps = [_init(), _delta(_CALL_A), _delta(_CALL_B[:half]), 1.6, _delta(_CALL_B[half:])]
    events = await _turn(tmp_path, monkeypatch, sessions, steps + [_result()], "none")
    assert _calls(events) == ["update_run_task", "email_search_messages"]
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["cost_usd"] == 0.01


@_MODES
async def test_a_round_that_ends_normally_is_untouched(tmp_path, monkeypatch, sessions):
    events = await _turn(
        tmp_path, monkeypatch, sessions, [_init(), _delta(_CALL_A), _result()], "none"
    )
    assert _calls(events) == ["update_run_task"]
    assert next(e for e in events if e["type"] == "usage")["cost_usd"] == 0.01


@_MODES
async def test_a_timeout_logs_what_the_round_streamed(tmp_path, monkeypatch, caplog, sessions):
    monkeypatch.setattr(cls.LiveSession, "timeout_seconds", classmethod(lambda c: 1.5))
    monkeypatch.setattr(ccp, "_timeout_seconds", lambda: 1.5)
    caplog.set_level(logging.WARNING, logger=ccp.__name__)
    with pytest.raises(ProviderTimeoutError):
        await _turn(tmp_path, monkeypatch, sessions, [_init()], "runaway")
    timed_out = [r.getMessage() for r in caplog.records if "timed out" in r.getMessage()]
    assert timed_out and "no call parsed" in timed_out[0] and "text_delta=" in timed_out[0]

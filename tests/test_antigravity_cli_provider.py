"""Tests for the AntigravityCliProvider (`agy -p`) and its live sessions.

Hermetic: a FAKE `agy` (a small Python script) speaks the documented headless stream-json
contract over real pipes — one `{"event": "user", ...}` per stdin line, answered with `init`,
`step_update` and ONE `result` per turn — and records every line it receives together with its
pid and argv. The real binary is not required. See ADR 2026-10-05-antigravity-cli-live-sessions.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import textwrap
from types import SimpleNamespace

import pytest

from backend.ai.models import antigravity_live_session as als
from backend.ai.models.antigravity_cli_provider import (
    AntigravityCliProvider,
    cost_basis_for,
)
from backend.core.errors import ExternalServiceError

USER = {"role": "user", "content": "What is my balance? TOOLCALL"}
ASSISTANT = {
    "role": "assistant",
    "content": [
        {"type": "text", "text": "Checking."},
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "get_balance",
            "input": {"account_id": "acc_123"},
        },
    ],
}
TOOL_RESULT = {
    "role": "user",
    "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "balance is 42"}],
}
TOOLS = [{"name": "get_balance", "description": "Get account balance", "parameters": {}}]
SYSTEM = "System instructions"


def _fake_agy(tmp_path):
    """An `agy` stand-in. Markers in the user text steer it:

    TOOLCALL emits a tool call in text; NODELTA answers only in `result.response`;
    CRASH_ONCE exits 1 the first time it is seen (then behaves); FAIL answers a result with
    status ERROR; NORESULT exits 0 with no result event; PARTIAL_ONCE streams half a tool
    call and dies (once). `result.usage` is CUMULATIVE over the process, like the real CLI's:
    turn n alone is 100+n input, 7 output, 3 cached.
    """
    script = tmp_path / "agy"
    script.write_text(
        textwrap.dedent(
            f"""
            #!{sys.executable}
            import json, os, sys, pathlib
            here = pathlib.Path(__file__).resolve().parent

            def emit(obj):
                sys.stdout.write(json.dumps(obj) + "\\n")
                sys.stdout.flush()

            emit({{"event": "init", "init": {{"cwd": os.getcwd(), "tools": [], "permission_mode": "x"}}}})
            n = 0
            for line in sys.stdin:
                n += 1
                event = json.loads(line)
                content = event["message"]["content"]
                with open(here / "received.jsonl", "a") as fh:
                    fh.write(json.dumps({{"pid": os.getpid(), "argv": sys.argv[1:], "n": n, "event": event}}) + "\\n")
                if "CRASH_ONCE" in content and not (here / "crashed").exists():
                    (here / "crashed").write_text("1")
                    sys.exit(1)
                if "NORESULT" in content:
                    sys.exit(0)
                if "PARTIAL_ONCE" in content and not (here / "partial").exists():
                    (here / "partial").write_text("1")
                    emit({{"event": "step_update", "step_update": {{"state": "ACTIVE",
                          "text_delta": '<tool_call>\\n{{"na'}}}})
                    sys.exit(1)
                cid = "conv-1"
                if "FAIL" in content:
                    emit({{"event": "result", "result": {{"conversation_id": cid, "status": "ERROR",
                          "error": "Authentication failed", "response": "", "num_turns": 1}}}})
                    continue
                if "TOOLCALL" in content:
                    chunks = ["Checking.\\n<tool_", 'call>\\n{{"id": "call_1", "name": "get_balance", ',
                              '"input": {{"account_id": "acc_123"}}}}\\n</tool_call>']
                else:
                    chunks = ["ok %d" % n]
                response = "".join(chunks)
                if "NODELTA" in content:
                    chunks, response = [], "from response"
                for i, chunk in enumerate(chunks):
                    emit({{"event": "step_update", "step_update": {{"conversation_id": cid, "step_index": i,
                          "state": "ACTIVE", "step_type": "MODEL", "text_delta": chunk}}}})
                emit({{"event": "step_update", "step_update": {{"conversation_id": cid, "step_index": 9,
                      "state": "DONE", "step_type": "MODEL"}}}})
                emit({{"event": "result", "result": {{"conversation_id": cid, "status": "SUCCESS",
                      "response": response, "duration_seconds": 0.1, "num_turns": n,
                      "usage": {{"input_tokens": 100 * n + n * (n + 1) // 2, "output_tokens": 7 * n,
                                 "thinking_tokens": 2 * n, "cache_read_tokens": 3 * n,
                                 "total_tokens": 112 * n}}}}}})
            """
        ).strip()
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _received(tmp_path) -> list[dict]:
    path = tmp_path / "received.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture(autouse=True)
def _clean_pool():
    def purge():
        for pool in als._pools.values():
            for session in pool.all():
                proc = session.proc
                if proc is not None and proc.returncode is None:
                    try:
                        proc.kill()
                    except (ProcessLookupError, OSError):
                        pass
        als._pools.clear()

    purge()
    yield
    purge()


@pytest.fixture
def agy(tmp_path, monkeypatch):
    script = _fake_agy(tmp_path)
    monkeypatch.setattr(
        "backend.ai.models.antigravity_cli_provider.child_environment",
        lambda: {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "ONW_PRIVATE_BUS_ID": "synthetic",
        },
    )
    monkeypatch.setattr(
        "backend.db.model_config_store.cli_binary_path", lambda *_a, **_k: str(script)
    )
    return script


@pytest.fixture
async def provider(agy):
    yield AntigravityCliProvider()
    await als.close_all()


async def _chat(provider, messages, system=SYSTEM, tools=TOOLS) -> list[dict]:
    return [
        ev
        async for ev in provider.stream_chat(
            messages=messages, tools=tools, system=system, model="gemini-2.5-flash"
        )
    ]


def _text(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "text_delta")


def test_cost_basis_for():
    assert cost_basis_for("") == "plan_included"
    assert cost_basis_for("key-123") == "api_billed"


# ── one-shot paths (complete / stream_complete) ──────────────────────────────


async def test_complete_sends_the_prompt_over_stdin_never_argv(provider, tmp_path):
    res = await provider.complete(
        prompt="Hi there",
        system="You are an assistant.",
        model="gemini-2.5-flash",
        effort="high",
    )

    assert res == "ok 1"
    (row,) = _received(tmp_path)
    assert (
        "# System instructions\n\nYou are an assistant.\n\n---\n\nHi there"
        in (row["event"]["message"]["content"])
    )
    assert row["event"]["event"] == "user"
    argv = row["argv"]
    assert argv[:4] == ["--input-format", "stream-json", "--output-format", "stream-json"]
    assert "-p" not in argv, "a bare -p would swallow the next flag as its prompt"
    assert "--model" in argv and "gemini-2.5-flash" in argv
    assert "--effort" in argv and "high" in argv
    assert not any("Hi there" in a for a in argv), "the prompt must never ride in argv"


async def test_complete_raises_on_cli_error(provider):
    with pytest.raises(ExternalServiceError, match="Authentication failed"):
        await provider.complete(prompt="FAIL", model="gemini-2.5-flash")


async def test_complete_falls_back_to_the_result_response(provider):
    assert await provider.complete(prompt="NODELTA", model="gemini-2.5-flash") == "from response"


async def test_complete_raises_when_the_cli_never_answers(provider):
    with pytest.raises(ExternalServiceError, match="without a result event"):
        await provider.complete(prompt="NORESULT", model="gemini-2.5-flash")


async def test_stream_complete_yields_chunk_usage_done(provider):
    events = [e async for e in provider.stream_complete(prompt="Hi", model="gemini-2.5-flash")]
    assert [e["type"] for e in events] == ["chunk", "usage", "done"]
    assert events[0]["text"] == "ok 1"
    assert events[1]["input_tokens"] == 101


# ── chat: tool calls, usage ───────────────────────────────────────────────────


async def test_stream_chat_emits_tool_calls_and_maps_usage(provider):
    events = await _chat(provider, [USER])

    types = [e["type"] for e in events]
    assert types.index("text_delta") < types.index("tool_use") < types.index("usage")
    assert _text(events).strip() == "Checking."
    tool_use = next(e for e in events if e["type"] == "tool_use")
    assert tool_use["name"] == "get_balance"
    assert tool_use["input"] == {"account_id": "acc_123"}
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["input_tokens"] == 101
    assert usage["output_tokens"] == 7
    assert usage["cache_read_tokens"] == 3
    assert usage["cost_basis"] == "plan_included"
    assert usage["model"] == "gemini-2.5-flash"
    assert events[-1] == {"type": "done", "stop_reason": "tool_use"}


async def test_stream_chat_without_deltas_uses_the_result_response(provider):
    events = await _chat(provider, [{"role": "user", "content": "NODELTA"}])
    assert _text(events) == "from response"
    assert events[-1]["stop_reason"] == "end_turn"


# ── chat: live sessions ───────────────────────────────────────────────────────


async def test_the_first_round_sends_system_and_the_full_transcript(provider, tmp_path):
    await _chat(provider, [USER])

    (row,) = _received(tmp_path)
    content = row["event"]["message"]["content"]
    assert content.startswith("# System instructions\n\nSystem instructions")
    assert "get_balance" in content, "the tool manifest rides in the system block"
    assert "Human: What is my balance? TOOLCALL" in content
    assert content.endswith("Assistant:")
    assert not any("balance" in a for a in row["argv"])


async def test_the_second_round_reuses_the_process_and_sends_only_the_tool_result(
    provider, tmp_path
):
    await _chat(provider, [USER])
    second = await _chat(provider, [USER, ASSISTANT, TOOL_RESULT])

    first_row, second_row = _received(tmp_path)
    assert second_row["pid"] == first_row["pid"], "the second round must land on the live process"
    assert second_row["n"] == 2
    sent = second_row["event"]["message"]["content"]
    assert "balance is 42" in sent
    assert "What is my balance?" not in sent, "history is not re-sent"
    assert "Checking." not in sent, "the CLI's own reply is never echoed back at it"
    assert "System instructions" not in sent
    assert _text(second) == "ok 2"
    assert len(als.pool().all()) == 1


async def test_a_rewritten_prefix_spawns_a_fresh_process(provider, tmp_path):
    await _chat(provider, [USER])
    folded = [{"role": "user", "content": "[summary of earlier turns]"}, TOOL_RESULT]
    await _chat(provider, folded)

    first_row, second_row = _received(tmp_path)
    assert second_row["pid"] != first_row["pid"]
    assert second_row["n"] == 1
    sent = second_row["event"]["message"]["content"]
    assert "[summary of earlier turns]" in sent, "a fresh session gets the FULL transcript"
    assert "System instructions" in sent


async def test_a_changed_system_prompt_never_shares_a_session(provider, tmp_path):
    await _chat(provider, [USER], system="Prompt A")
    await _chat(provider, [USER, ASSISTANT, TOOL_RESULT], system="Prompt B")

    first_row, second_row = _received(tmp_path)
    assert second_row["pid"] != first_row["pid"]
    assert "Prompt B" in second_row["event"]["message"]["content"]


async def test_changed_identity_never_shares_a_session(provider, tmp_path, monkeypatch):
    await _chat(provider, [USER])
    monkeypatch.setattr(
        "backend.ai.models.antigravity_cli_provider.child_environment",
        lambda: {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "ONW_PRIVATE_BUS_ID": "other"},
    )
    await _chat(provider, [USER, ASSISTANT, TOOL_RESULT])
    first_row, second_row = _received(tmp_path)
    assert second_row["pid"] != first_row["pid"]


async def test_a_changed_tool_manifest_never_shares_a_session(provider, tmp_path):
    await _chat(provider, [USER])
    other = [{"name": "other_tool", "description": "x", "parameters": {}}]
    await _chat(provider, [USER, ASSISTANT, TOOL_RESULT], tools=other)

    first_row, second_row = _received(tmp_path)
    assert second_row["pid"] != first_row["pid"]


async def test_a_failed_reused_session_retries_on_a_fresh_process(provider, tmp_path):
    await _chat(provider, [USER])
    crash = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "CRASH_ONCE"}],
    }
    events = await _chat(provider, [USER, ASSISTANT, crash])

    rows = _received(tmp_path)
    assert len(rows) == 3
    live, crashed, retried = rows
    assert crashed["pid"] == live["pid"], "the reused session took the round and died on it"
    assert retried["pid"] != live["pid"]
    assert "What is my balance?" in retried["event"]["message"]["content"], (
        "the retry is a fresh process, so it carries the full transcript"
    )
    assert _text(events).strip() == "Checking."
    assert any(e["type"] == "tool_use" for e in events)
    assert events[-1]["type"] == "done"


async def test_a_failed_fresh_session_raises_and_is_retired(provider):
    with pytest.raises(ExternalServiceError, match="Authentication failed"):
        await _chat(provider, [{"role": "user", "content": "FAIL"}])
    assert als.pool().all() == []


async def test_a_round_without_a_result_event_retires_the_session(provider):
    with pytest.raises(ExternalServiceError, match="without a result event"):
        await _chat(provider, [{"role": "user", "content": "NORESULT"}])
    assert als.pool().all() == []


async def test_sessions_off_spawns_a_process_per_round_still_over_stdin(
    provider, tmp_path, monkeypatch
):
    monkeypatch.setenv("ONW_ANTIGRAVITY_CLI_SESSIONS", "0")
    await _chat(provider, [USER])
    await _chat(provider, [USER, ASSISTANT, TOOL_RESULT])

    first_row, second_row = _received(tmp_path)
    assert second_row["pid"] != first_row["pid"]
    assert second_row["n"] == 1
    assert "What is my balance?" in second_row["event"]["message"]["content"]
    assert "balance is 42" in second_row["event"]["message"]["content"]
    for row in (first_row, second_row):
        assert not any("balance" in a for a in row["argv"])
    assert als.pool().all() == []


async def test_a_prompt_beyond_arg_max_goes_through_stdin(provider, tmp_path):
    big = "x" * 2_000_000
    events = await _chat(provider, [{"role": "user", "content": big}])

    (row,) = _received(tmp_path)
    assert big in row["event"]["message"]["content"]
    assert _text(events) == "ok 1"
    assert max(len(a) for a in row["argv"]) < 100


def test_sessions_can_be_switched_off(monkeypatch):
    assert als.sessions_enabled() is True
    monkeypatch.setenv("ONW_ANTIGRAVITY_CLI_SESSIONS", "0")
    assert als.sessions_enabled() is False


async def test_usage_is_the_rounds_own_delta_not_the_sessions_running_total(provider, monkeypatch):
    """`result.usage` is cumulative over the session — recording it raw bills round 1 again."""
    recorded: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        "backend.ai.cost_tracker.record",
        lambda _src, _model, inp, out, _w, cached, **_kw: recorded.append((inp, out, cached)),
    )
    await _chat(provider, [USER])
    second = await _chat(provider, [USER, ASSISTANT, TOOL_RESULT])
    third = await _chat(provider, [USER, ASSISTANT, TOOL_RESULT, ASSISTANT, TOOL_RESULT])

    assert recorded == [(101, 7, 3), (102, 7, 3), (103, 7, 3)]
    usage = next(e for e in second if e["type"] == "usage")
    assert (usage["input_tokens"], usage["output_tokens"], usage["cache_read_tokens"]) == (
        102,
        7,
        3,
    )
    assert next(e for e in third if e["type"] == "usage")["input_tokens"] == 103


async def test_a_failed_round_is_not_flushed_and_still_retries(provider, tmp_path):
    """A half-held `<tool_call>` fragment must not be shown, nor block the nothing-shown retry."""
    first = [{"role": "user", "content": "hello"}]
    await _chat(provider, first)
    again = {"role": "user", "content": "PARTIAL_ONCE"}
    events = await _chat(provider, first + [{"role": "assistant", "content": "ok 1"}, again])

    assert _text(events) == "ok 1"
    assert "<tool_call" not in _text(events)
    rows = _received(tmp_path)
    assert len(rows) == 3 and rows[2]["pid"] != rows[0]["pid"]


async def test_the_retry_spawns_a_new_process_never_another_idle_session(provider, tmp_path):
    # Two sessions holding the same prefix under the same key.
    await asyncio.gather(_chat(provider, [USER]), _chat(provider, [USER]))
    crash = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "CRASH_ONCE"}],
    }
    await _chat(provider, [USER, ASSISTANT, crash])

    rows = _received(tmp_path)
    earlier = {rows[0]["pid"], rows[1]["pid"]}
    assert len(earlier) == 2 and len(rows) == 4
    assert rows[3]["pid"] not in earlier, "the retry must be a brand-new process"
    assert rows[3]["n"] == 1


async def test_a_write_to_an_exited_child_is_an_external_service_error(agy):
    session = await als.spawn([str(agy)], {})

    async def broken_drain():
        raise BrokenPipeError(32, "Broken pipe")

    real_proc = session.proc
    session.proc = SimpleNamespace(
        stdin=SimpleNamespace(write=lambda _b: None, drain=broken_drain), returncode=None
    )
    with pytest.raises(ExternalServiceError, match="closed its input"):
        await session.send("hi")
    session.proc = real_proc
    await session.close()

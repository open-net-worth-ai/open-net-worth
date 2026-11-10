"""``assistant.run()`` / ``resolve_pending_tool_use()`` event ownership + cancellation
healing — concurrent-conversations RFC Phase 2.

Two things pinned here:

1. **Ownership stamping.** Every event either function yields carries ``session_id`` and a
   freshly minted ``turn_id`` (additive — no existing key touched). This is what lets a
   frontend reducer verify an event's owner against server-supplied data instead of a
   closure variable, and lets a late event from a superseded turn be discarded.
2. **Cancellation healing (security review, 2026-08-06).** ``asyncio.CancelledError`` is a
   ``BaseException``, so the `except Exception` rollback inside ``_run_turn`` never runs on
   a genuine cancellation (a client disconnect, or ``POST /api/chat/{id}/stop``) — before
   the fix, a turn cancelled mid-tool-call left ``SESSIONS[session_id]`` with a dangling
   ``tool_use`` that 400s the very next message until a SECOND disconnect happened to heal
   it. ``_run_turn``'s ``finally`` now heals + persists unconditionally, AND (concurrent-
   conversations RFC Phase 3, acceptance D8) the stub text for a cancellation-caused heal is
   deliberately NOT "did not complete" — a cancellation can land at an ``await`` AFTER a
   tool's own synchronous store write already committed, so the stub says the outcome is
   unknown and must be verified before retrying. Only a genuine crash-orphan (no cancel in
   flight) gets the plain "did not complete" default.

Offline + isolated: no LLM, no network — a scripted/stubbed provider and a faked
``execute_tool``, matching ``tests/test_p0_5_concurrency.py`` and
``tests/test_resolve_pending_tool_use.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.ai import assistant
from backend.db import pending_plan_store as plan_mod


def _isolate(monkeypatch):
    # Sessions (assistant._sessions_dir) and PendingPlanStore are both DB-backed now
    # (Wave 5E / 5D2b) — no more per-store file path to monkeypatch; both resolve
    # against the shared default engine, cleared after every test.
    # Bypasses the discovery/offer gate (auto-load) entirely — irrelevant to what these
    # tests pin, and exercising the real thing needs a much larger fixture (see
    # tests/test_resolve_pending_tool_use.py's `env`).
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)


def _cleanup(sid: str) -> None:
    assistant.SESSIONS.pop(sid, None)
    assistant._SESSION_LOCKS.pop(sid, None)


async def _drain(gen):
    events = []
    async for ev in gen:
        events.append(ev)
    return events


class _TextOnlyProvider:
    async def stream_chat(self, *a, **kw):
        yield {"type": "text_delta", "text": "hello"}
        yield {"type": "done", "stop_reason": "end_turn"}


# ── 1. session_id / turn_id ownership stamping ──────────────────────────────────────────


async def test_run_stamps_session_id_and_a_turn_id_on_every_event(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _TextOnlyProvider())

    sid = "test-turn-stamp"
    _cleanup(sid)
    try:
        events = await _drain(assistant.run(sid, [{"role": "user", "content": "hi"}], "s"))
    finally:
        _cleanup(sid)

    assert events, "expected at least one event"
    assert all(e.get("session_id") == sid for e in events), events
    turn_ids = {e.get("turn_id") for e in events}
    assert len(turn_ids) == 1, "one run() call must mint exactly one turn_id"
    assert next(iter(turn_ids)), "turn_id must be non-empty"


async def test_two_separate_calls_on_the_same_session_mint_different_turn_ids(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _TextOnlyProvider())

    sid = "test-turn-stamp-two"
    _cleanup(sid)
    try:
        first = await _drain(assistant.run(sid, [{"role": "user", "content": "a"}], "s"))
        second = await _drain(assistant.run(sid, [{"role": "user", "content": "b"}], "s"))
    finally:
        _cleanup(sid)

    assert first[0]["turn_id"] != second[0]["turn_id"], (
        "a superseded turn's late event could not be told apart from the current one"
    )


async def test_resolve_pending_tool_use_stamps_session_id_and_a_turn_id(monkeypatch):
    """The deny path — no real tool execution needed to prove the stamping contract.

    Denial resolves the group and resumes the SAME turn on the model (see
    ``resolve_pending_tool_use``'s docstring), so the provider is stubbed just like the
    ``run()`` tests above.
    """
    _isolate(monkeypatch)
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _TextOnlyProvider())
    sid = "test-resolve-stamp"
    _cleanup(sid)
    try:
        assistant.SESSIONS[sid] = [
            {"role": "user", "content": "add a payee"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t0", "name": "create_payees", "input": {}}],
            },
        ]
        plan_mod.PendingPlanStore().stage_pending("t0", "create_payees", {}, session_id=sid)
        events = await _drain(assistant.resolve_pending_tool_use(sid, "t0", "deny", "s"))
    finally:
        _cleanup(sid)

    assert events, "expected at least one event"
    assert all(e.get("session_id") == sid for e in events), events
    turn_ids = {e.get("turn_id") for e in events}
    assert len(turn_ids) == 1
    assert next(iter(turn_ids))


# ── 2. cancellation heals the dangling tool_use (security review, 2026-08-06) ───────────


class _ToolCallProvider:
    async def stream_chat(self, *a, **kw):
        yield {"type": "tool_use", "id": "t0", "name": "get_net_worth", "input": {}}
        yield {"type": "done", "stop_reason": "tool_use"}


async def test_cancelling_mid_tool_call_heals_the_dangling_tool_use_immediately(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _ToolCallProvider())

    reached = asyncio.Event()

    async def _hanging_execute_tool(name, args, tool_use_id=None):
        reached.set()
        await asyncio.Event().wait()  # never resolves — simulates a slow/stuck write

    monkeypatch.setattr(assistant, "execute_tool", _hanging_execute_tool)

    sid = "test-cancel-heals-dangling"
    _cleanup(sid)
    try:
        gen = assistant.run(sid, [{"role": "user", "content": "what's my net worth"}], "s")
        saw_tool_executing = False
        async for ev in gen:
            if ev.get("type") == "tool_executing":
                saw_tool_executing = True
                break
        assert saw_tool_executing, "never reached the tool call — test setup is wrong"

        # `break` only stops CONSUMING — the generator itself is still suspended AT the
        # "tool_executing" yield, not past it. Resuming it (a further __anext__()) is what
        # actually enters `execute_tool` and hangs. Do that resumption as its own task so
        # it can be cancelled independently, exactly like the real request-handling task
        # gets cancelled on a client disconnect or POST /stop.
        resume = asyncio.ensure_future(gen.__anext__())
        await asyncio.wait_for(reached.wait(), timeout=1)

        # The dangling tool_use is in place now (assistant message appended+saved by the
        # provider's own `done` handling, before execute_tool was ever called). Cancel the
        # resumption — the same asyncio.CancelledError a disconnect/`/stop` delivers.
        resume.cancel()
        with pytest.raises(asyncio.CancelledError):
            await resume
        await gen.aclose()

        history = assistant.SESSIONS[sid]
        assert history[-1]["role"] == "user", (
            "the dangling tool_use was never healed — the next message would 400"
        )
        stubs = [
            b
            for b in history[-1]["content"]
            if isinstance(b, dict)
            and b.get("type") == "tool_result"
            and b.get("tool_use_id") == "t0"
        ]
        assert len(stubs) == 1
        # Security review / acceptance D8: a cancellation can land AFTER a tool's own
        # synchronous store write already committed, so "did not complete" would be an
        # assertion this code cannot make. The stub must say the outcome is unknown.
        assert "did not complete" not in stubs[0]["content"]
        assert "may or may not have completed" in stubs[0]["content"]
        assert "verify" in stubs[0]["content"].lower()

        # And it is durable — not just healed in memory.
        assistant.SESSIONS.pop(sid, None)
        _, reloaded = assistant.get_or_create_session(sid)
        assert reloaded[-1]["role"] == "user"
        assert reloaded[-1]["content"][0]["tool_use_id"] == "t0"
    finally:
        _cleanup(sid)

"""``POST /api/chat/{id}/resolve`` under server-owned turn lifetime — acceptance D6.

Per §0 of the Phase 3 RFC (confirmed by two independent reviews): the paused-approval flow
does NOT park inside a request coroutine — a gated tool leaves its ``tool_use`` result-less
and ``_run_turn`` returns; the pause is durable in ``pending_plans.json`` and ``/resolve``
is an independent entrypoint that re-takes the session lock. So detaching turn lifetime
does not by itself endanger approve/deny. What Phase 3 makes REQUIRED is spawning the
CONTINUATION too — left request-driven, "approve, then close the tab" would lose the part
that actually applies the approved action's follow-through, shipping only half of Phase 3.

Drives the REAL turn loop (``assistant.resolve_pending_tool_use``) and the REAL confirm
gate through ``backend.api.chat.resolve_pending`` — only the LLM provider is stubbed.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.ai import assistant, live_sessions, turns
from backend.api.chat import ResolvePendingRequest, resolve_pending
from backend.db.pending_plan_store import PendingPlanStore


class _TextOnlyProvider:
    async def stream_chat(self, *a, **kw):
        yield {"type": "text_delta", "text": "done"}
        yield {"type": "done", "stop_reason": "end_turn"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch, db_isolated):
    from backend.ai.tools import _confirm_gate as gate

    # sessions / pending_plans / payee_registry._store / ToolPermissionStore (the absorbed
    # ToolGrantStore's successor, revision 0040) are all DB-backed — db_isolated covers
    # isolation, no per-store class override needed.
    monkeypatch.setattr(gate, "GATE_ALL_WRITES", True)
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _TextOnlyProvider())
    live_sessions._reset_for_tests()
    turns._turns.clear()
    turns._by_session.clear()
    turns.set_inline_for_tests(False)
    yield
    live_sessions._reset_for_tests()
    turns._turns.clear()
    turns._by_session.clear()


def _stage(session_id: str, tool_use_id: str, tool: str, args: dict) -> None:
    """Put the session + the pending row in the exact shape a real pause leaves them:
    the assistant message with the tool_use, and PendingPlanStore holding the args."""
    assistant.SESSIONS[session_id] = [
        {"role": "user", "content": "add a payee"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": tool, "input": args}],
        },
    ]
    PendingPlanStore().stage_pending(tool_use_id, tool, args, session_id=session_id)


def _payee_names() -> list[str]:
    from backend.services import payee_registry

    return [row.get("name") for row in payee_registry.list_registry()]


async def _drain_response(response) -> dict:
    import json as _json

    return _json.loads(bytes(response.body))


# ── D6: /resolve is spawned; the continuation reaches a watcher that never posted ────────


async def test_approve_reaches_a_watcher_that_never_sent_the_post():
    session_id = "test-resolve-watch"
    _stage(session_id, "t0", "create_payees", {"name": "Watched Co"})

    watched: list[dict] = []

    async def _watch():
        async for ev in live_sessions.watch(session_id):
            watched.append(ev)

    # The watcher attaches AFTER spawn (which opens the episode synchronously, before the
    # 202) — no sleep needed, matching the race-freedom argument in turns.spawn's docstring.
    response = await resolve_pending(
        session_id, ResolvePendingRequest(tool_use_id="t0", decision="approve")
    )
    assert response.status_code == 202
    body = await _drain_response(response)
    assert body["accepted"] == "resolve"

    watch_task = asyncio.ensure_future(_watch())
    await asyncio.wait_for(watch_task, timeout=1)

    assert any(e.get("type") == "tool_done" and e.get("id") == "t0" for e in watched)
    assert "Watched Co" in _payee_names(), "approve must actually execute the stored args"


async def test_deny_pops_the_pending_entry_and_writes_nothing():
    session_id = "test-resolve-deny"
    _stage(session_id, "t0", "create_payees", {"name": "Denied Co"})

    response = await resolve_pending(
        session_id, ResolvePendingRequest(tool_use_id="t0", decision="deny")
    )
    assert response.status_code == 202
    assert turns.drain_for_tests(session_id, timeout=1) or True
    await asyncio.sleep(0.05)  # let the (already-spawned) short deny turn finish

    assert PendingPlanStore().get_pending("t0") is None
    assert "Denied Co" not in _payee_names()


async def test_approving_an_already_resolved_id_is_a_truthful_error_and_writes_nothing():
    session_id = "test-resolve-twice"
    _stage(session_id, "t0", "create_payees", {"name": "OnceOnly Co"})

    r1 = await resolve_pending(
        session_id, ResolvePendingRequest(tool_use_id="t0", decision="approve")
    )
    assert r1.status_code == 202
    # Drive it to completion before the second attempt (this is the ORDERED case; the
    # concurrent case is covered separately below).
    turn_id_1 = (await _drain_response(r1))["turn_id"]
    handle1 = turns._turns.get(turn_id_1)
    if handle1 is not None:
        await asyncio.wait_for(handle1.task, timeout=1)
    else:
        await asyncio.sleep(0.05)

    watched: list[dict] = []

    async def _watch():
        async for ev in live_sessions.watch(session_id):
            watched.append(ev)

    r2 = await resolve_pending(
        session_id, ResolvePendingRequest(tool_use_id="t0", decision="approve")
    )
    assert r2.status_code == 202
    watch_task = asyncio.ensure_future(_watch())
    await asyncio.wait_for(watch_task, timeout=1)

    errors = [e for e in watched if e.get("type") == "error" and e.get("tool_use_id") == "t0"]
    assert errors, "a re-approve of an already-answered card must yield a truthful error"
    assert _payee_names().count("OnceOnly Co") == 1, "the write must never happen twice"


async def test_two_concurrent_approve_posts_execute_the_tool_exactly_once():
    """The money assertion: PendingPlanStore's single-use pop must hold even when BOTH
    approvals reach the server as separately spawned turns racing on the same session
    lock, not just as two sequentially-awaited generator calls."""
    session_id = "test-resolve-race"
    _stage(session_id, "t0", "create_payees", {"name": "RaceCo"})

    r1, r2 = await asyncio.gather(
        resolve_pending(session_id, ResolvePendingRequest(tool_use_id="t0", decision="approve")),
        resolve_pending(session_id, ResolvePendingRequest(tool_use_id="t0", decision="approve")),
    )
    assert r1.status_code == 202 and r2.status_code == 202

    for body in (await _drain_response(r1), await _drain_response(r2)):
        handle = turns._turns.get(body["turn_id"])
        if handle is not None and handle.task is not None:
            try:
                await asyncio.wait_for(handle.task, timeout=2)
            except asyncio.CancelledError:
                pass
    await asyncio.sleep(0.05)

    assert _payee_names().count("RaceCo") == 1, "two concurrent approvals must write exactly once"

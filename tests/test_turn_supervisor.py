"""``backend.ai.turns`` — server-owned turn lifetime (concurrent-conversations RFC Phase 3).

D7 (BLOCKING) is the reason this module exists: ``set_cost_surface("web")`` used to live
INSIDE ``api/chat.py``'s ``event_stream()`` generator, which Phase 3 deletes.
``contextvars`` are copied at ``asyncio.create_task`` time, so a task spawned from the
route handler's own context starts with ``cost_surface`` still ``None`` unless something
INSIDE the task sets it before the first ``await`` — and ``ai.tools._confirm_gate.
should_gate`` fails OPEN on an unrecognised surface by design, so a bug here means every
gated write executes with no approval card. ``test_a_gated_write_still_stages_a_card_
through_the_detached_path`` is the regression test: it deliberately does NOT stub
``get_cost_surface`` (unlike ``tests/test_resolve_pending_tool_use.py``'s ``env`` fixture,
which sidesteps this exact question) — it drives a REAL gated tool through a REAL
``asyncio.create_task`` and asserts on the GATE (a ``proposed_change`` event + a
``PendingPlanStore`` entry, and that the write did NOT happen), never on the cost log,
because a cost row falls back to a legacy attribution and looks fine even when the gate is
wide open — that is exactly how this bug hid the first time.

Hermetic: no network, no LLM — a scripted/validating fake provider (the same pattern
``tests/test_resolve_pending_tool_use.py`` uses), isolated stores.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.ai import assistant, live_sessions, turns
from backend.core.cost_context import clear_cost_surface, get_cost_surface


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch, db_isolated):
    # Sessions (assistant._sessions_dir) and PendingPlanStore (was pending_plans.json) are
    # both DB-backed now — db_isolated covers isolation for both, no class-level __init__
    # override needed (every PendingPlanStore() call resolves the same isolated engine
    # lazily, exactly like ToolPermissionStore below).
    # ToolPermissionStore (the absorbed ToolGrantStore's successor, revision 0040) is
    # DB-backed — db_isolated covers isolation, no class-level override needed (every
    # ToolPermissionStore() call resolves the same isolated engine lazily).
    live_sessions._reset_for_tests()
    turns._turns.clear()
    turns._by_session.clear()
    turns.set_inline_for_tests(False)
    # This ContextVar is process-global — a leaked "web" from an earlier test would make
    # the D7 regression test pass for the WRONG reason (an ambient surface, not the fix).
    clear_cost_surface()
    yield
    live_sessions._reset_for_tests()
    turns._turns.clear()
    turns._by_session.clear()
    clear_cost_surface()


def _error_event(exc: BaseException) -> dict:
    return {"type": "error", "message": str(exc)}


async def _await_handle(handle: "turns.TurnHandle") -> None:
    assert handle.task is not None
    try:
        await handle.task
    except asyncio.CancelledError:
        pass


# ── D7 (BLOCKING): the confirm gate must still fire on a detached turn ──────────────────


class _CreatePayeeProvider:
    """Emits one ``create_payees`` tool_use, then (if resumed) plain text."""

    def __init__(self):
        self.calls = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "tool_use",
                "id": "t0",
                "name": "create_payees",
                "input": {"name": "SecurityHole Co"},
            }
            yield {"type": "done", "stop_reason": "tool_use"}
        else:
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "stop_reason": "end_turn"}


@pytest.fixture
def gated_env(tmp_path, monkeypatch, db_isolated):
    """A card-capable, GATE_ALL_WRITES posture with NO cost_surface stub — see the module
    docstring for why that omission is the entire point of this fixture."""
    from backend.ai.tools import _confirm_gate as gate

    monkeypatch.setattr(gate, "GATE_ALL_WRITES", True)
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    provider = _CreatePayeeProvider()
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)
    return provider


async def test_a_gated_write_still_stages_a_card_through_the_detached_path(gated_env):
    from backend.db.pending_plan_store import PendingPlanStore
    from backend.services import conversation_approval, payee_registry

    session_id = "test-d7-gate"
    # A brand-new (never-persisted) session now defaults to auto-approve (2026-09-06,
    # "a brand-new conversation starts in auto approval mode") — orthogonal to what THIS
    # test pins (cost_surface propagation into a detached task), so pin this conversation
    # to manual explicitly rather than ride a default this test does not own.
    conversation_approval.set_mode(session_id, "manual")

    def make_events(turn_id: str):
        return assistant.run(
            session_id, [{"role": "user", "content": "add a payee"}], "s", turn_id=turn_id
        )

    # This IS the vulnerability: cost_surface is unset in THIS (the caller's) context —
    # exactly the shape create_task copies into the detached task. Without _drive setting
    # it FIRST, the gate would see None and fail open.
    assert get_cost_surface() is None

    handle = turns.spawn(
        session_id, "chat", make_events, error_event=_error_event, cost_surface="web"
    )
    await _await_handle(handle)

    # The gate fired: a card was staged, not executed.
    entry = PendingPlanStore().get_pending("t0")
    assert entry is not None, "the gated tool call must be STAGED, not executed"
    assert entry.get("tool") == "create_payees"

    # The wire got the card too — checked via the replay buffer the grace window retains.
    replayed = list(live_sessions._replay.get(session_id) or ())
    proposed = [e for e in replayed if e.get("type") == "proposed_change"]
    assert len(proposed) == 1, "expected exactly one proposed_change event"
    assert proposed[0]["tool_use_id"] == "t0"

    # And the write genuinely did NOT happen — the payee must not exist.
    names = [row.get("name") for row in payee_registry.list_registry()]
    assert "SecurityHole Co" not in names, (
        "the gate failed open: a detached turn executed a mutating tool with no approval"
    )

    # Cleanly ended and journaled — a real bug here would ALSO tend to leave the registry
    # dirty, since the fix and the cleanup live in the same function.
    assert turns._turns == {}
    assert turns._by_session == {}
    rows = {row["id"]: row for row in assistant.list_sessions()}
    assert rows[session_id]["last_turn_status"] == "awaiting_approval"


async def test_cost_surface_is_cleared_after_the_turn_so_it_cannot_leak_into_the_next_task(
    gated_env,
):
    """Belt and braces: clear_cost_surface() in _drive's finally must actually run, so a
    later unrelated task on the same worker never inherits a stale "web" surface."""
    session_id = "test-d7-clears"

    def make_events(turn_id: str):
        return assistant.run(
            session_id, [{"role": "user", "content": "add a payee"}], "s", turn_id=turn_id
        )

    handle = turns.spawn(
        session_id, "chat", make_events, error_event=_error_event, cost_surface="web"
    )
    await _await_handle(handle)

    # _drive ran (and cleared) INSIDE the task's own copied context — this assertion is
    # about THIS (the test's) context, which was never set and must still read None.
    assert get_cost_surface() is None


# ── registry mechanics ───────────────────────────────────────────────────────────────────


async def test_spawn_registers_and_unregisters(monkeypatch):
    async def source(turn_id: str):
        yield {"type": "turn_done", "usage": {}}

    handle = turns.spawn("s1", "chat", source, error_event=_error_event)
    assert "s1" in turns._by_session
    assert handle.turn_id in turns._turns
    await _await_handle(handle)
    assert turns._turns == {}
    assert turns._by_session == {}


async def test_the_task_is_strongly_referenced_across_a_gc(monkeypatch):
    """A bare asyncio.create_task() result is only WEAKLY referenced by the loop — spawn()
    must hold its own strong reference or a GC pass can reap the task mid-turn."""
    import gc

    reached = asyncio.Event()
    proceed = asyncio.Event()

    async def source(turn_id: str):
        reached.set()
        await proceed.wait()
        yield {"type": "turn_done", "usage": {}}

    handle = turns.spawn("s-gc", "chat", source, error_event=_error_event)
    await asyncio.wait_for(reached.wait(), timeout=1)
    gc.collect()
    assert not handle.task.done(), "the task was garbage-collected mid-turn"
    proceed.set()
    await _await_handle(handle)


async def test_a_raising_factory_still_publishes_an_error_ends_and_journals(monkeypatch):
    def make_events(turn_id: str):
        raise RuntimeError("setup exploded")

    handle = turns.spawn("s-raise", "chat", make_events, error_event=_error_event)
    await _await_handle(handle)

    assert not live_sessions.is_live("s-raise")
    assert turns._turns == {}
    rows = {row["id"]: row for row in assistant.list_sessions()}
    assert rows["s-raise"]["last_turn_status"] == "error"


async def test_cancel_hits_only_the_executing_turn_on_a_session():
    reached = asyncio.Event()
    hang = asyncio.Event()

    async def hanging(turn_id: str):
        reached.set()
        await hang.wait()
        yield {"type": "turn_done", "usage": {}}
        yield {"type": "never reached"}

    handle = turns.spawn("s-cancel", "chat", hanging, error_event=_error_event)
    await asyncio.wait_for(reached.wait(), timeout=1)

    result = turns.cancel("s-cancel")
    assert result == {"stopped": True, "turn_id": handle.turn_id}
    await _await_handle(handle)
    assert not live_sessions.is_live("s-cancel")


def test_cancel_with_a_stale_turn_id_is_a_truthful_noop():
    result = turns.cancel("s-nothing", turn_id="turn_stale")
    assert result == {"stopped": False, "reason": "not_running"}


def test_cancel_with_no_turn_running_is_a_truthful_noop():
    result = turns.cancel("s-empty")
    assert result == {"stopped": False, "reason": "not_running"}


async def test_cancel_rejects_a_turn_id_belonging_to_a_different_session():
    async def hanging(turn_id: str):
        await asyncio.Event().wait()
        yield {"type": "unreachable"}

    handle = turns.spawn("s-owner", "chat", hanging, error_event=_error_event)
    await asyncio.sleep(0)

    result = turns.cancel("s-someone-else", turn_id=handle.turn_id)
    assert result == {"stopped": False, "reason": "not_running"}
    assert not handle.task.done()

    turns.cancel("s-owner", turn_id=handle.turn_id)
    await _await_handle(handle)


# ── wall-clock timeout ───────────────────────────────────────────────────────────────────


async def test_deadline_cancels_the_turn_and_journals_timeout(monkeypatch):
    monkeypatch.setattr(turns, "_turn_limit_s", lambda: 0.05)

    started = asyncio.Event()

    async def hangs_forever(turn_id: str):
        started.set()
        await asyncio.Event().wait()
        yield {"type": "unreachable"}

    handle = turns.spawn("s-timeout", "chat", hangs_forever, error_event=_error_event)
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.wait_for(_await_handle(handle), timeout=1)

    rows = {row["id"]: row for row in assistant.list_sessions()}
    assert rows["s-timeout"]["last_turn_status"] == "timeout"
    replayed = list(live_sessions._replay.get("s-timeout") or ())
    timeout_errors = [e for e in replayed if e.get("type") == "error"]
    assert timeout_errors, "expected a timeout error event on the wire"
    message = timeout_errors[0]["message"]
    assert timeout_errors[0]["code"] == "turn_time_limit"
    # The way out is named: a faster model, or a higher limit.
    assert "faster model" in message and "Turn time limit" in message


async def test_no_limit_lets_a_turn_outlive_the_default(monkeypatch):
    """0 = no limit: the env default (here 50ms) must not stop the turn."""
    import dataclasses

    from backend.core import config as config_mod

    fast_settings = dataclasses.replace(config_mod.get_settings(), turn_timeout_s=0.05)
    monkeypatch.setattr(turns, "get_settings", lambda: fast_settings)
    monkeypatch.setattr(turns, "_turn_limit_s", lambda: 0)

    async def slow(turn_id: str):
        await asyncio.sleep(0.2)
        yield {"type": "text", "content": "done"}

    handle = turns.spawn("s-unlimited", "chat", slow, error_event=_error_event)
    assert handle.limit_s == 0
    await asyncio.wait_for(_await_handle(handle), timeout=2)

    rows = {row["id"]: row for row in assistant.list_sessions()}
    assert rows["s-unlimited"]["last_turn_status"] == "ok"


def test_limit_phrase_reads_naturally():
    assert turns._limit_phrase(1200) == "20-minute"
    assert turns._limit_phrase(7200) == "2-hour"
    assert turns._limit_phrase(5) == "1-minute"


# ── boot recovery / shutdown ─────────────────────────────────────────────────────────────


def test_recover_interrupted_turns_corrects_a_stale_running_status():
    assistant.upsert_session_meta("s-crashed", last_turn_status="running")
    assistant.upsert_session_meta("s-fine", last_turn_status="ok")

    outcome = turns.recover_interrupted_turns()

    assert outcome == {"recovered": 1}
    rows = {row["id"]: row for row in assistant.list_sessions()}
    assert rows["s-crashed"]["last_turn_status"] == "interrupted"
    assert rows["s-fine"]["last_turn_status"] == "ok"


def test_recover_interrupted_turns_is_idempotent():
    assistant.upsert_session_meta("s-idem", last_turn_status="running")
    turns.recover_interrupted_turns()
    outcome_second = turns.recover_interrupted_turns()
    assert outcome_second == {"recovered": 0}


async def test_shutdown_cancels_every_registered_turn_and_journals_cancelled():
    reached1 = asyncio.Event()
    reached2 = asyncio.Event()

    async def hangs(reached: asyncio.Event, turn_id: str):
        reached.set()
        await asyncio.Event().wait()
        yield {"type": "unreachable"}

    turns.spawn("s-shutdown-a", "chat", lambda tid: hangs(reached1, tid), error_event=_error_event)
    turns.spawn("s-shutdown-b", "chat", lambda tid: hangs(reached2, tid), error_event=_error_event)
    await asyncio.wait_for(asyncio.gather(reached1.wait(), reached2.wait()), timeout=1)

    await asyncio.wait_for(turns.shutdown(), timeout=1)

    assert turns._turns == {}
    rows = {row["id"]: row for row in assistant.list_sessions()}
    assert rows["s-shutdown-a"]["last_turn_status"] == "cancelled"
    assert rows["s-shutdown-b"]["last_turn_status"] == "cancelled"
    # A clean shutdown must never leave "running" behind for recover_interrupted_turns to
    # (wrongly) treat as a crash on the next boot.
    outcome = turns.recover_interrupted_turns()
    assert outcome == {"recovered": 0}

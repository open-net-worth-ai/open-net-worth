"""One turn lifecycle for chat and Automations.

ADR ``2026-08-06-one-turn-lifecycle-for-chat-and-automations``. An Automation run and a chat
turn are the same thing with different triggers, so they run through the same ``turns._drive``
via two entry points: ``spawn`` (new task) and ``run_inline`` (the caller's task).

These pin the properties that only hold BECAUSE of that: a run is registered, therefore it is
cancellable and injectable; and the cost surface is bound inside the turn's own task, which is
what keeps the confirm gate from failing open.
"""

from __future__ import annotations

import asyncio

import pytest

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.turns as turns
from backend.ai.automation_runner_adapter import automation_runner
from backend.core.automation_runner import (
    STOPPED_SUMMARY_NOTE,
    AutomationRunnerRequest,
    AutomationRunnerResult,
)
from backend.core.cost_context import get_cost_surface
from backend.core.turn_cancel import record_cancel_reason, take_cancel_reason
from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_runtime_store import AutomationRuntimeStore
from backend.db.automation_store import AutomationStore
from backend.services.automations import runs as runs_mod


@pytest.fixture(autouse=True)
def _isolate(db_isolated, tmp_path, monkeypatch):
    import backend.paths as paths

    monkeypatch.setattr(paths, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(assistant, "save_session_durable", lambda *a, **k: None)
    assistant.SESSIONS.clear()
    live_sessions._reset_for_tests()


def _request(session_id: str, *, run_id: str = "run_a", automation_id: str = "automation_a"):
    return AutomationRunnerRequest(
        automation_id=automation_id,
        run_id=run_id,
        session_id=session_id,
        title="Nightly review",
        instructions="Review it",
    )


async def test_a_running_automation_is_the_executing_turn(monkeypatch):
    """The single fact every other Automation/chat parity property descends from.

    ``api/chat.py`` routes a mid-turn message on ``executing_turn_id``. While runs drove
    ``assistant.run`` outside the registry this was ``None`` for them, so a message typed
    during a run became a followup QUEUED BEHIND it instead of being read during it — and
    ``turns.cancel`` had nothing to cancel, which is why Stop did not exist for runs.
    """
    session_id = "automation-run-registered"
    assistant.SESSIONS[session_id] = [{"role": "user", "content": "Review it"}]
    seen: list[str | None] = []

    async def fake_run(*args, **kwargs):
        seen.append(turns.executing_turn_id(session_id))
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)
    await automation_runner(_request(session_id))

    assert seen and seen[0] is not None, "a run must be registered while it is running"
    assert turns.executing_turn_id(session_id) is None, "and unregistered once it ends"


async def test_the_cost_surface_is_bound_inside_the_run_s_own_task(monkeypatch):
    """Confirm-gate fail-open regression (security review finding 1).

    ``should_gate`` fails OPEN on an unrecognised surface by design, and with
    the gate always armed that means every mutating tool executing with no approval card. The
    surface must therefore be set before the run's first tool call. This asserts the value
    observed FROM INSIDE the turn, which is the only place it matters.
    """
    session_id = "automation-run-surface"
    assistant.SESSIONS[session_id] = [{"role": "user", "content": "Review it"}]
    observed: list[str | None] = []

    async def fake_run(*args, **kwargs):
        observed.append(get_cost_surface())
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)
    await automation_runner(_request(session_id))

    assert observed == ["automation"]


async def test_a_run_is_cancellable_through_the_ordinary_turn_cancel(monkeypatch):
    """``turns.cancel`` reaches a run — the mechanism behind the Stop button."""
    session_id = "automation-run-cancellable"
    assistant.SESSIONS[session_id] = [{"role": "user", "content": "Review it"}]
    started = asyncio.Event()

    async def fake_run(*args, **kwargs):
        started.set()
        await asyncio.sleep(30)
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)
    task = asyncio.create_task(automation_runner(_request(session_id)))
    await asyncio.wait_for(started.wait(), timeout=2)

    assert turns.cancel(session_id)["stopped"] is True

    with pytest.raises(asyncio.CancelledError):
        await task
    assert turns.executing_turn_id(session_id) is None


async def test_a_stopped_run_settles_to_cancelled_and_releases_its_claim():
    """Without this the row sits at ``running`` forever holding the runtime claim, so the
    Automation could never be run again — a state a Stop button must not be able to create.
    """
    automation = AutomationStore().create(
        {"name": "Nightly", "instructions": "Review it", "trigger": {"kind": "manual"}}
    )
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-run-stopped"}
    )
    AutomationRuntimeStore().put_for_automation(
        automation["id"], {"running_run_id": run["id"], "running_since": "2026-08-06T00:00:00Z"}
    )

    runs_mod._settle_cancelled_run(run["id"])

    settled = AutomationRunStore().find_by_id(run["id"])
    assert settled["status"] == "cancelled"
    assert settled["finished_at"], "a terminal run is stamped finished"
    assert STOPPED_SUMMARY_NOTE in settled["summary"], (
        "a run stopped mid-tool-call may have committed a write — the summary must say the "
        "outcome is unknown rather than imply nothing happened"
    )
    runtime = AutomationRuntimeStore().get_for_automation(automation["id"]) or {}
    assert runtime.get("running_run_id") in (None, ""), "the claim must be released"


async def test_a_cancelled_run_says_which_cause_when_the_process_knows_it():
    """ "Either from Stop or by the server restarting" is a hedge over a KNOWN fact.

    ``turns`` sets the reason before it cancels anything; the run row is settled in
    ``services``, which cannot import ``ai`` and so used to guess. On 2026-08-21 a dev
    reload cancelled a run mid-task and the card told the owner it might have been a Stop
    they never pressed. ``core.turn_cancel`` carries the reason across that boundary.
    """
    automation = AutomationStore().create(
        {"name": "Reloaded", "instructions": "Review it", "trigger": {"kind": "manual"}}
    )
    session_id = "automation-run-shutdown"
    run = AutomationRunStore().start({"automation_id": automation["id"], "session_id": session_id})
    record_cancel_reason(session_id, "shutdown")

    runs_mod._settle_cancelled_run(run["id"])

    summary = AutomationRunStore().find_by_id(run["id"])["summary"]
    assert "the server restarted" in summary
    assert "either from Stop" not in summary, "the hedge is for when the cause is UNKNOWN"
    assert take_cancel_reason(session_id) is None, (
        "the reason is consumed, so a later run on the same conversation cannot inherit it"
    )


async def test_a_cancelled_turn_records_why_for_the_conversation_to_show(monkeypatch):
    """The run card is not the only surface. Opening the conversation itself must explain.

    ``_drive`` deliberately publishes no synthetic error EVENT on a cancel — a Stop is not a
    failure. But it must still JOURNAL the explanation, or a reader who opens the
    conversation later gets the tool pills the turn got through and nothing else (owner,
    2026-08-22). Asserts on the journalled call rather than the stored row because this
    module's autouse fixture stubs the write out.
    """
    session_id = "chat-cancelled-explains"
    journalled: list[dict] = []
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda _sid, **kw: journalled.append(kw))
    started = asyncio.Event()

    async def fake_run(*_a, **_k):
        started.set()
        await asyncio.sleep(60)
        yield {}

    monkeypatch.setattr(assistant, "run", fake_run)
    task = asyncio.create_task(
        turns.run_inline(
            session_id,
            "automation",
            lambda _t: assistant.run(),
            error_event=lambda exc: {"type": "error", "message": str(exc)},
            cost_surface="automation",
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)

    turns.cancel(session_id, reason="shutdown")
    with pytest.raises(asyncio.CancelledError):
        await task

    terminal = journalled[-1]
    assert terminal["last_turn_status"] == "cancelled"
    assert "the server restarted" in terminal["last_turn_error"], (
        "the conversation must be able to say WHICH cause, not that something vaguely ended"
    )
    assert "either from Stop" not in terminal["last_turn_error"]


async def test_a_cancelled_run_settles_the_task_it_was_working_on():
    """The interrupted task is never coming back to close itself.

    Left ``in_progress``, the card kept drawing a dead run as one still working — which is
    what the owner saw on 2026-08-21, nineteen hours after the run had been cancelled.
    """
    automation = AutomationStore().create(
        {"name": "Mid-task", "instructions": "Review it", "trigger": {"kind": "manual"}}
    )
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-run-midtask"}
    )
    AutomationRunStore().update_by_id(
        run["id"],
        {
            "plan": [
                {"id": "1", "label": "Read accounts", "status": "completed", "done": True},
                {"id": "2", "label": "Read categories", "status": "in_progress"},
                {"id": "3", "label": "Log the rows", "status": "pending"},
            ],
            "steps": [{"tool_use_id": "t1", "label": "List categories", "status": "running"}],
        },
    )

    runs_mod._settle_cancelled_run(run["id"])

    settled = AutomationRunStore().find_by_id(run["id"])
    assert [s["status"] for s in settled["plan"]] == ["completed", "failed", "pending"]
    assert settled["steps"][0]["status"] == "failed"


async def test_stopping_a_run_does_not_consume_a_budgeted_run():
    """``max_runs`` caps how many times a scheduled Automation may fire before it pauses
    itself. You stopped this one, so it should not spend that budget."""
    automation = AutomationStore().create(
        {
            "name": "Capped",
            "instructions": "Review it",
            "trigger": {"kind": "schedule", "type": "daily", "time": "09:00"},
            "max_runs": 3,
        }
    )
    AutomationStore().update_by_id(automation["id"], {"run_count": 1})
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-run-capped"}
    )

    runs_mod._settle_cancelled_run(run["id"])

    after = AutomationStore().find_by_id(automation["id"])
    assert after["run_count"] == 1, "a stopped run does not count against max_runs"
    assert after["last_status"] == "cancelled"
    assert after["next_run_at"], "but it must still be rescheduled — nothing else does that"


async def test_stopping_a_follow_up_turn_settles_cancelled_like_stopping_the_run():
    """One user action, one status.

    A follow-up turn on an Automation conversation runs through ``turns.spawn`` while the
    original run runs through ``run_inline``. Before this, stopping the follow-up recorded
    ``failed`` (its cancellation fell through the generic error path) while stopping the run
    recorded ``cancelled`` — the same tap reading two different ways depending on which turn
    of the conversation it landed on.
    """
    from backend.ai.automation_runner_adapter import _EventProjection

    projection = _EventProjection(_request("automation-run-followup"))
    projection.cancelled = True

    result = projection.result(pending_tool_ids=[])

    assert result.status == "cancelled"
    assert result.error is None, "a deliberate Stop is not an error"
    assert STOPPED_SUMMARY_NOTE in result.summary


async def test_stopping_a_follow_up_turn_also_spares_the_budgeted_run():
    automation = AutomationStore().create(
        {
            "name": "Capped follow-up",
            "instructions": "Review it",
            "trigger": {"kind": "schedule", "type": "daily", "time": "09:00"},
            "max_runs": 3,
        }
    )
    AutomationStore().update_by_id(automation["id"], {"run_count": 2})
    baseline = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-run-followup-capped"}
    )

    runs_mod.reconcile_automation_continuation(
        AutomationStore().find_by_id(automation["id"]),
        baseline,
        AutomationRunnerResult(status="cancelled", session_id=baseline["session_id"]),
    )

    assert AutomationRunStore().find_by_id(baseline["id"])["status"] == "cancelled"
    assert AutomationStore().find_by_id(automation["id"])["run_count"] == 2


async def test_a_stopped_run_can_be_resumed():
    """``cancelled`` is terminal, not dead: the conversation is still reopenable, exactly as
    a completed or failed run is."""
    automation = AutomationStore().create(
        {"name": "Resumable", "instructions": "Review it", "trigger": {"kind": "manual"}}
    )
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-run-resumable"}
    )
    AutomationRunStore().transition(run["id"], {"running"}, "cancelled", {})

    _, started = runs_mod.begin_automation_continuation("automation-run-resumable")

    assert started["status"] == "running"

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import backend.services.automations.runs as runs_mod
from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_store import AutomationStore
from backend.services.automations.runs import run_due_automations
from backend.services.automations.service import apply_automation_change


@pytest.fixture
def isolated_automation_paths(db_isolated):
    """AutomationStore is DB-backed now (plan 4d task 2) -- db_isolated covers it."""


async def _wait_for_terminal(run_id: str) -> dict:
    for _ in range(100):
        row = AutomationRunStore().find_by_id(run_id)
        if row and row["status"] in {"completed", "failed"}:
            return row
        await asyncio.sleep(0)
    raise AssertionError(f"run {run_id} did not finish")


@pytest.mark.asyncio
async def test_due_tick_isolates_runs_advances_recurring_and_pauses_exhausted_rows(
    isolated_automation_paths, monkeypatch
):

    now = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "backend.services.automations.service.now_utc",
        lambda: datetime(2026, 8, 2, 7, 0, tzinfo=timezone.utc),
    )
    recurring = apply_automation_change(
        {
            "op": "create",
            "automation": {
                "name": "Recurring",
                "instructions": "Run every day.",
                "trigger": {"kind": "schedule", "type": "daily", "time": "08:00", "tzid": "UTC"},
                "execution": {"kind": "lucius"},
            },
        }
    )
    one_shot = apply_automation_change(
        {
            "op": "create",
            "automation": {
                "name": "Once",
                "instructions": "Run once.",
                "trigger": {"kind": "schedule", "type": "once", "at": now.isoformat()},
                "execution": {"kind": "lucius"},
            },
        }
    )
    manual = apply_automation_change(
        {
            "op": "create",
            "automation": {
                "name": "Manual",
                "instructions": "Only on demand.",
                "trigger": {"kind": "manual"},
                "execution": {"kind": "lucius"},
            },
        }
    )

    async def fake_execute(automation, payload):
        if automation["id"] == one_shot["id"]:
            raise RuntimeError("one failure must not wedge another automation")
        return {"ok": True}

    # Stub the LUCIUS runner, not the deterministic executor: `execution.kind` is `lucius`
    # and nothing else since 2026-08-02, so a deterministic stub is never reached.
    from backend.core.automation_runner import (
        AutomationRunnerResult,
        register_automation_session_materializer,
    )

    # A run now mints a durable conversation before it executes; without a materializer
    # every run fails before reaching the runner at all.
    register_automation_session_materializer(
        lambda run, automation: str(run.get("session_id") or f"automation-run-{run['id']}")
    )

    async def fake_runner(request, event_sink=None):
        if request.automation_id == one_shot["id"]:
            raise RuntimeError("one failure must not wedge another automation")
        return AutomationRunnerResult(
            status="completed",
            session_id=request.session_id,
            summary="done",
            model="test",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
        )

    monkeypatch.setattr(runs_mod, "run_with_automation_runner", fake_runner)

    result = await run_due_automations(now)
    assert all("run_id" in item for item in result["results"]), result["results"]
    terminal = [await _wait_for_terminal(item["run_id"]) for item in result["results"]]

    assert result["ran"] == 2
    assert {row["status"] for row in terminal} == {"completed", "failed"}
    assert AutomationStore().find_by_id(recurring["id"])["next_run_at"] == (
        "2026-08-03T08:00:00+00:00"
    )
    assert AutomationStore().find_by_id(one_shot["id"])["status"] == "completed"
    assert AutomationStore().find_by_id(one_shot["id"])["next_run_at"] is None
    assert AutomationStore().find_by_id(manual["id"])["status"] == "active"
    assert AutomationStore().find_by_id(manual["id"])["next_run_at"] is None


@pytest.mark.asyncio
async def test_due_tick_skips_an_automation_already_holding_its_runtime_claim(
    isolated_automation_paths, monkeypatch
):
    """Repro: an Automation parked on an approval card (`notify` waiting on the
    user) sat `due` every tick for 5 days because its `next_run_at` cannot advance until
    the run settles. Retrying it anyway is guaranteed to raise "already running" — this
    pins that the tick now skips a claimed Automation instead of manufacturing a failure
    every 60 seconds for a state the Automations tab already shows as "Needs you".
    """
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    now = datetime(2025, 5, 12, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "backend.services.automations.service.now_utc",
        lambda: datetime(2025, 5, 12, 7, 0, tzinfo=timezone.utc),
    )
    stuck = apply_automation_change(
        {
            "op": "create",
            "automation": {
                "name": "Daily Trades Summary",
                "instructions": "Summarize trades.",
                "trigger": {"kind": "schedule", "type": "daily", "time": "08:00", "tzid": "UTC"},
                "execution": {"kind": "lucius"},
            },
        }
    )
    AutomationRuntimeStore().put_for_automation(
        stuck["id"], {"running_run_id": "run_parked_on_approval"}
    )

    called = False

    async def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("a claimed Automation must not be retried")

    monkeypatch.setattr(runs_mod, "run_automation", fail_if_called)

    result = await run_due_automations(now)

    assert called is False
    assert result["ran"] == 0
    assert result["errors"] == 0
    assert result["waiting"] == 1
    assert result["results"] == [{"id": stuck["id"], "waiting": True}]


@pytest.mark.asyncio
async def test_scheduler_wrapper_calls_the_single_automation_tick(monkeypatch):
    import backend.scheduler as scheduler

    calls = []

    async def fake_tick():
        calls.append(True)
        return {"ran": 0, "errors": 0, "results": []}

    monkeypatch.setattr("backend.services.automations.runs.run_due_automations", fake_tick)

    await scheduler._run_automations()

    assert calls == [True]


def test_scheduler_registers_one_sixty_second_automation_job(monkeypatch):
    import backend.scheduler as scheduler

    jobs = []

    class FakeScheduler:
        def add_job(self, fn, **kwargs):
            jobs.append((fn, kwargs))

    monkeypatch.setattr(scheduler, "_scheduler", FakeScheduler())

    scheduler._schedule_automations_job()

    assert len(jobs) == 1
    fn, kwargs = jobs[0]
    assert fn is scheduler._run_automations
    assert kwargs["id"] == "automations"
    assert kwargs["trigger"].interval.total_seconds() == 60


@pytest.mark.asyncio
async def test_a_pending_approval_does_not_block_the_next_scheduled_run(
    isolated_automation_paths, monkeypatch
):
    """Repro: "Daily Trades Summary" parked on a notify approval card.
    The card was never tapped, and because a waiting run held the Automation's runtime claim,
    every one of its own later slots was skipped as `waiting` — five days with no summary and
    no notice, and a manual run answering "automation is already running".

    The claim means "a run is EXECUTING", not "a run exists". A waiting run is doing nothing,
    so it holds no slot: the approval stays pending and the next scheduled run starts as
    normal.
    """
    from backend.db.automation_run_store import AutomationRunStore
    from backend.db.automation_store import AutomationStore

    monkeypatch.setattr(
        "backend.services.automations.service.now_utc",
        lambda: datetime(2025, 5, 4, 7, 0, tzinfo=timezone.utc),
    )
    automation = apply_automation_change(
        {
            "op": "create",
            "automation": {
                "name": "Daily Trades Summary",
                "instructions": "Summarize IBKR trades.",
                "trigger": {"kind": "schedule", "type": "daily", "time": "09:00", "tzid": "UTC"},
                "execution": {"kind": "lucius"},
            },
        }
    )
    AutomationStore().update_by_id(automation["id"], {"next_run_at": "2025-05-05T09:00:00+00:00"})
    parked = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-run-parked",
            "trigger": {"kind": "schedule", "scheduled_for": "2025-05-04T09:00:00+00:00"},
        }
    )
    AutomationRunStore().transition(parked["id"], {"running"}, "waiting", {})

    from backend.core.automation_runner import (
        AutomationRunnerResult,
        register_automation_session_materializer,
    )

    register_automation_session_materializer(
        lambda run, a: str(run.get("session_id") or f"automation-run-{run['id']}")
    )
    started: list[str] = []

    async def fake_runner(request, event_sink=None):
        started.append(request.run_id)
        return AutomationRunnerResult(
            status="completed",
            session_id=request.session_id,
            summary="done",
            model="test",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
        )

    monkeypatch.setattr(runs_mod, "run_with_automation_runner", fake_runner)

    result = await run_due_automations(datetime(2025, 5, 5, 9, 0, tzinfo=timezone.utc))

    assert result["ran"] == 1, "the next scheduled run must start, not be skipped as waiting"
    assert result["waiting"] == 0
    # The run executes as a background task, so settle it before asserting on the runner.
    for item in result["results"]:
        await _wait_for_terminal(item["run_id"])
    assert started and started[0] != parked["id"]
    # And the approval is untouched — it is still there for the user to answer.
    assert AutomationRunStore().find_by_id(parked["id"])["status"] == "waiting"


@pytest.mark.asyncio
async def test_a_run_that_is_actually_executing_still_blocks_a_second_one(
    isolated_automation_paths, monkeypatch
):
    """The claim keeps doing its real job. Only `waiting` stops holding it; a run that is
    genuinely `running` still admits no overlapping second run."""
    from backend.db.automation_run_store import AutomationRunStore
    from backend.db.automation_runtime_store import AutomationRuntimeStore
    from backend.db.automation_store import AutomationStore

    monkeypatch.setattr(
        "backend.services.automations.service.now_utc",
        lambda: datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc),
    )
    automation = apply_automation_change(
        {
            "op": "create",
            "automation": {
                "name": "Long job",
                "instructions": "Take a while.",
                "trigger": {"kind": "schedule", "type": "daily", "time": "09:00", "tzid": "UTC"},
                "execution": {"kind": "lucius"},
            },
        }
    )
    AutomationStore().update_by_id(automation["id"], {"next_run_at": "2026-08-04T09:00:00+00:00"})
    running = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-run-busy",
            "trigger": {"kind": "schedule", "scheduled_for": "2026-08-04T09:00:00+00:00"},
        }
    )
    AutomationRuntimeStore().put_for_automation(automation["id"], {"running_run_id": running["id"]})

    result = await run_due_automations(datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc))

    assert result["ran"] == 0 and result["waiting"] == 1
    assert AutomationRunStore().find_by_id(running["id"])["status"] == "running"


SLOT = "2026-08-04T09:00:00+00:00"
DAILY = {"kind": "schedule", "type": "daily", "time": "09:00", "tzid": "UTC"}


def _at(day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc)


def _automation_that_parks(monkeypatch, trigger: dict, *, while_running=None) -> tuple[dict, list]:
    """An Automation due at SLOT whose every run stops on an approval card."""
    from backend.core.automation_runner import (
        AutomationRunnerResult,
        register_automation_session_materializer,
    )

    monkeypatch.setattr("backend.services.automations.service.now_utc", lambda: _at(4, 7))
    automation = apply_automation_change(
        {
            "op": "create",
            "automation": {
                "name": "Digest",
                "instructions": "Read my email.",
                "trigger": trigger,
                "execution": {"kind": "lucius"},
            },
        }
    )
    AutomationStore().update_by_id(automation["id"], {"next_run_at": SLOT})
    monkeypatch.setattr("backend.services.automations.service.now_utc", lambda: _at(4, 9, 1))
    register_automation_session_materializer(
        lambda run, a: str(run.get("session_id") or f"automation-run-{run['id']}")
    )
    started: list[str] = []

    async def parking_runner(request, event_sink=None):
        started.append(request.run_id)
        if while_running is not None:
            while_running(automation["id"])
        return AutomationRunnerResult(
            status="waiting",
            session_id=request.session_id,
            summary="Needs you",
            model="test",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
        )

    monkeypatch.setattr(runs_mod, "run_with_automation_runner", parking_runner)
    return automation, started


async def _settle_background_runs() -> None:
    while runs_mod._tasks:
        await asyncio.gather(*list(runs_mod._tasks))


@pytest.mark.asyncio
async def test_a_run_that_parks_uses_up_its_slot_and_the_next_one_still_runs(
    isolated_automation_paths, monkeypatch
):
    """Repro: a digest stopped on an approval card at 00:02 and the 00:03 tick started a second
    run for the same midnight slot. Letting go of the claim was right (a parked run must not
    freeze the schedule), but the slot stayed due because only a FINISHED run moved it on."""
    automation, started = _automation_that_parks(monkeypatch, DAILY)

    first = await run_due_automations(_at(4, 9, 1))
    await _settle_background_runs()
    again = await run_due_automations(_at(4, 9, 3))

    assert first["ran"] == 1
    assert again["ran"] == 0, "the same slot must not start a second run"
    assert len(started) == 1
    parked = AutomationRunStore().find_by_id(started[0])
    assert parked["status"] == "waiting"
    assert AutomationStore().find_by_id(automation["id"])["next_run_at"] == (
        "2026-08-05T09:00:00+00:00"
    )

    # The fix from the five-day freeze still holds: tomorrow's slot runs with the card untapped.
    monkeypatch.setattr("backend.services.automations.service.now_utc", lambda: _at(5, 9, 1))
    tomorrow = await run_due_automations(_at(5, 9, 1))
    await _settle_background_runs()

    assert tomorrow["ran"] == 1
    assert len(started) == 2
    assert AutomationRunStore().find_by_id(started[0])["status"] == "waiting"


@pytest.mark.asyncio
async def test_a_one_off_run_that_parks_runs_once_and_finishes_when_answered(
    isolated_automation_paths, monkeypatch
):
    from backend.core.automation_runner import AutomationRunnerResult
    from backend.services.automations.runs import (
        begin_automation_continuation,
        reconcile_automation_continuation,
    )

    automation, started = _automation_that_parks(
        monkeypatch, {"kind": "schedule", "type": "once", "at": SLOT}
    )

    await run_due_automations(_at(4, 9, 1))
    await _settle_background_runs()
    again = await run_due_automations(_at(4, 9, 3))

    assert again["ran"] == 0
    parked_automation = AutomationStore().find_by_id(automation["id"])
    assert parked_automation["next_run_at"] is None
    assert parked_automation["status"] == "active", "it isn't finished until the run is"

    session_id = AutomationRunStore().find_by_id(started[0])["session_id"]
    latest, baseline = begin_automation_continuation(session_id)
    reconcile_automation_continuation(
        latest,
        baseline,
        AutomationRunnerResult(status="completed", session_id=session_id, summary="done"),
    )

    assert AutomationStore().find_by_id(automation["id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_a_parked_run_counts_towards_max_runs(isolated_automation_paths, monkeypatch):
    automation, started = _automation_that_parks(monkeypatch, DAILY)
    AutomationStore().update_by_id(automation["id"], {"max_runs": 1})

    await run_due_automations(_at(4, 9, 1))
    await _settle_background_runs()
    tomorrow = await run_due_automations(_at(5, 9, 1))

    assert tomorrow["ran"] == 0
    assert len(started) == 1


@pytest.mark.asyncio
async def test_a_manual_run_that_parks_leaves_the_schedule_alone(
    isolated_automation_paths, monkeypatch
):
    automation, started = _automation_that_parks(monkeypatch, DAILY)

    await runs_mod.run_automation(automation["id"], trigger={"kind": "manual"})
    await _settle_background_runs()

    assert len(started) == 1
    assert AutomationStore().find_by_id(automation["id"])["next_run_at"] == SLOT


@pytest.mark.asyncio
async def test_a_schedule_edited_during_the_run_is_not_overwritten_when_it_parks(
    isolated_automation_paths, monkeypatch
):
    edited = "2026-08-10T09:00:00+00:00"

    def edit_schedule(automation_id: str) -> None:
        AutomationStore().update_by_id(automation_id, {"next_run_at": edited})

    automation, _ = _automation_that_parks(monkeypatch, DAILY, while_running=edit_schedule)

    await run_due_automations(_at(4, 9, 1))
    await _settle_background_runs()

    assert AutomationStore().find_by_id(automation["id"])["next_run_at"] == edited


def test_boot_moves_on_a_slot_whose_parked_run_never_got_to(isolated_automation_paths, monkeypatch):
    """A crash between parking and moving on would otherwise hand the slot back at boot."""
    from backend.services.automations.runs import recover_automation_runs

    automation, _ = _automation_that_parks(monkeypatch, DAILY)
    parked = AutomationRunStore().start(
        {
            "automation_id": automation["id"],
            "session_id": "automation-run-parked",
            "trigger": {"kind": "schedule", "scheduled_for": SLOT},
        }
    )
    AutomationRunStore().transition(parked["id"], {"running"}, "waiting", {})

    recover_automation_runs(
        materialize_session=lambda row, a: row["session_id"],
        pending_for_session=lambda session_id: ["tool-1"],
    )

    assert AutomationStore().find_by_id(automation["id"])["next_run_at"] == (
        "2026-08-05T09:00:00+00:00"
    )

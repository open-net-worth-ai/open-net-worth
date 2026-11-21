from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_runtime_store import AutomationRuntimeStore
from backend.db.automation_store import AutomationStore
from backend.services.automations.runs import begin_automation_continuation, run_automation
from backend.services.automations.service import (
    apply_automation_change,
    preview_automation_updates,
    register_automation_cleanup,
)


def test_update_preview_is_registered_and_contains_only_real_field_changes(
    isolated_automation_paths,
):
    created = apply_automation_change({"op": "create", "automation": _manual()})

    preview = preview_automation_updates(
        [
            {
                "id": created["id"],
                "name": "Review liquidity",
                "status": "active",
            }
        ]
    )

    assert preview[0]["name"] == "Review cash"
    assert set(preview[0]["changes"]) == {"name"}
    assert preview[0]["changes"]["name"] == {
        "old": "Review cash",
        "new": "Review liquidity",
    }


def test_update_preview_adapter_rejects_an_automation_changed_after_staging(
    isolated_automation_paths,
):
    from backend.ai.tools import automations as automation_tools

    created = apply_automation_change({"op": "create", "automation": _manual()})
    built = automation_tools._AUTOMATION_UPDATES_PREVIEW.build(
        {"updates": [{"id": created["id"], "name": "Review liquidity"}]}
    )
    apply_automation_change({"op": "update", "id": created["id"], "changes": {"name": "Other"}})

    assert automation_tools._AUTOMATION_UPDATES_PREVIEW.validate(built) == (
        f"automation {created['id']!r} changed"
    )


@pytest.fixture
def isolated_automation_paths(db_isolated):
    from backend.core.automation_runner import register_automation_session_materializer

    register_automation_cleanup(None)
    register_automation_session_materializer(
        lambda run, automation: str(run.get("session_id") or f"automation-run-{run['id']}")
    )
    yield
    register_automation_cleanup(None)
    register_automation_session_materializer(None)


def _manual(name: str = "Review cash") -> dict:
    return {
        "name": name,
        "description": "Review the current cash position.",
        "instructions": "Review my cash position.",
        "trigger": {"kind": "manual"},
        "execution": {"kind": "lucius"},
    }


def test_reminder_seeds_run_at_low_effort(isolated_automation_paths):
    """A daily reminder that usually finds nothing to say should not pay for medium effort."""
    from backend.services.automations.service import seed_coupon_reminder, seed_vest_reminder
    from backend.services.options import seed_expiry_sweep

    assert seed_vest_reminder() is True
    assert seed_coupon_reminder() is True
    assert seed_expiry_sweep() is True
    rows = AutomationStore().all_persisted()
    assert len(rows) == 3
    assert {row["effort"] for row in rows} == {"low"}


def test_a_paused_event_automation_does_not_react_until_enabled(isolated_automation_paths):
    """Kept from the deleted `txn_categorizer` seeding tests (2026-08-27).

    Nothing seeds an event automation any more, but the property those tests happened to
    cover is general and worth its own guard: a PAUSED automation must not appear in the
    active set for its event, and must appear the moment it is enabled.
    """
    from backend.services.automations.events import _active_event_automations

    created = apply_automation_change(
        {
            "op": "create",
            "automation": {
                **_manual(),
                "name": "Reacts to new transactions",
                "status": "paused",
                "trigger": {
                    "kind": "event",
                    "event": "txn",
                    "when": {"operation": "create"},
                    "limit": "each",
                },
            },
        }
    )
    assert _active_event_automations("txn") == []

    apply_automation_change({"op": "update", "id": created["id"], "changes": {"status": "active"}})
    assert [row["id"] for row in _active_event_automations("txn")] == [created["id"]]


def test_all_configuration_writes_share_the_automation_change_choke(
    isolated_automation_paths,
):
    created = apply_automation_change({"op": "create", "automation": _manual()})
    paused = apply_automation_change({"op": "pause", "id": created["id"]})
    resumed = apply_automation_change({"op": "resume", "id": created["id"]})
    updated = apply_automation_change(
        {"op": "update", "id": created["id"], "changes": {"name": "Review liquidity"}}
    )

    assert created["status"] == "active"
    assert created["next_run_at"] is None
    assert paused["status"] == "paused"
    assert resumed["status"] == "active"
    assert resumed["next_run_at"] is None
    assert updated["name"] == "Review liquidity"


def test_trigger_revision_changes_only_when_the_normalized_trigger_changes(
    isolated_automation_paths,
):
    created = apply_automation_change({"op": "create", "automation": _manual()})
    original_revision = created["trigger_revision"]

    renamed = apply_automation_change(
        {"op": "update", "id": created["id"], "changes": {"name": "Renamed"}}
    )
    assert renamed["trigger_revision"] == original_revision

    rescheduled = apply_automation_change(
        {
            "op": "update",
            "id": created["id"],
            "changes": {"trigger": {"kind": "schedule", "type": "daily", "time": "09:00"}},
        }
    )
    assert rescheduled["trigger_revision"] != original_revision

    same_trigger = apply_automation_change(
        {
            "op": "update",
            "id": created["id"],
            "changes": {"trigger": {"kind": "schedule", "type": "daily", "time": "09:00"}},
        }
    )
    assert same_trigger["trigger_revision"] == rescheduled["trigger_revision"]

    restored = apply_automation_change(
        {
            "op": "update",
            "id": created["id"],
            "changes": {"trigger": {"kind": "manual"}},
        }
    )
    assert restored["trigger_revision"] not in {
        original_revision,
        rescheduled["trigger_revision"],
    }


def test_private_execution_union_rejects_unknown_or_unvalidated_variants(
    isolated_automation_paths,
):
    bad = _manual()
    bad["execution"] = {"kind": "shell", "command": "rm -rf data"}

    with pytest.raises(ValidationError, match="execution kind"):
        apply_automation_change({"op": "create", "automation": bad})

    assert AutomationStore().all() == []


@pytest.mark.flags(transaction_rules=False)
@pytest.mark.parametrize("kind", ["agent", "transaction_transform", "notify"])
def test_configuration_choke_accepts_only_lucius_execution(isolated_automation_paths, kind):
    bad = _manual()
    bad["execution"] = {"kind": kind}

    with pytest.raises(ValidationError, match="Lucius"):
        apply_automation_change({"op": "create", "automation": bad})


@pytest.mark.flags(transaction_rules=True)
@pytest.mark.parametrize("kind", ["agent", "notify"])
def test_configuration_choke_with_rules_on_still_refuses_other_executions(
    isolated_automation_paths, kind
):
    bad = _manual()
    bad["execution"] = {"kind": kind}

    with pytest.raises(ValidationError):
        apply_automation_change({"op": "create", "automation": bad})


def test_configuration_choke_rejects_non_object_provenance(isolated_automation_paths):
    bad = {**_manual(), "provenance": ["not", "an", "object"]}

    with pytest.raises(ValidationError, match="provenance must be an object"):
        apply_automation_change({"op": "create", "automation": bad})


def test_allowed_permission_preserves_runtime_tool_metadata(isolated_automation_paths):
    row = apply_automation_change(
        {
            "op": "create",
            "automation": {
                **_manual(),
                "permission_mode": "auto",
                "always_allowed": [
                    {
                        "src": "Open Net Worth",
                        "desc": "Log imported transactions.",
                        "capability": "log_transactions",
                        "tools": ["add_transactions", "telegram_send_message"],
                    }
                ],
            },
        }
    )

    assert row["permission_mode"] == "auto"
    assert row["always_allowed"] == [
        {
            "src": "Open Net Worth",
            "desc": "Log imported transactions.",
            "capability": "log_transactions",
            "tools": ["add_transactions", "telegram_send_message"],
        }
    ]


def test_trigger_edit_clears_old_event_state_but_preserves_run_and_conversation_ownership(
    isolated_automation_paths,
):
    automation = apply_automation_change(
        {
            "op": "create",
            "automation": {
                **_manual("Watch transactions"),
                "trigger": {
                    "kind": "event",
                    "event": "txn",
                    "when": {"direction": "debit", "operation": "create"},
                    "limit": "each",
                },
            },
        }
    )
    runtime = {
        "running_run_id": "run_live",
        "session_allowed_tools": {"automation-run-a": ["telegram_send_message"]},
        "event_queue": [{"event": "txn", "event_key": "txn:old"}],
        "event_seen": ["txn:old"],
        "event_last_fired_at": "2026-08-02T00:00:00+00:00",
        "balance_observations": {"acc": 10},
        "price_observations": {"AAPL": {"price": 100}},
    }
    AutomationRuntimeStore().put_for_automation(automation["id"], runtime)

    apply_automation_change(
        {
            "op": "update",
            "id": automation["id"],
            "changes": {
                "trigger": {
                    "kind": "event",
                    "event": "price",
                    "when": {"symbol": "AAPL", "direction": "below", "value": 100},
                    "limit": "each",
                }
            },
        }
    )

    state = AutomationRuntimeStore().get_for_automation(automation["id"])
    assert isinstance(state.pop("trigger_revision"), str)
    assert state == {
        "running_run_id": "run_live",
        "session_allowed_tools": {"automation-run-a": ["telegram_send_message"]},
    }


@pytest.mark.asyncio
async def test_run_now_persists_running_before_dispatch(
    isolated_automation_paths,
):
    from backend.core.automation_runner import (
        AutomationRunnerResult,
        register_automation_runner,
    )

    automation = apply_automation_change({"op": "create", "automation": _manual()})
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_runner(request, event_sink):
        persisted = AutomationRunStore().find_by_id(request.run_id)
        assert persisted is not None and persisted["status"] == "running"
        entered.set()
        await release.wait()
        return AutomationRunnerResult(
            status="completed", session_id=request.session_id, summary="ok"
        )

    register_automation_runner(blocking_runner)
    try:
        run = await run_automation(automation["id"], trigger="manual")
        await asyncio.wait_for(entered.wait(), timeout=1)

        assert run["status"] == "running"
        assert AutomationRunStore().find_by_id(run["id"])["status"] == "running"
    finally:
        release.set()
        await asyncio.sleep(0)
        register_automation_runner(None)


@pytest.mark.asyncio
async def test_second_start_is_refused_while_the_persisted_run_is_live(
    isolated_automation_paths,
):
    from backend.core.automation_runner import (
        AutomationRunnerResult,
        register_automation_runner,
    )

    automation = apply_automation_change({"op": "create", "automation": _manual()})
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_runner(request, event_sink):
        entered.set()
        await release.wait()
        return AutomationRunnerResult(status="completed", session_id=request.session_id)

    register_automation_runner(blocking_runner)
    try:
        first = await run_automation(automation["id"], trigger="manual")
        await asyncio.wait_for(entered.wait(), timeout=1)

        with pytest.raises(ConflictError, match="already running"):
            await run_automation(automation["id"], trigger="manual")

        assert AutomationRuntimeStore().get_for_automation(automation["id"]) == {
            "running_run_id": first["id"]
        }
    finally:
        release.set()
        await asyncio.sleep(0)
        register_automation_runner(None)


@pytest.mark.asyncio
async def test_start_rolls_back_its_claim_when_delete_tombstones_after_claim(
    isolated_automation_paths,
    monkeypatch,
):
    from backend.services.automations import runs

    automation = apply_automation_change({"op": "create", "automation": _manual()})
    real_claim = runs._claim_runtime

    def claim_then_tombstone(automation_id, run_id):
        result = real_claim(automation_id, run_id)
        AutomationStore().update_by_id(automation_id, {"status": "deleting"})
        return result

    monkeypatch.setattr(runs, "_claim_runtime", claim_then_tombstone)

    with pytest.raises(NotFoundError, match="not found"):
        await runs.run_automation(automation["id"], trigger="manual")

    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is None
    assert AutomationRunStore().for_automation(automation["id"], limit=10, offset=0) == []


def test_continuation_rolls_back_its_claim_when_delete_tombstones_after_claim(
    isolated_automation_paths,
    monkeypatch,
):
    from backend.services.automations import runs

    automation = apply_automation_change({"op": "create", "automation": _manual()})
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-delete-race"}
    )
    AutomationRunStore().transition(run["id"], {"running"}, "waiting", {})
    real_claim = runs._claim_runtime

    def claim_then_tombstone(automation_id, run_id):
        result = real_claim(automation_id, run_id)
        AutomationStore().update_by_id(automation_id, {"status": "deleting"})
        return result

    monkeypatch.setattr(runs, "_claim_runtime", claim_then_tombstone)

    with pytest.raises(NotFoundError, match="conversation not found"):
        begin_automation_continuation(run["session_id"])

    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is None
    assert AutomationRunStore().find_by_id(run["id"])["status"] == "waiting"


def test_terminal_owner_must_finish_and_release_before_continuation(
    isolated_automation_paths,
):
    from backend.services.automations import runs

    automation = apply_automation_change({"op": "create", "automation": _manual()})
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-finalizing"}
    )
    assert AutomationRuntimeStore().claim_run(automation["id"], run["id"])
    completed, _ = AutomationRunStore().transition(run["id"], {"running"}, "completed", {})

    with pytest.raises(ConflictError, match="still finalizing"):
        begin_automation_continuation(run["session_id"])

    assert (
        AutomationRuntimeStore().get_for_automation(automation["id"])["running_run_id"] == run["id"]
    )
    runs._finish_automation_once(automation, completed, "completed", {})
    runs._release_runtime(automation["id"], run["id"])

    _automation, restarted = begin_automation_continuation(run["session_id"])
    assert restarted["status"] == "running"
    assert (
        AutomationRuntimeStore().get_for_automation(automation["id"])["running_run_id"] == run["id"]
    )


def test_hard_delete_refuses_unproved_conversation_cleanup(
    isolated_automation_paths,
):
    automation = apply_automation_change({"op": "create", "automation": _manual()})
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-session"}
    )
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})
    AutomationRuntimeStore().put_for_automation(automation["id"], {"running_run_id": run["id"]})

    with pytest.raises(ConflictError, match="cleanup ownership"):
        apply_automation_change({"op": "delete", "id": automation["id"]})

    assert AutomationStore().find_by_id(automation["id"]) is not None
    assert AutomationRunStore().find_by_id(run["id"]) is not None
    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is not None


def test_hard_delete_proves_then_cleans_dependencies_before_removing_configuration(
    isolated_automation_paths,
):
    automation = apply_automation_change({"op": "create", "automation": _manual()})
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-session"}
    )
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})
    AutomationRuntimeStore().put_for_automation(automation["id"], {"queue": [{"key": "txn"}]})
    phases: list[str] = []

    def cleanup(context, *, phase):
        assert context["automation"]["id"] == automation["id"]
        assert [row["id"] for row in context["runs"]] == [run["id"]]
        phases.append(phase)
        return {"ok": True, "owned_session_ids": ["automation-session"]}

    register_automation_cleanup(cleanup)
    deleted = apply_automation_change({"op": "delete", "id": automation["id"]})

    assert deleted == {"ok": True, "id": automation["id"]}
    assert phases == ["prove", "delete"]
    assert AutomationStore().find_by_id(automation["id"]) is None
    assert AutomationRunStore().for_automation(automation["id"], 50, 0) == []
    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is None


def test_hard_delete_persists_a_hidden_manifest_and_resumes_after_partial_cleanup(
    isolated_automation_paths,
):
    automation = apply_automation_change({"op": "create", "automation": _manual()})
    run = AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-session"}
    )
    AutomationRunStore().transition(run["id"], {"running"}, "completed", {})
    AutomationRuntimeStore().put_for_automation(automation["id"], {"queue": [{"key": "txn"}]})
    phases: list[str] = []

    def cleanup(context, *, phase):
        phases.append(phase)
        if phase == "prove":
            return {"ok": True, "owned_session_ids": ["automation-session"]}
        tombstone = AutomationStore().find_persisted_by_id(automation["id"])
        assert tombstone is not None
        assert tombstone["status"] == "deleting"
        assert AutomationStore().find_by_id(automation["id"]) is None
        assert AutomationStore().all() == []
        assert context["automation"]["deletion"]["owned_session_ids"] == ["automation-session"]
        if phases.count("delete") == 1:
            return {
                "ok": False,
                "owned_session_ids": ["automation-session"],
                "error": "session index write failed",
            }
        return {"ok": True, "owned_session_ids": ["automation-session"]}

    register_automation_cleanup(cleanup)

    with pytest.raises(ConflictError, match="dependency cleanup failed"):
        apply_automation_change({"op": "delete", "id": automation["id"]})

    tombstone = AutomationStore().find_persisted_by_id(automation["id"])
    assert tombstone is not None
    assert tombstone["deletion"]["runs"] == [{"id": run["id"], "session_id": "automation-session"}]
    assert AutomationRunStore().find_by_id(run["id"]) is not None
    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is not None

    assert apply_automation_change({"op": "delete", "id": automation["id"]}) == {
        "ok": True,
        "id": automation["id"],
    }
    assert phases == ["prove", "delete", "delete"]
    assert AutomationStore().find_persisted_by_id(automation["id"]) is None
    assert AutomationRunStore().find_by_id(run["id"]) is None
    assert AutomationRuntimeStore().get_for_automation(automation["id"]) is None


def test_hard_delete_refuses_a_runtime_claim_without_a_run_row(
    isolated_automation_paths,
):
    automation = apply_automation_change({"op": "create", "automation": _manual()})
    AutomationRuntimeStore().put_for_automation(
        automation["id"], {"running_run_id": "run-not-persisted-yet"}
    )

    with pytest.raises(ConflictError, match="currently starting"):
        apply_automation_change({"op": "delete", "id": automation["id"]})

    assert AutomationStore().find_by_id(automation["id"]) is not None


def test_hard_delete_refuses_a_run_that_can_still_be_executing(
    isolated_automation_paths,
):
    automation = apply_automation_change({"op": "create", "automation": _manual()})
    AutomationRunStore().start(
        {"automation_id": automation["id"], "session_id": "automation-session"}
    )

    with pytest.raises(ConflictError, match="currently running"):
        apply_automation_change({"op": "delete", "id": automation["id"]})

    assert AutomationStore().find_by_id(automation["id"]) is not None


def test_schedule_creation_uses_the_current_instant_for_next_run(
    isolated_automation_paths, monkeypatch
):
    import backend.services.automations.service as service

    now = datetime(2026, 8, 2, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "now_utc", lambda: now)

    row = apply_automation_change(
        {
            "op": "create",
            "automation": {
                **_manual("Morning review"),
                "trigger": {
                    "kind": "schedule",
                    "type": "daily",
                    "time": "08:00",
                    "tzid": "UTC",
                },
            },
        }
    )

    assert row["next_run_at"] == "2026-08-02T08:00:00+00:00"


# ── Data-hygiene: automation_runs (+ their sessions) never grow forever ─────────────────


@pytest.mark.asyncio
async def test_run_automation_trims_runs_past_the_cap(isolated_automation_paths):
    """Regression: AutomationRunStore had no retention of its own (unlike
    RunHistoryStore's per-entity cap) — a long-lived automation's runs accumulated without
    bound. `run_automation` now trims down to the newest `_MAX_RUNS_PER_AUTOMATION` runs
    after every new run starts. The session-deletion half of the same trim is covered at the
    unit level (tests/test_automation_run_retention.py) — this proves the real
    `run_automation()` call site actually wires into it, end to end."""
    from backend.core.automation_runner import (
        AutomationRunnerResult,
        register_automation_runner,
    )
    from backend.services.automations import runs as runs_mod

    async def instant_runner(request, event_sink):
        return AutomationRunnerResult(
            status="completed", session_id=request.session_id, summary="ok"
        )

    automation = apply_automation_change({"op": "create", "automation": _manual()})
    register_automation_runner(instant_runner)
    total = runs_mod._MAX_RUNS_PER_AUTOMATION + 2
    run_ids: list[str] = []
    try:
        for _ in range(total):
            run = await run_automation(automation["id"], trigger="manual")
            run_ids.append(run["id"])
            task = runs_mod._tasks_by_run.get(run["id"])
            if task is not None:
                await task  # let this run fully settle before starting the next
    finally:
        register_automation_runner(None)

    remaining = AutomationRunStore().for_automation(automation["id"], limit=1000, offset=0)
    assert len(remaining) == runs_mod._MAX_RUNS_PER_AUTOMATION
    remaining_ids = {r["id"] for r in remaining}
    assert run_ids[0] not in remaining_ids  # oldest — past the cap
    assert AutomationRunStore().find_by_id(run_ids[0]) is None
    assert run_ids[-1] in remaining_ids  # newest — always kept

"""A failed Automation stays visible until it is opened or a later run succeeds.

Owner report 2026-10-14: a run that failed showed "Failed" only until it was read, four failed
runs never showed it at all (their turn ended ``timeout``/``interrupted``), and two said "New
reply" (the run failed after a turn that said ``ok``). The run row's own status is the truth:
the Lucius home lists every automation whose LATEST run failed under Needs attention, and the
conversation list carries each run's status beside its conversation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.ai import assistant
from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_store import AutomationStore
from backend.db.session_store import SessionStore
from backend.services.automations.activity import automation_activity, run_status_by_session


def ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


TIMEOUT = "Claude Code CLI timed out after 300s (raise ONW_CLAUDE_CLI_TIMEOUT if this is expected)."


@pytest.fixture
def stores(db_isolated):
    return AutomationStore(), AutomationRunStore()


def _run(runs, automation_id, started_at, status, **patch):
    run = runs.start(
        {
            "automation_id": automation_id,
            "started_at": started_at,
            "session_id": f"automation-run-{started_at}",
        }
    )
    if status != "running":
        runs.transition(run["id"], {"running"}, status, patch)
    return run


def test_an_automation_whose_latest_run_failed_needs_attention(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    _run(runs, digest["id"], ago(14), "completed")
    finished = ago(1)
    failed = _run(runs, digest["id"], ago(2), "failed", error=TIMEOUT, finished_at=finished)

    [item] = automation_activity()["failed"]

    assert item["automation_name"] == "Email digest"
    assert item["run_id"] == failed["id"]
    assert item["session_id"] == failed["session_id"]
    assert item["finished_at"] == finished
    assert item["error"] == TIMEOUT


def test_a_later_successful_run_clears_it(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    _run(runs, digest["id"], "2026-10-12T00:00:00+00:00", "failed", error="boom")
    _run(runs, digest["id"], "2026-10-13T00:00:00+00:00", "completed")

    assert automation_activity()["failed"] == []


def test_a_retry_that_succeeds_clears_it(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    failed = _run(runs, digest["id"], "2026-10-13T00:00:00+00:00", "failed", error="boom")
    # A follow-up in the run's own conversation re-enters running on the SAME row.
    runs.transition(failed["id"], {"failed"}, "running", {"error": None, "finished_at": None})
    runs.transition(failed["id"], {"running"}, "completed", {})

    assert automation_activity()["failed"] == []


def test_a_new_run_in_flight_replaces_the_failure(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    _run(runs, digest["id"], "2026-10-12T00:00:00+00:00", "failed", error="boom")
    _run(runs, digest["id"], "2026-10-13T00:00:00+00:00", "running")

    assert automation_activity()["failed"] == []


def test_each_automation_is_judged_by_its_own_latest_run(stores):
    automations, runs = stores
    a = automations.create({"name": "A"})
    b = automations.create({"name": "B"})
    _run(runs, a["id"], ago(2), "failed", error="boom")
    _run(runs, b["id"], ago(1), "completed")

    assert [i["automation_name"] for i in automation_activity()["failed"]] == ["A"]


def test_a_failure_waits_at_most_a_day(stores):
    # A paused or finished automation never runs again — without a cap its failure would sit
    # in Needs attention forever (owner, 2026-10-14). The history list keeps saying Failed.
    automations, runs = stores
    recent = automations.create({"name": "Recent"})
    stale = automations.create({"name": "Stale"})
    _run(runs, recent["id"], ago(24), "failed", error="boom", finished_at=ago(23))
    old = _run(runs, stale["id"], ago(26), "failed", error="boom", finished_at=ago(25))
    automations.update_by_id(stale["id"], {"status": "paused"})

    assert [i["automation_name"] for i in automation_activity()["failed"]] == ["Recent"]
    assert run_status_by_session()[old["session_id"]] == "failed"


def _with_conversation(run: dict) -> str:
    SessionStore().upsert_meta(run["session_id"], {"title": "Email digest"})
    return run["session_id"]


def test_opening_the_failed_run_clears_it(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    failed = _run(runs, digest["id"], ago(9), "failed", error="boom", finished_at=ago(8))
    session_id = _with_conversation(failed)
    assert [item["run_id"] for item in automation_activity()["failed"]] == [failed["id"]]

    assistant.mark_sessions_read([session_id])

    assert automation_activity()["failed"] == []


def test_a_read_before_the_run_ended_does_not_clear_it(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    failed = _run(runs, digest["id"], ago(9), "failed", error="boom", finished_at=ago(1))
    SessionStore().mark_read([_with_conversation(failed)], at=ago(2))

    assert [item["run_id"] for item in automation_activity()["failed"]] == [failed["id"]]


def test_the_next_failure_comes_back_after_the_last_was_read(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    first = _run(runs, digest["id"], ago(9), "failed", error="boom", finished_at=ago(8))
    assistant.mark_sessions_read([_with_conversation(first)])
    second = _run(runs, digest["id"], ago(2), "failed", error="boom", finished_at=ago(1))

    assert [item["run_id"] for item in automation_activity()["failed"]] == [second["id"]]


def test_a_retry_that_fails_after_the_read_comes_back(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    run = _run(runs, digest["id"], ago(9), "failed", error="boom", finished_at=ago(8))
    SessionStore().mark_read([_with_conversation(run)], at=ago(2))
    assert automation_activity()["failed"] == []

    runs.transition(run["id"], {"failed"}, "running", {"finished_at": None})
    runs.transition(run["id"], {"running"}, "failed", {"error": "again", "finished_at": ago(1)})

    assert [item["run_id"] for item in automation_activity()["failed"]] == [run["id"]]


def test_a_recent_failure_read_stays_in_active_with_its_status(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    failed = _run(runs, digest["id"], ago(2), "failed", error="boom", finished_at=ago(1))
    assistant.mark_sessions_read([_with_conversation(failed)])

    activity = automation_activity()

    assert activity["failed"] == []
    assert [(i["run_id"], i["status"]) for i in activity["active"]] == [(failed["id"], "failed")]


def test_an_unreadable_read_stamp_keeps_the_failure_listed(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    failed = _run(runs, digest["id"], ago(2), "failed", error="boom", finished_at=ago(1))
    SessionStore().upsert_meta(failed["session_id"], {"last_read_at": "not a date"})

    assert [item["run_id"] for item in automation_activity()["failed"]] == [failed["id"]]


def test_an_automation_being_deleted_is_left_out(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    _run(runs, digest["id"], "2026-10-12T00:00:00+00:00", "failed", error="boom")
    automations.update_by_id(digest["id"], {"status": "deleting"})

    assert automation_activity()["failed"] == []


def test_outcomes_reads_only_the_outcome_fields_oldest_first(stores):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    first = _run(runs, digest["id"], "2026-10-12T00:00:00+00:00", "completed")
    second = _run(runs, digest["id"], "2026-10-13T00:00:00+00:00", "failed", error="boom")

    rows = runs.outcomes()

    assert [r["id"] for r in rows] == [first["id"], second["id"]]
    assert set(rows[0]) == {
        "id",
        "automation_id",
        "session_id",
        "status",
        "started_at",
        "finished_at",
        "error",
    }
    assert rows[1]["error"] == "boom"


def test_the_conversation_list_carries_each_runs_status(stores, monkeypatch):
    automations, runs = stores
    digest = automations.create({"name": "Email digest"})
    failed = _run(runs, digest["id"], "2026-10-13T00:00:00+00:00", "failed", error="boom")
    rows = [
        {"id": failed["session_id"], "updated_at": "2026-10-13T01:00:00+00:00"},
        {"id": "ordinary-chat", "updated_at": "2026-10-13T00:00:00+00:00"},
    ]
    monkeypatch.setattr(assistant, "_read_index", lambda: rows)

    listed = {row["id"]: row for row in assistant.list_sessions(with_run_status=True)}

    assert run_status_by_session() == {failed["session_id"]: "failed"}
    assert listed[failed["session_id"]]["run_status"] == "failed"
    assert "run_status" not in listed["ordinary-chat"]
    assert "run_status" not in assistant.list_sessions()[0]

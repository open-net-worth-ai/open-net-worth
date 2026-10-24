"""A sync run is a record of kind ``sync`` with its changes itemized: what went to YNAB, what came
in, Assigned moves, review items, deferrals and refusals. Scheduled runs with nothing to report
are not records of their own."""

from __future__ import annotations

import time

import pytest

from backend.services import import_runs, ynab_sync
from backend.services.ynab_sync import recorder, runner
from tests import ynab_stub
from tests.test_ynab_sync_service import assign, edit_onw, enable, row, sync, world  # noqa: F401


@pytest.fixture
def stub(world):  # noqa: F811
    return world


def sync_runs(**kw):
    return [r for r in import_runs.list_runs("ynab", include_archived=True) if r["kind"] == "sync"]


def detail(run_id):
    return import_runs.get(run_id)


def test_a_manual_run_is_a_record_with_its_sent_change_itemized(stub):
    enable()
    sync()
    edit_onw(row("Bistro")["id"], memo="team dinner")
    run = sync()
    rec = detail(run["run_id"])
    assert (rec["kind"], rec["trigger"], rec["status"]) == ("sync", "manual", "done")
    assert rec["outcome"] == "1 sent to YNAB" and rec["requests_used"] == run["requests"]
    (change,) = rec["txn_changes"]
    assert (change["direction"], change["action"], change["payee"]) == ("sent", "updated", "Bistro")
    assert change["currency"] == "USD" and change["txn_id"] == row("Bistro")["id"]
    assert {"name": "Memo", "before": "", "after": "team dinner"} in change["fields"]
    assert rec["totals"] == {
        "sent": 1,
        "pulled": 0,
        "assigned": 0,
        "conflicts": 0,
        "deferred": 0,
        "errors": 0,
    }


def test_a_pulled_change_shows_each_field_before_and_after(stub):
    enable()
    sync()
    ynab_stub.ynab_edit(
        stub.state,
        ynab_stub.find(stub.state, payee_name="Bistro")["id"],
        memo="from ynab",
        amount=-70_000,
    )
    (change,) = detail(sync()["run_id"])["txn_changes"]
    assert (change["direction"], change["action"]) == ("pulled", "updated")
    names = {f["name"]: f for f in change["fields"]}
    assert names["Memo"]["after"] == "from ynab" and names["Amount"]["after"] == "-70.00"


def test_assigned_moves_and_review_items_are_recorded(stub):
    enable()
    sync()
    month, _ = assign(0, "Rent", 1300)
    ynab_stub.ynab_budget(stub.state, 0, "groc", 350_000)
    edit_onw(row("Bistro")["id"], memo="mine")
    ynab_stub.ynab_edit(
        stub.state, ynab_stub.find(stub.state, payee_name="Bistro")["id"], memo="theirs"
    )
    rec = detail(sync()["run_id"])
    moves = {(c["direction"], c["category"]): c for c in rec["assigned_changes"]}
    assert moves[("sent", "Rent")]["after"] == 1300.0 and moves[("sent", "Rent")]["month"] == month
    assert moves[("pulled", "Groceries")]["after"] == 350.0
    (conflict,) = rec["conflicts"]
    assert conflict["status"] == "open" and conflict["onw_value"]["memo"] == "mine"
    assert conflict["id"] == ynab_sync.status()["conflicts"][0]["id"]
    assert rec["totals"]["assigned"] == 2 and rec["totals"]["conflicts"] == 1


def test_a_quiet_scheduled_run_is_not_a_record_but_updates_the_last_run(stub):
    enable("15m")
    assert ynab_sync.run_if_due() is True  # the first run: nothing differs
    last = ynab_sync.status()["last_run"]
    assert last["trigger"] == "scheduled" and last["summary"] == "Nothing changed"
    assert last["run_id"] is None and sync_runs() == []


def test_a_scheduled_run_with_a_change_is_a_scheduled_record(stub):
    enable("15m")
    sync()
    edit_onw(row("Bistro")["id"], memo="x")
    runner._locked_run("scheduled")
    (summary,) = [r for r in sync_runs() if r["trigger"] == "scheduled"]
    assert summary["trigger"] == "scheduled" and summary["outcome"] == "1 sent to YNAB"
    assert ynab_sync.status()["last_run"]["run_id"] == summary["id"]


def test_a_scheduled_failure_is_recorded_once_not_every_hour(stub, monkeypatch):
    enable()
    sync()
    boom = lambda *a, **k: (_ for _ in ()).throw(runner.ValidationError("YNAB HTTP 401"))  # noqa: E731
    monkeypatch.setattr(runner, "_execute", boom)
    runner._locked_run("scheduled")
    runner._locked_run("scheduled")
    failed = [r for r in sync_runs() if r["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["error"] == "YNAB HTTP 401"
    assert any(e["item"] == "The run" for e in detail(failed[0]["id"])["errors"])


def test_items_are_capped_and_the_overflow_is_counted(stub, monkeypatch):
    monkeypatch.setattr(recorder, "MAX_ITEMS", 1)
    enable()
    sync()
    edit_onw(row("Bistro")["id"], memo="a")
    edit_onw(row("Interest")["id"], memo="b")
    rec = detail(sync()["run_id"])
    assert len(rec["txn_changes"]) == 1
    assert rec["totals"]["sent"] == 2 and rec["not_stored"] == {"txn_changes": 1}


def test_a_recording_failure_never_fails_the_sync(stub, monkeypatch):
    def boom(self, *a, **k):
        raise RuntimeError("recorder broke")

    monkeypatch.setattr(recorder.Recorder, "add", boom)
    enable()
    sync()
    edit_onw(row("Bistro")["id"], memo="still sent")
    run = sync()
    assert run["summary"] == "1 sent to YNAB"
    assert ynab_stub.find(stub.state, payee_name="Bistro")["memo"] == "still sent"


def test_sync_now_opens_a_running_record_and_closes_it(stub):
    enable()
    status = ynab_sync.start_now()
    run_id = status["run_id"]
    assert run_id and import_runs.get(run_id)["kind"] == "sync"
    deadline = time.time() + 30
    while import_runs.get(run_id)["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    done = import_runs.get(run_id)
    assert done["status"] == "done" and done["outcome"] == "Nothing changed"
    assert (
        ynab_sync.status()["last_run"]["run_id"] == run_id and ynab_sync.status()["run_id"] is None
    )


def test_sync_now_is_refused_while_an_import_is_running(stub):
    enable()
    import_runs.start("ynab", "import", ynab_stub.PLAN)
    with pytest.raises(import_runs.ConflictError):
        ynab_sync.start_now()
    assert not runner._lock.locked()


def test_resolving_in_favour_of_the_ledger_is_a_record_but_keeping_ynabs_is_not(stub):
    enable()
    sync()
    edit_onw(row("Bistro")["id"], memo="mine")
    ynab_stub.ynab_edit(
        stub.state, ynab_stub.find(stub.state, payee_name="Bistro")["id"], memo="theirs"
    )
    sync()
    before = len(sync_runs())
    (item,) = ynab_sync.status()["conflicts"]
    ynab_sync.resolve.resolve(item["id"], "onw")
    (newest, *_) = sync_runs()
    assert len(sync_runs()) == before + 1 and newest["outcome"] == "Resolved a review item"
    (change,) = detail(newest["id"])["txn_changes"]
    assert change["direction"] == "sent"


def test_a_sync_record_is_never_left_running_when_closing_it_fails(stub, monkeypatch):
    enable()
    real, calls = import_runs.finish, []

    def flaky(run_id, status, **fields):
        calls.append(status)
        if len(calls) == 1:
            raise RuntimeError("db hiccup")
        return real(run_id, status, **fields)

    monkeypatch.setattr(import_runs, "finish", flaky)
    status = ynab_sync.start_now()
    deadline = time.time() + 30
    while ynab_sync.status()["running"] and time.time() < deadline:
        time.sleep(0.05)
    assert import_runs.get(status["run_id"])["status"] == "done"  # the retry closed it

    always = {"n": 0}

    def broken(run_id, status, **fields):
        always["n"] += 1
        if status == "done":
            raise RuntimeError("db gone")
        return real(run_id, status, **fields)

    monkeypatch.setattr(import_runs, "finish", broken)
    status = ynab_sync.start_now()
    while ynab_sync.status()["running"] and time.time() < deadline:
        time.sleep(0.05)
    assert import_runs.get(status["run_id"])["status"] == "failed"  # never left running


def test_a_scheduled_tick_and_a_resolve_wait_for_a_running_import(stub):
    from backend.core.errors import ConflictError

    enable("15m")
    sync()
    edit_onw(row("Bistro")["id"], memo="mine")
    ynab_stub.ynab_edit(
        stub.state, ynab_stub.find(stub.state, payee_name="Bistro")["id"], memo="theirs"
    )
    sync()
    (item,) = ynab_sync.status()["conflicts"]
    import_runs.start("ynab", "import", ynab_stub.PLAN)
    assert ynab_sync.run_if_due() is False and not runner.is_running()
    with pytest.raises(ConflictError):
        ynab_sync.resolve.resolve(item["id"], "onw")
    assert not runner.is_running()


def test_an_import_refuses_to_start_while_the_sync_runs(stub):
    from backend.core.errors import ConflictError
    from backend.services import ynab_migration

    enable()
    assert runner._lock.acquire(blocking=False)
    try:
        with pytest.raises(ConflictError):
            ynab_migration.start("import", ynab_stub.PLAN)
    finally:
        runner._lock.release()


def test_the_migration_status_ignores_sync_records_and_refuses_the_sync_kind(stub):
    from backend.core.errors import ValidationError
    from backend.services import ynab_migration

    enable()
    sync()
    assert ynab_migration.status()["job"]["kind"] == "import"  # not the sync record just stored
    with pytest.raises(ValidationError):
        ynab_migration.start("sync", ynab_stub.PLAN)


def test_a_stored_schedule_trigger_reads_as_scheduled(stub):
    from backend.services.ynab_sync import state

    enable()
    st = state.load()
    state.save({**st, "last_run": {**sync(), "trigger": "schedule"}})
    assert ynab_sync.status()["last_run"]["trigger"] == "scheduled"

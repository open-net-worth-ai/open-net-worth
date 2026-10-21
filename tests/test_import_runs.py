"""Import runs: the persisted lifecycle (start, finish, interrupted on boot, archive), the list
filter, the REST routes, Reset and the report shape. Synthetic data only."""

from __future__ import annotations

from collections import Counter

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.demo_lockdown import is_demo_locked
from backend.api.import_runs_api import router
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.import_run_store import COLUMNS, ImportRunStore
from backend.services import import_runs, portability
from backend.services.ynab_migration import report


@pytest.fixture
def client(db_isolated):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def finished(kind="import", status="done"):
    run = import_runs.start("ynab", kind, "plan-1")
    import_runs.finish(run["id"], status, outcome="ok")
    return run["id"]


def test_a_run_is_a_row_from_the_moment_it_starts(db_isolated):
    run = import_runs.start("ynab", "preview", "plan-1")
    stored = import_runs.get(run["id"])
    assert stored["status"] == "running" and stored["kind"] == "preview"
    assert stored["trigger"] == "manual" and stored["finished_at"] is None
    import_runs.update(run["id"], phase="mapping", progress={"done": 2, "total": 4})
    assert import_runs.get(run["id"])["progress"] == {"done": 2, "total": 4}
    import_runs.finish(run["id"], "done", outcome="Would bring across 1 accounts")
    done = import_runs.get(run["id"])
    assert done["status"] == "done" and done["finished_at"]


def test_one_run_per_provider_at_a_time(db_isolated):
    first = import_runs.start("ynab", "import", "plan-1")
    with pytest.raises(ConflictError):
        import_runs.start("ynab", "verify", "plan-1")
    import_runs.start("other", "verify", "plan-1")  # another provider is independent
    import_runs.finish(first["id"], "done")
    import_runs.start("ynab", "verify", "plan-1")


def test_kind_and_trigger_are_validated(db_isolated):
    with pytest.raises(ValidationError):
        import_runs.start("ynab", "sync", "plan-1")
    with pytest.raises(ValidationError):
        import_runs.start("ynab", "import", "plan-1", trigger="hourly")


def test_a_run_left_running_becomes_interrupted_on_boot(db_isolated):
    done = finished()
    stuck = import_runs.start("ynab", "import", "plan-1")
    assert import_runs.recover_interrupted() == 1
    run = import_runs.get(stuck["id"])
    assert run["status"] == "interrupted" and run["finished_at"] and run["outcome"] == "Interrupted"
    assert "stopped" in run["error"]
    assert import_runs.get(done)["status"] == "done"
    assert import_runs.recover_interrupted() == 0
    import_runs.start("ynab", "verify", "plan-1")  # the interrupted run no longer blocks a start


def test_archive_hides_a_finished_run_and_unarchive_brings_it_back(db_isolated):
    keep, hide = finished(), finished("verify")
    import_runs.archive(hide)
    assert [r["id"] for r in import_runs.list_runs("ynab")] == [keep]
    both = import_runs.list_runs("ynab", include_archived=True)
    assert [r["id"] for r in both] == [hide, keep]  # newest first
    assert both[0]["archived_at"] and not both[1]["archived_at"]
    import_runs.unarchive(hide)
    assert len(import_runs.list_runs("ynab")) == 2
    assert import_runs.get(hide)["id"] == hide  # archived is a hide, the record stays


def test_a_running_run_cannot_be_archived(db_isolated):
    run = import_runs.start("ynab", "import", "plan-1")
    with pytest.raises(ConflictError):
        import_runs.archive(run["id"])


def test_list_filters_by_provider_and_carries_summaries_only(db_isolated):
    mine = finished()
    other = import_runs.start("other", "verify", "p")
    import_runs.finish(other["id"], "done")
    import_runs.update(mine, log=[{"at": "t", "line": "x"}])
    assert [r["id"] for r in import_runs.list_runs("ynab")] == [mine]
    assert len(import_runs.list_runs()) == 2
    summary = import_runs.list_runs("ynab")[0]
    assert "log" not in summary and "verification" not in summary and summary["outcome"] == "ok"


def test_the_list_reads_columns_only_never_the_heavy_detail(db_isolated):
    run_id = finished()
    import_runs.update(run_id, log=[{"at": "t", "line": "x"}], verification={"matches": [1] * 50})
    (summary,) = ImportRunStore().summaries("ynab", True, 10)
    assert set(summary) == set(COLUMNS)
    assert "log" not in summary and "verification" not in summary
    assert import_runs.get(run_id)["verification"] == {"matches": [1] * 50}  # the detail is kept
    assert ImportRunStore().latest("ynab")["log"] == [{"at": "t", "line": "x"}]


def test_an_unknown_field_is_refused(db_isolated):
    run_id = finished()
    with pytest.raises(ValueError):
        import_runs.update(run_id, bogus=1)


def test_archive_and_unarchive_publish_a_data_event(db_isolated, monkeypatch):
    run_id = finished()
    events = []
    monkeypatch.setattr(import_runs.data_events, "publish", lambda d: events.append(list(d)))
    import_runs.archive(run_id)
    import_runs.unarchive(run_id)
    assert events == [["*"], ["*"]]


def test_unknown_run_is_not_found(db_isolated):
    with pytest.raises(NotFoundError):
        import_runs.get("nope")
    with pytest.raises(NotFoundError):
        import_runs.archive("nope")


def test_reset_wipes_the_run_history(db_isolated):
    finished()
    portability.reset_bridged_stores()
    assert ImportRunStore().summaries(None, True, 10) == []


# ── REST ─────────────────────────────────────────────────────────────────────────────


def test_routes_list_open_archive_and_unarchive(client):
    run_id = finished()
    listed = client.get("/api/imports/runs", params={"provider": "ynab"}).json()["runs"]
    assert [r["id"] for r in listed] == [run_id]

    assert client.get(f"/api/imports/runs/{run_id}").json()["log"] == []
    assert client.post(f"/api/imports/runs/{run_id}/archive").json()["archived_at"]
    assert client.get("/api/imports/runs").json()["runs"] == []
    shown = client.get("/api/imports/runs", params={"archived": "true"}).json()["runs"]
    assert [r["id"] for r in shown] == [run_id]
    assert client.post(f"/api/imports/runs/{run_id}/unarchive").json()["archived_at"] is None


def test_routes_report_missing_and_running(client):
    assert client.get("/api/imports/runs/nope").status_code == 404
    assert client.post("/api/imports/runs/nope/archive").status_code == 404
    running = import_runs.start("ynab", "import", "plan-1")
    assert client.post(f"/api/imports/runs/{running['id']}/archive").status_code == 409


@pytest.mark.parametrize("action", ["archive", "unarchive"])
def test_archive_routes_are_refused_on_the_demo(action):
    assert is_demo_locked("POST", f"/api/imports/runs/abc/{action}")


# ── the stored report shape ──────────────────────────────────────────────────────────


def test_verification_keeps_mismatches_apart_from_matches():
    rows = [
        {"group": "account", "label": "A: balance", "expected": 10.0, "actual": 10.0, "ok": True},
        {
            "group": "account",
            "label": "A: transactions",
            "expected": 5,
            "actual": 4,
            "ok": False,
            "note": "YNAB has 5",
            "unit": "count",
        },
        {
            "group": "envelope",
            "label": "Food: envelope",
            "expected": "present",
            "actual": "missing",
            "ok": False,
        },
    ]
    shaped = report.verification({"rows": rows, "ok": False, "mismatches": 2})
    assert (shaped["checks_total"], shaped["checks_matching"]) == (3, 1)
    assert [m["check"] for m in shaped["mismatches"]] == ["A: transactions", "Food: envelope"]
    assert shaped["mismatches"][0] == {
        "check": "A: transactions",
        "ynab_value": 5,
        "onw_value": 4,
        "difference": -1,
        "note": "YNAB has 5",
        "unit": "count",
    }
    assert (
        shaped["mismatches"][1]["difference"] is None and shaped["mismatches"][1]["unit"] == "text"
    )
    assert shaped["matches"][0]["unit"] == "money"
    assert [m["check"] for m in shaped["matches"]] == ["A: balance"]


def test_a_refused_row_carries_a_plain_language_hint():
    problem = report.problem({"ref": "t-1", "reason": "", "kind": "previously_deleted"})
    assert problem["item"] == "t-1" and "Restore it" in problem["hint"]
    tally = Counter(
        created=3, already_present=2, refused_duplicate=1, previously_deleted=1, failed=1
    )
    assert report.transaction_counts(tally) == {
        "created": 3,
        "existing": 2,
        "refused": 2,
        "failed": 1,
    }

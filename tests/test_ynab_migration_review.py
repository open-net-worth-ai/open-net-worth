"""The review before an import: the preview names every category under its group and lists the
transactions, and an import started from the review can turn the sync on when it succeeds.
Against an isolated database and the synthetic YNAB API (tests/ynab_stub.py)."""

from __future__ import annotations

import time

import pytest

from backend.db.ynab_config_store import YnabConfigStore
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ynab_migration, ynab_sync
from backend.services.aggregation import ynab as ynab_service
from backend.services.ynab_migration import preview as preview_module
from tests import ynab_stub

pytestmark = pytest.mark.flags(ynab_import_and_sync=True)


@pytest.fixture
def ynab(db_isolated, monkeypatch):
    server, base_url, _calls = ynab_stub.serve()
    monkeypatch.setattr(ynab_service, "API_BASE", base_url)
    categories_service.ensure_builtins_seeded()
    envelopes_service.ensure_envelopes_migrated()
    YnabConfigStore().update({"access_token": ynab_stub.TOKEN})
    yield server
    server.shutdown()


def run(mode="import", **kw):
    ynab_migration.start(mode, ynab_stub.PLAN, **kw)
    deadline = time.time() + 90
    while time.time() < deadline:
        job = ynab_migration.status()["job"]
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("migration job did not finish")


def test_preview_names_each_category_under_its_group(ynab):
    groups = run("preview")["preview"]["category_groups"]
    assert [(g["name"], [c["label"] for c in g["categories"]]) for g in groups] == [
        ("Bills", ["Rent", "Utilities"]),
        ("Needs", ["Groceries", "Dining out"]),
        ("Wants", ["Fun Money"]),
        ("Hidden Categories", ["Old Cat"]),
    ]


def test_preview_lists_the_transactions_newest_first_with_accounts_and_categories(ynab):
    preview = run("preview")["preview"]
    rows = preview["transaction_list"]
    assert len(rows) == preview["transactions"] == 19
    assert [r["date"] for r in rows] == sorted((r["date"] for r in rows), reverse=True)
    rent = next(r for r in rows if r["payee"] == "Landlord")
    assert rent == {
        "date": rent["date"],
        "kind": "expense",
        "account": "Checking",
        "to_account": None,
        "payee": "Landlord",
        "category": "Rent",
        "amount": -1200.0,
        "memo": None,
    }
    move = next(r for r in rows if r["kind"] == "transfer" and r["amount"] == 500.0)
    assert (move["account"], move["to_account"]) == ("Checking", "Savings")
    split = next(r for r in rows if r["payee"] == "Costco")
    assert split["category"] == "Split (2)"


def test_preview_lists_only_the_newest_rows_of_a_long_history(ynab, monkeypatch):
    monkeypatch.setattr(preview_module, "LIST_CAP", 5)
    preview = run("preview")["preview"]
    assert len(preview["transaction_list"]) == 5 and preview["transactions"] == 19


def test_an_import_from_the_review_turns_the_sync_on_in_the_chosen_direction(ynab):
    job = run(sync_direction="from_ynab")
    assert job["status"] == "done", job["error"]
    status = ynab_sync.status()
    assert (status["enabled"], status["direction"], status["plan_id"]) == (
        True,
        "from_ynab",
        ynab_stub.PLAN,
    )
    assert any("Sync turned on" in e["line"] for e in job["log"])


def test_an_import_without_a_direction_leaves_the_sync_off(ynab):
    assert run()["status"] == "done"
    assert ynab_sync.status()["enabled"] is False


def test_a_sync_that_cannot_turn_on_leaves_the_import_done_and_says_why(ynab, monkeypatch):
    from backend.core.errors import ConflictError
    from backend.services.ynab_sync import runner

    def busy(**_kw):
        raise ConflictError("A YNAB sync is running.")

    monkeypatch.setattr(runner, "update_settings", busy)
    job = run(sync_direction="both")
    assert job["status"] == "done"
    assert any("sync was not turned on" in e["line"] for e in job["log"])


def test_an_unknown_direction_is_refused_before_anything_runs(ynab):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        ynab_migration.start("import", ynab_stub.PLAN, sync_direction="sideways")
    assert ynab_migration.status()["job"] is None


def test_the_start_route_carries_the_direction(ynab):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.ynab_api import router

    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post(
        "/api/ynab/migration/start", json={"plan_id": ynab_stub.PLAN, "sync_direction": "to_ynab"}
    )
    assert r.status_code == 202, r.text
    deadline = time.time() + 90
    while ynab_migration.status()["job"]["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert ynab_sync.status()["direction"] == "to_ynab"

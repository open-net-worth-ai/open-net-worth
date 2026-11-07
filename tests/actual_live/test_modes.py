"""Tracking and encrypted budgets, end to end on a real server."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing

import pytest

from backend.core.errors import ExternalServiceError
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services import planning
from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync import client as actual_client
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from tests.actual_live.harness import PASSWORD
from tests.actual_live.world import (
    THIS_MONTH,
    balances_here,
    balances_there,
    edit_here,
    envelope_here,
    open_items,
    quiet,
    ready_to_assign,
    row_here,
    rows_here,
    run,
    sync,
    there,
    warnings,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)

ENCRYPTION = "made-up-encryption-words"


def test_a_tracking_budget_imports_without_assigned_and_syncs_the_rest(world):
    budget = world.seed(tracking=True)
    assert budget.steps(["call", "getBudgetMonth", THIS_MONTH])[0]["totalBudgeted"] != 0
    world.imported(budget)
    assert not any(planning.assignments_store.month_map(THIS_MONTH).values()), (
        "a tracking budget writes no Assigned"
    )
    assert ready_to_assign() > 0, "its cash starts in Ready to Assign"
    quiet()
    assert actual_app.sync_status()["budget_mode"] == "tracking"

    edit_here(row_here(memo="plain")["id"], memo="tracking here", from_amount=44.0)
    sync()
    assert [r["amount"] for r in there(budget.view(), "tracking here")] == [-4400]
    budget.steps(["edit", "other row", {"notes": "tracking there", "amount": -1600}])
    sync()
    assert float(row_here(memo="tracking there")["from_amount"]) == 16.0
    assert balances_here() == balances_there(budget.view())
    quiet()


def test_tracking_assigned_never_travels_either_way(world):
    budget = world.seed(tracking=True)
    world.imported(budget)
    quiet()
    food = budget.ids["food"]
    budget.call("setBudgetAmount", THIS_MONTH, food, 99900)
    quiet()
    assert not any(planning.assignments_store.month_map(THIS_MONTH).values())
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": THIS_MONTH,
            "envelope_id": envelope_here("ACME Food")["id"],
            "amount": 12.0,
        }
    )
    sync()
    view = budget.view(THIS_MONTH)
    assert view["budget"][THIS_MONTH]["categories"][food]["budgeted"] == 99900
    assert open_items() == []


def test_a_mode_change_during_the_sync_moves_no_figure(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    food = budget.ids["food"]
    budget.steps(["send", "preferences/save", {"id": "budgetType", "value": "tracking"}])
    sync()
    assert actual_app.sync_status()["budget_mode"] == "tracking"
    assigned_before = planning.assignments_store.month_map(THIS_MONTH).copy()
    budget.call("setBudgetAmount", THIS_MONTH, food, 10000)
    sync()
    assert planning.assignments_store.month_map(THIS_MONTH) == assigned_before

    budget.steps(["send", "preferences/save", {"id": "budgetType", "value": "envelope"}])
    sync()
    assert actual_app.sync_status()["budget_mode"] == "envelope"
    assert planning.assignments_store.month_map(THIS_MONTH) == assigned_before, "no figure moves"
    sync()
    assert planning.assignments_store.month_map(THIS_MONTH) == assigned_before


def test_an_encrypted_budget_is_refused_without_its_password_and_with_a_wrong_one(world):
    budget = world.seed(encryption=ENCRYPTION)
    with pytest.raises(ExternalServiceError) as missing:
        with using(ACTUAL), actual_client.open_copy(None, budget.file):
            pass
    assert "encrypt" in str(missing.value).lower()
    ActualBudgetConfigStore().update({"encryption_password": "not-the-words"})
    with pytest.raises(ExternalServiceError) as wrong:
        with using(ACTUAL), actual_client.open_copy(None, budget.file):
            pass
    assert "encrypt" in str(wrong.value).lower()
    assert ENCRYPTION not in str(wrong.value)
    assert list(actual_client.work_root().iterdir()) == []


def test_an_encrypted_budget_end_to_end_and_the_server_holds_no_plain_row(world):
    budget = world.seed(encryption=ENCRYPTION)
    listed = next(b for b in actual_client.list_budgets() if b["id"] == budget.file)
    assert listed["encrypted"] is True
    ActualBudgetConfigStore().update({"encryption_password": ENCRYPTION})
    world.imported(budget)
    assert balances_here() == balances_there(budget.view())
    quiet()

    edit_here(row_here(memo="plain")["id"], memo="secret memo here", from_amount=29.0)
    sync()
    assert [r["amount"] for r in there(budget.view(), "secret memo here")] == [-2900]
    budget.steps(["edit", "other row", {"notes": "secret memo there"}])
    sync()
    assert rows_here(memo="secret memo there")
    quiet()
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": THIS_MONTH,
            "envelope_id": envelope_here("ACME Food")["id"],
            "amount": 345.0,
        }
    )
    sync()
    food = budget.ids["food"]
    assert budget.view(THIS_MONTH)["budget"][THIS_MONTH]["categories"][food]["budgeted"] == 34500
    quiet()

    server_file = world.server.root / "server-data" / "user-files" / f"group-{budget.group}.sqlite"
    with closing(sqlite3.connect(server_file)) as conn:
        kept = conn.execute("select is_encrypted, content from messages_binary").fetchall()
    assert kept and all(flag == 1 for flag, _ in kept), "what we send is encrypted as Actual does"
    assert not any(b"secret memo here" in bytes(content) for _, content in kept)
    assert list(actual_client.work_root().iterdir()) == [], "no decrypted copy stays behind"
    assert warnings() == {}


def test_a_wrong_encryption_password_stops_a_run_and_leaves_nothing_behind(world):
    budget = world.seed(encryption=ENCRYPTION)
    ActualBudgetConfigStore().update({"encryption_password": ENCRYPTION})
    world.imported(budget)
    quiet()
    ActualBudgetConfigStore().update({"encryption_password": "not-the-words"})
    last = run()
    assert not last["ok"]
    assert "encrypt" in last["error"].lower() and "not-the-words" not in last["error"]
    assert list(actual_client.work_root().iterdir()) == []
    ActualBudgetConfigStore().update({"encryption_password": ENCRYPTION})
    quiet()


def test_a_refused_password_is_plain_and_nothing_logged_carries_the_address(world, caplog):
    budget = world.seed()
    world.imported(budget)
    with caplog.at_level(logging.DEBUG):
        quiet()
    ActualBudgetConfigStore().update({"password": "not-the-password"})
    with caplog.at_level(logging.DEBUG):
        last = run()
    assert not last["ok"]
    assert "refused the sign-in" in last["error"]
    assert ActualBudgetConfigStore().public()["last_status"] == "auth_error"
    for text in (world.server.url, "127.0.0.1", "not-the-password", PASSWORD):
        assert text not in last["error"]
        assert not [r for r in caplog.records if text in r.getMessage()], text
    assert list(actual_client.work_root().iterdir()) == []
    ActualBudgetConfigStore().update({"password": PASSWORD})
    quiet()
    assert ActualBudgetConfigStore().public()["last_status"] != "auth_error"

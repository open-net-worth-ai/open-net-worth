"""Where the money lands: salary, openings, and the available port against the official client's
own budget sheets, in both budget modes."""

from __future__ import annotations

import pytest

from backend.services import categories as categories_service
from backend.services import ledger as ledger_service
from backend.services.actual_sync import client as actual_client
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from backend.services.envelopes import all_envelopes
from tests.actual_live.world import (
    FIRST_SHEET_MONTH,
    LAST_DAY,
    LAST_MONTH,
    THIS_MONTH,
    TODAY,
    account_here,
    balances_here,
    balances_there,
    live_rows,
    quiet,
    ready_to_assign,
    row_here,
    rows_here,
    sync,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def test_salary_raises_ready_to_assign_and_has_no_envelope(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    income = next(c for c in budget.view()["categories"] if c["id"] == budget.ids["income"])
    here = next(c for c in categories_service.all_category_rows() if c["label"] == income["name"])
    assert here["kind"] == "income"
    assert income["name"] not in {e["name"] for e in all_envelopes()}
    before = ready_to_assign()
    budget.call(
        "addTransactions",
        budget.ids["checking"],
        [{"date": TODAY, "amount": 123400, "payee_name": "ACME Payroll", "category": income["id"],
          "notes": "november salary"}],
    )  # fmt: skip
    sync()
    row = row_here(memo="november salary")
    assert row["category"] == here["id"]
    assert ready_to_assign() == pytest.approx(before + 1234.0)
    quiet()


def test_a_blank_inflow_here_stays_blank_there_and_is_told_once(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    from tests.actual_live.world import new_row

    before = ready_to_assign()
    new_row(kind="income", memo="blank inflow", description="ACME Refund", from_account_id=None,
            from_amount=None, from_currency=None, to_account_id=account_here("ACME Checking")["id"],
            to_amount=50.0, to_currency="USD")  # fmt: skip
    sync()
    assert ready_to_assign() == pytest.approx(before + 50.0)
    (row,) = [r for r in budget.view()["rows"] if r["notes"] == "blank inflow"]
    assert row["category"] is None
    assert row["amount"] == 5000
    quiet()


def test_an_opening_stays_an_opening(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    openings = ledger_service.opening_txn_ids()
    for name, cents in (("ACME Checking", 500000), ("ACME Savings", 100000)):
        account = account_here(name)
        row = next(t for t in live_rows() if t["id"] == account["opening_txn_id"])
        assert row["id"] in openings
        assert not row.get("category"), "Starting Balances isn't a category here"
        amount = row.get("to_amount") or row.get("from_amount")
        assert round(float(amount) * 100) == cents
    assert "Starting Balances" not in {
        c["label"] for c in categories_service.all_category_rows() if not c.get("hidden")
    }
    assert balances_here() == balances_there(budget.view())
    assert len(rows_here(description="Starting Balance")) >= 2
    # An untouched opening is never sent back, whatever it reads as here.
    view = budget.view()
    quiet()
    assert budget.view() == view


def _available_agrees(budget, months: list[str]) -> None:
    with using(ACTUAL), actual_client.open_copy(None, budget.file) as copy:
        plan = copy.plan()
    view = budget.view(*months)
    income = {c["id"] for c in view["categories"] if c["is_income"]}
    compared = 0
    for month in months:
        ours = {
            c["id"]: c
            for c in next(m for m in plan["months"] if m["month"] == f"{month}-01")["categories"]
        }
        for cid, theirs in view["budget"][month]["categories"].items():
            if cid in income:
                continue
            assert ours[cid]["balance"] == theirs["balance"] * 10, (month, cid)
            assert ours[cid]["activity"] == theirs["spent"] * 10, (month, cid)
            compared += 1
    assert compared >= 2 * len(months)


def _overspent_book(budget) -> None:
    ids = budget.ids
    budget.steps(
        ["call", "setBudgetAmount", LAST_MONTH, ids["food"], 5000],
        ["call", "setBudgetAmount", LAST_MONTH, ids["fun"], 2000],
        ["call", "addTransactions", ids["checking"], [
            {"date": LAST_DAY, "amount": -9000, "payee_name": "ACME Over", "category": ids["food"], "notes": "over"},
            {"date": LAST_DAY, "amount": -500, "payee_name": "ACME Under", "category": ids["fun"], "notes": "under"},
        ]],
    )  # fmt: skip


def test_available_agrees_with_the_official_client_on_an_envelope_budget(world):
    budget = world.seed()
    _overspent_book(budget)
    _available_agrees(budget, [LAST_MONTH, THIS_MONTH])
    budget.steps(["call", "setBudgetCarryover", LAST_MONTH, budget.ids["food"], True])
    _available_agrees(budget, [LAST_MONTH, THIS_MONTH])


def test_available_agrees_with_the_official_client_on_a_tracking_budget(world):
    budget = world.seed(tracking=True)
    _overspent_book(budget)
    _available_agrees(budget, [LAST_MONTH, THIS_MONTH])
    budget.steps(["call", "setBudgetCarryover", LAST_MONTH, budget.ids["fun"], True])
    _available_agrees(budget, [LAST_MONTH, THIS_MONTH])


def test_the_official_clients_blind_spot_is_its_own(world):
    """The first month of a budget's sheets: the official client's ``getBudgetMonth`` answers 0 for
    a figure the official client itself just wrote. Not ours, and the port reads the true figure.
    Pinned so a new client version that changes this is noticed."""
    budget = world.seed()
    budget.call("setBudgetAmount", FIRST_SHEET_MONTH, budget.ids["food"], 5900)
    theirs = budget.view(FIRST_SHEET_MONTH)["budget"][FIRST_SHEET_MONTH]["categories"]
    assert theirs[budget.ids["food"]]["budgeted"] == 0
    with using(ACTUAL), actual_client.open_copy(None, budget.file) as copy:
        months = copy.plan()["months"]
    ours = next(m for m in months if m["month"] == f"{FIRST_SHEET_MONTH}-01")["categories"]
    assert next(c for c in ours if c["id"] == budget.ids["food"])["budgeted"] == 59000


def test_verify_passes_on_an_envelope_budget_with_no_ready_to_assign_row(world):
    budget = world.seed()
    world.imported(budget)
    checked = world.verify(budget)
    assert checked["ok"] and checked["mismatches"] == []
    labels = " ".join(row["check"] for row in checked["matches"])
    assert "Ready to Assign" not in labels
    assert "balance" in labels and "available" in labels and "assigned" in labels


def test_verify_reads_carryover_as_a_known_difference(world):
    budget = world.seed()
    _overspent_book(budget)
    budget.steps(["call", "setBudgetCarryover", LAST_MONTH, budget.ids["food"], True])
    world.imported(budget)
    checked = world.verify(budget)
    assert checked["ok"] and checked["mismatches"] == []


def test_verify_on_a_tracking_budget_checks_balances_and_never_fails_on_assigned(world):
    budget = world.seed(tracking=True)
    world.imported(budget)
    checked = world.verify(budget)
    assert checked["ok"] and checked["mismatches"] == []
    assert "balance" in " ".join(row["check"] for row in checked["matches"])

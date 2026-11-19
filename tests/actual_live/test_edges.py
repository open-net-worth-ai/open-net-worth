"""Edges a code review traced but nobody ran: a category Actual can't hold on a transfer between
two budget accounts or on an off-budget row, and an amount finer than a cent."""

from __future__ import annotations

import pytest

from tests.actual_live.world import (
    account_here,
    balances_here,
    balances_there,
    category_here,
    edit_here,
    new_row,
    quiet,
    row_here,
    sync,
    there,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def _edit_there(budget, notes: str, new_notes: str) -> None:
    budget.steps(["edit", notes, {"notes": new_notes}])


def test_a_categorized_transfer_between_budget_accounts_keeps_its_category_here(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    food = category_here("ACME Food")["id"]
    new_row(
        kind="transfer",
        description="",
        memo="cat transfer",
        from_amount=40.0,
        to_account_id=account_here("ACME Card")["id"],
        to_amount=40.0,
        to_currency="USD",
        category=food,
    )
    sync()
    assert row_here(memo="cat transfer").get("category") == food
    quiet()
    legs = there(budget.view(), "cat transfer")
    budget.steps(["call", "updateTransaction", legs[0]["id"], {"notes": "cat transfer there"}])
    sync()
    assert row_here(memo="cat transfer there").get("category") == food
    assert balances_here() == balances_there(budget.view())
    quiet()


def test_a_category_on_an_off_budget_row_keeps_its_category_here(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    food = category_here("ACME Food")["id"]
    new_row(account="ACME Savings", memo="off cat", category=food)
    sync()
    assert row_here(memo="off cat").get("category") == food
    quiet()
    _edit_there(budget, "off cat", "off cat there")
    sync()
    assert row_here(memo="off cat there").get("category") == food
    quiet()


def test_an_amount_finer_than_a_cent_goes_as_the_nearest_cent_and_stays_put(world):
    """Actual stores whole cents. The ledger keeps its figure, Actual holds the nearest cent, and
    neither run reads the difference as a change; an edit that moves the cent is sent."""
    budget = world.seed()
    world.imported(budget)
    quiet()
    new_row(memo="fine", from_amount=12.345)
    sync()
    assert [r["amount"] for r in there(budget.view(), "fine")] == [-1235]
    assert float(row_here(memo="fine")["from_amount"]) == 12.345
    quiet()
    edit_here(row_here(memo="fine")["id"], from_amount=12.346)
    quiet()
    edit_here(row_here(memo="fine")["id"], from_amount=12.334)
    assert sync()["summary"].startswith("1 sent")
    assert [r["amount"] for r in there(budget.view(), "fine")] == [-1233]
    quiet()

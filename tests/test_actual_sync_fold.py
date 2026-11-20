"""Folding a category into another here, and a categorized transfer to an off-budget account:
two changes the Actual sync used to leave half done. Same harness as ``test_actual_sync_engine``;
every name and number is invented."""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import planning
from tests import test_actual_sync_engine as engine
from tests.test_actual_sync_engine import (
    by_ref,
    category_here,
    edit_here,
    enable,
    open_items,
    sync,
    writes_seen,
)

world = engine.world  # the engine tests' fixture: an Actual budget, imported

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def _fold_fun_into_food() -> None:
    planning.apply_budget_change(
        {
            "type": "category_delete",
            "key": category_here("Fun")["id"],
            "reassign": True,
            "reassign_to": category_here("Food")["id"],
        }
    )


def test_a_category_folded_here_holds_nothing_in_actual(world):
    """Its Assigned there is cleared, or Actual would count it beside the target's, which took
    this month's money with the activity."""
    enable()
    sync()
    _fold_fun_into_food()
    sync()
    (fun,) = world.rows("zero_budgets", "category = 'c-fun'")
    assert fun["amount"] == 0
    assert world.row("transactions", "t-l2")["category"] == "c-food"
    assert world.row("categories", "c-fun")["hidden"] == 1
    assert open_items() == []
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_a_category_folded_here_in_to_this_book_mode_sends_nothing(world):
    enable("from_ynab")
    sync()
    _fold_fun_into_food()
    world.calls.clear()
    sync()
    (fun,) = world.rows("zero_budgets", "category = 'c-fun'")
    assert fun["amount"] == 10_000 and writes_seen(world) == []


def test_a_category_on_a_transfer_to_an_off_budget_account_reaches_actual(world):
    enable()
    sync()
    out = by_ref("t-out")
    edit_here(out["id"], category=category_here("Food")["id"])
    sync()
    assert world.row("transactions", "t-out")["category"] == "c-food"
    assert open_items() == []
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []
    assert (
        ledger_service.transactions.find_by_id(out["id"])["category"] == category_here("Food")["id"]
    )

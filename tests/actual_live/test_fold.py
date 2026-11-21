"""Folding one category into another here, with the sync on: Actual follows, and neither side's
money to budget moves. Every name and number is invented."""

from __future__ import annotations

import pytest

from backend.services import planning
from tests.actual_live.world import (
    THIS_MONTH,
    balances_here,
    balances_there,
    category_here,
    open_items,
    quiet,
    ready_to_assign,
    sync,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def _to_budget(view: dict) -> int:
    return view["budget"][THIS_MONTH]["toBudget"]


def test_folding_a_category_here_moves_its_rows_and_budget_in_actual(world):
    """The seed's Fun has 125.00 budgeted this month, a plain row, a split line, a card row and a
    categorized transfer to an off-budget account. Folded into Food here, every one of them moves
    to Food in Actual, Fun holds nothing there, and To Budget doesn't move on either side."""
    budget = world.seed()
    world.imported(budget)
    quiet()
    ids = budget.ids
    before = budget.view(THIS_MONTH)
    rta_before = ready_to_assign()
    assert before["budget"][THIS_MONTH]["categories"][ids["fun"]]["budgeted"] == 12500

    planning.apply_budget_change(
        {
            "type": "category_delete",
            "key": category_here("ACME Fun")["id"],
            "reassign": True,
            "reassign_to": category_here("ACME Food")["id"],
        }
    )
    sync()
    after = budget.view(THIS_MONTH)
    lines = [s for r in after["rows"] for s in r["subs"]]
    assert [r for r in after["rows"] + lines if r["category"] == ids["fun"]] == []
    fun = after["budget"][THIS_MONTH]["categories"][ids["fun"]]
    assert fun == {"budgeted": 0, "spent": 0, "balance": 0}
    assert _to_budget(after) == _to_budget(before)
    assert ready_to_assign() == rta_before
    assert balances_here() == balances_there(after)
    assert open_items() == []
    quiet()


def test_a_fresh_start_here_clears_the_month_in_actual(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    ids = budget.ids
    assert budget.view(THIS_MONTH)["budget"][THIS_MONTH]["categories"][ids["fun"]]["budgeted"]
    planning.fresh_start(THIS_MONTH)
    sync()
    cats = budget.view(THIS_MONTH)["budget"][THIS_MONTH]["categories"]
    assert {cats[ids[k]]["budgeted"] for k in ("food", "fun")} == {0}
    assert open_items() == []
    quiet()

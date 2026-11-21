"""Actual's To Budget, worked out here from a sync's read, against Actual's own engine (the
official client's getBudgetMonth): it must be the same figure. Every name and number is invented."""

from __future__ import annotations

import pytest

from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services.actual_sync import available
from backend.services.actual_sync import client as actual_client
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from tests.actual_live.world import LAST_MONTH, THIS_MONTH

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def _ours(budget, month: str) -> int:
    with using(ACTUAL):
        with actual_client.open_copy(ActualBudgetConfigStore(), budget.file) as copy:
            _rows, plan = copy.read()
    return available.to_budget(plan, month) // 10  # milliunits -> Actual's cents


def _theirs(budget, month: str) -> int:
    return budget.view(month)["budget"][month]["toBudget"]


@pytest.mark.parametrize("month", [LAST_MONTH, THIS_MONTH])
def test_to_budget_matches_actuals_own_figure(world, month):
    budget = world.seed()
    assert _ours(budget, month) == _theirs(budget, month)


def test_it_matches_with_overspending_carryover_and_a_hold(world):
    budget = world.seed()
    ids = budget.ids
    # Fun overspends last month; Food carries its overspend; money is held for next month.
    budget.call("setBudgetAmount", LAST_MONTH, ids["fun"], 100)
    budget.call("setBudgetAmount", LAST_MONTH, ids["food"], 100)
    budget.call("setBudgetCarryover", LAST_MONTH, ids["food"], True)
    budget.steps(["send", "budget/hold-for-next-month", {"month": THIS_MONTH, "amount": 20000}])
    for month in (LAST_MONTH, THIS_MONTH):
        assert _ours(budget, month) == _theirs(budget, month), month


def test_the_sync_page_shows_actuals_own_figure(world):
    from backend.services.actual_sync import app as actual_app
    from tests.actual_live.world import quiet

    budget = world.seed()
    world.imported(budget)
    quiet()
    with using(ACTUAL):
        figures = actual_app.sync_status()["to_budget"]
    assert figures["month"] == THIS_MONTH
    assert round(figures["theirs"] * 100) == _theirs(budget, THIS_MONTH)

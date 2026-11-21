"""Undo the last sync here, against a real Actual server: the row it changed comes back, the sync
switches off, and turning it on again sends the row back to Actual as a change made here.
Every name and number is invented."""

from __future__ import annotations

import pytest

from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from tests.actual_live.world import quiet, row_here, sync, there

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def test_undo_here_then_back_on_sends_it_to_actual(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    (theirs,) = there(budget.view(), "plain")
    budget.call("updateTransaction", theirs["id"], {"notes": "plain, edited there"})
    assert "pulled" in sync()["summary"]
    assert row_here(memo="plain, edited there")
    with using(ACTUAL):
        done = actual_app.undo_last_sync()
        assert done["put_back"] == 1 and done["status"]["enabled"] is False
    assert row_here(memo="plain")
    assert there(budget.view(), "plain, edited there"), "Actual untouched while the sync is off"
    with using(ACTUAL):
        actual_app.sync_settings(True, None, None, None)
    sync()
    assert there(budget.view(), "plain")
    quiet()

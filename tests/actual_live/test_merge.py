"""A transaction changed on both sides, in a real Actual server: different fields both stand;
the same field changed two ways goes to review. Every name and number is invented."""

from __future__ import annotations

import pytest

from tests.actual_live.world import (
    category_here,
    edit_here,
    open_items,
    quiet,
    row_here,
    sync,
    there,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def test_a_memo_here_and_a_category_there_both_stand(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="plain, edited here")
    (theirs,) = there(budget.view(), "plain")
    budget.call("updateTransaction", theirs["id"], {"category": budget.ids["fun"]})
    sync()
    assert open_items() == []
    assert row_here(memo="plain, edited here")["category"] == category_here("ACME Fun")["id"]
    (now,) = there(budget.view(), "plain, edited here")
    assert now["category"] == budget.ids["fun"]
    quiet()


def test_the_same_note_changed_two_ways_waits_for_review(world):
    budget = world.seed()
    world.imported(budget)
    quiet()
    edit_here(row_here(memo="plain")["id"], memo="mine")
    (theirs,) = there(budget.view(), "plain")
    budget.call("updateTransaction", theirs["id"], {"notes": "theirs"})
    sync()
    assert [i["kind"] for i in open_items()] == ["conflict"]
    assert row_here(memo="mine") and there(budget.view(), "theirs")

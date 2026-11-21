"""A transaction changed on both sides since the last sync: different fields both stand, the
same field changed two ways goes to review. Same harness as ``test_actual_sync_engine``; every
name and number is invented."""

from __future__ import annotations

import pytest

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


def _quiet(world) -> None:
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed" and writes_seen(world) == []


def test_a_memo_here_and_a_category_there_both_stand(world):
    enable()
    sync()
    edit_here(by_ref("t-market")["id"], memo="weekly shop")
    world.edit("UPDATE transactions SET category = 'c-fun' WHERE id = 't-market'")
    world.queue.clear()
    sync()
    assert open_items() == []
    here = by_ref("t-market")
    assert here["memo"] == "weekly shop" and here["category"] == category_here("Fun")["id"]
    there = world.row("transactions", "t-market")
    assert there["notes"] == "weekly shop" and there["category"] == "c-fun"
    _quiet(world)


def test_an_amount_here_and_a_payee_there_both_stand(world):
    enable()
    sync()
    edit_here(by_ref("t-market")["id"], from_amount=31.0)
    world.edit("UPDATE transactions SET notes = 'from the app' WHERE id = 't-market'")
    world.queue.clear()
    sync()
    assert open_items() == []
    assert world.row("transactions", "t-market")["amount"] == -3_100
    assert by_ref("t-market")["memo"] == "from the app"
    _quiet(world)


def test_the_same_field_changed_two_ways_goes_to_review(world):
    enable()
    sync()
    edit_here(by_ref("t-market")["id"], memo="here")
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-market'")
    world.queue.clear()
    sync()
    assert [i["kind"] for i in open_items()] == ["conflict"]
    assert by_ref("t-market")["memo"] == "here"
    assert world.row("transactions", "t-market")["notes"] == "there"


def test_one_side_on_a_group_the_other_changed_too_goes_to_review(world):
    """A category and a split's lines travel together: one side may change them, not both."""
    enable()
    sync()
    edit_here(by_ref("t-market")["id"], category=category_here("Fun")["id"], memo="mine")
    world.edit("UPDATE transactions SET category = 'c-pay' WHERE id = 't-market'")
    world.queue.clear()
    sync()
    assert [i["kind"] for i in open_items()] == ["conflict"]


def test_a_one_way_run_still_asks(world):
    enable("from_ynab")
    sync()
    edit_here(by_ref("t-market")["id"], memo="weekly shop")
    world.edit("UPDATE transactions SET category = 'c-fun' WHERE id = 't-market'")
    world.queue.clear()
    sync()
    assert [i["kind"] for i in open_items()] == ["conflict"]

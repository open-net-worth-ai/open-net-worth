"""The dry run says what the next sync would do and writes nothing on either side. Same harness
as ``test_actual_sync_engine``; nothing real."""

from __future__ import annotations

import pytest

from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from backend.services.ynab_sync import state as sync_state
from tests import test_actual_sync_engine as engine
from tests.test_actual_sync_engine import (
    ActualBaselineStore,
    ActualMirrorStore,
    by_ref,
    edit_here,
    enable,
    open_items,
    row,
    sync,
    writes_seen,
)

world = engine.world

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)


def _state() -> dict:
    with using(ACTUAL):
        return sync_state.load()


def test_a_preview_counts_both_ways_and_writes_nothing(world):
    enable()
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-market'")
    world.queue.clear()
    world.calls.clear()
    before_state, before_memo = _state(), by_ref("t-market")["memo"]
    stores = (ActualBaselineStore().all(), ActualMirrorStore().all())
    seen = actual_app.sync_preview()
    tx = seen["transactions"]["counts"]
    assert tx == {"change_there": 1, "change_here": 1}
    assert seen["categories_left_out"] is False
    assert writes_seen(world) == [] and open_items() == []
    assert world.row("transactions", "t-pay")["notes"] is None, "nothing sent"
    assert by_ref("t-market")["memo"] == before_memo, "nothing pulled"
    assert _state() == before_state
    assert (ActualBaselineStore().all(), ActualMirrorStore().all()) == stores
    run = sync()
    assert run["summary"].startswith("1 sent to Actual, 1 pulled")


def test_a_preview_names_what_waits_for_review(world):
    enable()
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    world.edit("UPDATE transactions SET notes = 'there' WHERE id = 't-pay'")
    world.queue.clear()
    seen = actual_app.sync_preview()
    assert seen["transactions"]["counts"] == {"review": 1}
    assert seen["transactions"]["samples"]["review"][0].endswith("ACME Corp")
    assert open_items() == []


def test_a_one_way_preview_says_what_it_holds_back(world):
    enable("from_ynab")
    sync()
    edit_here(row("ACME Corp")["id"], memo="here")
    seen = actual_app.sync_preview()
    assert seen["transactions"]["counts"] == {"held": 1}


def test_a_preview_names_category_changes_both_ways_and_writes_nothing(world):
    from backend.services import planning
    from tests.test_actual_sync_engine import category_here

    enable()
    sync()
    world.edit("UPDATE categories SET name = 'Groceries' WHERE id = 'c-food'")
    world.queue.clear()
    planning.apply_budget_change(
        {"type": "category_rename", "key": category_here("Fun")["id"], "new_label": "Leisure"}
    )
    planning.apply_budget_change({"type": "category_create", "label": "Pets", "kind": "expense"})
    world.calls.clear()
    before = world.rows("categories")
    seen = actual_app.sync_preview()["categories"]
    assert seen["counts"].get("name_here") == 1 and seen["samples"]["name_here"] == ["Food"]
    assert seen["counts"].get("name_there") == 1 and seen["samples"]["name_there"] == ["Fun"]
    assert "Pets" in seen["samples"].get("new_there", [])
    assert world.rows("categories") == before and writes_seen(world) == []
    assert category_here("Food")["label"] == "Food", "nothing pulled"
    sync()
    assert category_here("Groceries") and world.row("categories", "c-fun")["name"] == "Leisure"


def test_a_preview_says_a_fold_hides_the_category_in_actual(world):
    from backend.services import planning
    from tests.test_actual_sync_engine import category_here

    enable()
    sync()
    planning.apply_budget_change(
        {
            "type": "category_delete",
            "key": category_here("Fun")["id"],
            "reassign": True,
            "reassign_to": category_here("Food")["id"],
        }
    )
    seen = actual_app.sync_preview()["categories"]
    assert seen["samples"].get("hide_there") == ["Fun"]
    assert world.row("categories", "c-fun")["hidden"] == 0


def test_the_status_shows_ready_to_assign_beside_actuals_to_budget(world):
    enable()
    sync()
    figures = actual_app.sync_status()["to_budget"]
    assert figures is not None
    assert figures["difference"] == round(figures["here"] - figures["theirs"], 2)
    assert figures["card_payment_envelopes"] >= 0

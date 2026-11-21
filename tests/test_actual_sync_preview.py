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
    assert seen["categories_left_out"] is True
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

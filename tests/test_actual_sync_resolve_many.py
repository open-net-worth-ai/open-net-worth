"""One answer for many review items: one session with Actual, so one restore point from before
it, and an item that can't be answered stays while the rest go on. Same harness as
``test_actual_sync_engine``; nothing real."""

from __future__ import annotations

import pytest

from backend.core.errors import ConflictError, ValidationError
from backend.services import ledger as ledger_service
from backend.services.actual_sync import app as actual_app
from backend.services.actual_sync import restore_points
from backend.services.actual_sync.app import ACTUAL
from backend.services.budget_app import using
from backend.services.ynab_sync import resolve, units
from tests import test_actual_sync_engine as engine
from tests.test_actual_sync_engine import edit_here, enable, open_items, sync

world = engine.world

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)

_THERE = {"t-pay": "ACME Corp", "t-market": "Corner Market"}


def here(tid: str) -> dict:
    """The row here that stands for Actual's ``tid``."""
    with using(ACTUAL):
        (found,) = [
            t
            for t in ledger_service.transactions.all()
            if not t.get("deleted") and tid in units.ynab_refs(t)
        ]
    return found


@pytest.fixture(autouse=True)
def _own_folder(tmp_path, monkeypatch):
    folder = tmp_path / "restore-points"
    folder.mkdir()
    monkeypatch.setattr(restore_points, "_folder", lambda: folder)


def _two_conflicts(world) -> list[dict]:
    enable()
    sync()
    for tid in _THERE:
        edit_here(here(tid)["id"], memo=f"here {tid}")
        world.edit(f"UPDATE transactions SET notes = 'there {tid}' WHERE id = '{tid}'")
    world.queue.clear()
    sync()
    items = open_items()
    assert len(items) == 2 and {i["kind"] for i in items} == {"conflict"}
    return items


def test_keeping_ours_for_all_writes_actual_in_one_session_with_one_restore_point(world):
    items = _two_conflicts(world)
    points = len(actual_app.sync_status()["restore_points"])
    world.calls.clear()
    out = actual_app.resolve_sync_items([i["id"] for i in items], "onw")
    assert out["resolved"] == 2 and out["failed"] == []
    assert out["status"]["conflicts"] == []
    assert len(out["status"]["restore_points"]) == points + 1, "one session, one restore point"
    for tid in _THERE:
        assert world.row("transactions", tid)["notes"] == f"here {tid}"
    world.calls.clear()
    assert sync()["summary"] == "Nothing changed"


def test_answered_one_by_one_each_keeps_a_restore_point(world):
    """Why it is one request: each answer alone is its own session with Actual."""
    items = _two_conflicts(world)
    points = len(actual_app.sync_status()["restore_points"])
    for item in items:
        actual_app.resolve_sync_item(item["id"], "onw")
    assert len(actual_app.sync_status()["restore_points"]) == points + 2


def test_keeping_actuals_for_all_writes_here(world):
    items = _two_conflicts(world)
    out = actual_app.resolve_sync_items([i["id"] for i in items], "ynab")
    assert out["resolved"] == 2
    for tid in _THERE:
        assert here(tid)["memo"] == f"there {tid}"


def test_one_that_fails_stays_and_the_rest_go_on(world, monkeypatch):
    items = _two_conflicts(world)
    real = resolve._transaction

    def refuse_market(ctx, item, keep, client, base):
        if _THERE["t-market"] in item["title"]:
            raise ConflictError("Actual did not accept it: ACME refusal")
        return real(ctx, item, keep, client, base)

    monkeypatch.setattr(resolve, "_transaction", refuse_market)
    out = actual_app.resolve_sync_items([i["id"] for i in items], "onw")
    assert out["resolved"] == 1
    (failed,) = out["failed"]
    assert "ACME refusal" in failed["reason"]
    (left,) = out["status"]["conflicts"]
    assert _THERE["t-market"] in left["title"]
    assert world.row("transactions", "t-pay")["notes"] == "here t-pay"


def test_only_plain_transaction_conflicts_are_answered_together(world, monkeypatch):
    items = _two_conflicts(world)
    monkeypatch.setitem(items[0], "entity", "opening")
    monkeypatch.setattr(resolve.conflicts, "get", lambda i: items[0] if i == "x" else None)
    with pytest.raises(ValidationError, match="answered together"):
        actual_app.resolve_sync_items(["x"], "onw")

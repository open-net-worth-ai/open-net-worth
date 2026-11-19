"""What Actual held is kept as the source of the ledger row it booked: the import and every sync
read stash Actual's own row (before reshaping) under ``actual_budget``, keyed by the ref the
ledger row carries, and the drawer's source list shows it. Every name and number is invented."""

from __future__ import annotations

import pytest

from backend.services import connector_snapshots, matching
from backend.services import ledger as ledger_service
from tests.test_actual_sync_engine import (  # noqa: F401
    enable,
    row,
    sync,
    world,
)

pytestmark = pytest.mark.flags(actual_budget_sync=True, ynab_import_and_sync=True)

SOURCE = "actual_budget"


def stored(ref: str) -> dict:
    found = connector_snapshots._snapshots.get(SOURCE, ref)
    assert found is not None, ref
    return found


def test_an_import_keeps_actuals_own_row_and_the_drawer_shows_it(world):  # noqa: F811
    pay = row("ACME Corp")
    snap = stored("t-pay")
    assert snap["referenced"] is True and snap["provider_account_id"] is None
    assert snap["raw"]["acct"] == "a1" and snap["raw"]["category"] == "c-pay"
    assert snap["raw"]["reconciled"] == 0 and snap["raw"]["sort_order"] == 1.0
    assert "financial_id" in snap["raw"] and "imported_description" in snap["raw"]
    (entry,) = connector_snapshots.snapshots_for_transaction(pay["id"])
    assert entry["unavailable"] is False and entry["provider_label"] == "Actual Budget"
    assert entry["raw"]["id"] == "t-pay"


def test_an_opening_balance_keeps_its_flag(world):  # noqa: F811
    assert stored("t-open")["raw"]["starting_balance_flag"] == 1


def test_a_split_keeps_its_lines_under_the_parent_and_a_transfer_both_legs(world):  # noqa: F811
    split = stored("t-split")["raw"]
    assert split["isParent"] == 1
    assert sorted(c["id"] for c in split["children"]) == ["t-l1", "t-l2"]
    assert {c["notes"] for c in split["children"]} == {"one", "two"}
    assert {"t-out", "t-in"} <= {
        e["ref"]
        for t in ledger_service.transactions.all()
        for e in t.get("external_refs") or []
        if e["source"] == SOURCE
    }
    assert stored("t-out")["referenced"] and stored("t-in")["referenced"]
    assert connector_snapshots._snapshots.get(SOURCE, "t-l1") is None


def test_an_edit_in_actual_updates_the_snapshot_on_the_next_sync(world):  # noqa: F811
    enable()
    sync()
    before = stored("t-pay")
    world.edit("UPDATE transactions SET notes = 'bonus', reconciled = 1 WHERE id = 't-pay'")
    world.queue.clear()
    sync()
    after = stored("t-pay")
    assert after["raw"]["notes"] == "bonus" and after["raw"]["reconciled"] == 1
    assert after["referenced"] is True
    assert after["content_hash"] == before["content_hash"]


def test_a_row_booked_before_the_snapshots_existed_gains_one_on_the_next_sync(world):  # noqa: F811
    connector_snapshots._snapshots.delete(connector_snapshots.snapshot_key(SOURCE, "t-pay"))
    enable()
    sync()
    assert stored("t-pay")["referenced"] is True
    (entry,) = connector_snapshots.snapshots_for_transaction(row("ACME Corp")["id"])
    assert entry["unavailable"] is False


def test_an_unchanged_sync_writes_no_snapshot(world, monkeypatch):  # noqa: F811
    enable()
    sync()
    writes = []
    real = connector_snapshots._snapshots.bulk_patch
    monkeypatch.setattr(
        connector_snapshots._snapshots,
        "bulk_patch",
        lambda u, **kw: writes.append(u) or real(u, **kw),
    )
    sync()
    assert all(not patch for patch in writes)


def test_a_failing_stash_does_not_fail_or_change_the_sync(world, monkeypatch):  # noqa: F811
    def boom(*_a, **_k):
        raise RuntimeError("store is down")

    monkeypatch.setattr(connector_snapshots, "stash_synced", boom)
    enable()
    world.edit("UPDATE transactions SET notes = 'new' WHERE id = 't-pay'")
    world.queue.clear()
    run = sync()
    assert run["summary"] == "1 pulled"
    assert row("ACME Corp")["memo"] == "new"


def test_keeping_rows_does_not_change_how_a_booking_dedups(world):  # noqa: F811
    """The kept rows carry no provider account, so no booking gains an ``emitter``: two Actual
    ids stay "possibly one emitter" and the veto holds, exactly as with no snapshot at all."""
    booked = [
        t
        for t in ledger_service.transactions.all()
        if any(e["source"] == SOURCE for e in t.get("external_refs") or [])
    ]
    refs = [e for t in booked for e in t["external_refs"] if e["source"] == SOURCE]
    assert refs and all("emitter" not in e for e in refs)
    assert all(connector_snapshots.emitter_for(SOURCE, e["ref"]) is None for e in refs)
    a = row("ACME Corp")
    other = next(t for t in booked if t["id"] != a["id"])
    assert matching._same_source_event_veto(a, other)

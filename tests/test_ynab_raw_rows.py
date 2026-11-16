"""What YNAB held is kept as the source of the ledger row it booked: the import and every sync read
stash the row as the plan export sent it under ``ynab``, the same key the one-way fetch tool uses,
and a fetch of the same transaction finds it unchanged. Every name and number is invented."""

from __future__ import annotations

import pytest

from backend.db.ynab_config_store import YnabConfigStore
from backend.services import connector_fetch, connector_snapshots
from backend.services import ledger as ledger_service
from tests import ynab_stub
from tests.test_ynab_sync_service import enable, row, sync, world  # noqa: F401

pytestmark = pytest.mark.flags(ynab_import_and_sync=True)

EVER = "2000-01-01"


def stored(ref: str) -> dict:
    found = connector_snapshots._snapshots.get("ynab", ref)
    assert found is not None, ref
    return found


def test_an_import_keeps_the_row_as_ynab_sent_it_and_the_drawer_shows_it(world):  # noqa: F811
    bistro = row("Bistro")
    ref = next(e["ref"] for e in bistro["external_refs"] if e["source"] == "ynab")
    snap = stored(ref)
    assert snap["referenced"] is True and snap["provider_account_id"] is None
    assert snap["raw"]["id"] == ref and snap["raw"]["subtransactions"] == []
    assert "payee_name" not in snap["raw"] and "payee_id" in snap["raw"]
    (entry,) = connector_snapshots.snapshots_for_transaction(bistro["id"])
    assert entry["unavailable"] is False and entry["provider_label"] == "YNAB"


def test_a_split_keeps_its_lines_as_ynab_sent_them(world):  # noqa: F811
    parents = [
        s for s in connector_snapshots._snapshots.by_source("ynab") if s["raw"]["subtransactions"]
    ]
    assert parents
    line = parents[0]["raw"]["subtransactions"][0]
    assert line["transaction_id"] == parents[0]["ref"] and line["id"]


def test_an_edit_in_ynab_updates_the_snapshot_on_the_next_sync(world):  # noqa: F811
    enable()
    sync()
    txn_id = ynab_stub.find(world.state, payee_name="Bistro")["id"]
    before = stored(txn_id)
    ynab_stub.ynab_edit(world.state, txn_id, memo="from ynab")
    sync()
    after = stored(txn_id)
    assert after["raw"]["memo"] == "from ynab" and after["referenced"] is True
    assert after["content_hash"] == before["content_hash"]


def test_the_fetch_tool_and_the_sync_agree_on_a_ref(world):  # noqa: F811
    """Same ref, same hash: neither flips the other's row, and the fetch reports no correction."""
    YnabConfigStore().update({"enabled": True})
    enable()
    sync()
    hashes = {s["ref"]: s["content_hash"] for s in connector_snapshots._snapshots.by_source("ynab")}
    fetched = connector_fetch.fetch("ynab", plan_id=ynab_stub.PLAN, since=EVER, include_booked=True)
    assert fetched["status"] == "ok" and fetched["changes"] == []
    after = {s["ref"]: s["content_hash"] for s in connector_snapshots._snapshots.by_source("ynab")}
    assert after == hashes
    sync()
    assert {
        s["ref"]: s["content_hash"] for s in connector_snapshots._snapshots.by_source("ynab")
    } == hashes


def test_a_fetched_row_gives_its_account_to_a_row_the_sync_kept_first(world):  # noqa: F811
    YnabConfigStore().update({"enabled": True})
    ref = next(e["ref"] for e in row("Bistro")["external_refs"] if e["source"] == "ynab")
    assert stored(ref)["provider_account_id"] is None
    connector_fetch.fetch("ynab", plan_id=ynab_stub.PLAN, since=EVER, include_booked=True)
    assert stored(ref)["provider_account_id"]


def test_a_failing_stash_does_not_fail_the_sync(world, monkeypatch):  # noqa: F811
    def boom(*_a, **_k):
        raise RuntimeError("store is down")

    monkeypatch.setattr(connector_snapshots, "stash_synced", boom)
    enable()
    ynab_stub.ynab_edit(
        world.state, ynab_stub.find(world.state, payee_name="Bistro")["id"], memo="still pulled"
    )
    sync()
    assert row("Bistro")["memo"] == "still pulled"


def test_keeping_rows_does_not_change_how_a_booking_dedups(world):  # noqa: F811
    refs = [
        e
        for t in ledger_service.transactions.all()
        for e in t.get("external_refs") or []
        if e["source"] == "ynab"
    ]
    assert refs and all("emitter" not in e for e in refs)
    assert all(connector_snapshots.emitter_for("ynab", e["ref"]) is None for e in refs)

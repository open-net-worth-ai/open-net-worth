"""Self-repair of lots a removed position left behind (synthetic data only)."""

from __future__ import annotations

import pytest

from backend.db import NotificationLogStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.transaction_store import TransactionStore
from backend.services.lot_integrity import repair_orphan_lots


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    return tmp_path


def _lot(position_id, *, original=10.0, remaining=10.0, txn_id=None, instrument_id="inst-x"):
    if txn_id:  # lots.txn_id is a real FK
        TransactionStore().upsert(
            {
                "id": txn_id,
                "date": "2026-06-08",
                "kind": "investment",
                "source": "manual",
                "description": "Share purchase",
            }
        )
    return LotStore().insert(
        {
            "id": f"lot-{position_id}-{original}-{remaining}-{txn_id}",
            "position_id": position_id,
            "instrument_id": instrument_id,
            "account_id": "acct",
            "acquired_at": "2026-06-08",
            "original_qty": original,
            "remaining_qty": remaining,
            "unit_cost": None,
            "cost_currency": "USD",
            "kind": "buy",
            "txn_id": txn_id,
        }
    )


def _ids():
    return {lot["id"] for lot in LotStore().all()}


def test_a_never_sold_lot_of_a_removed_position_is_repaired_and_reported(isolated):
    inst = InstrumentStore().create(
        {"symbol": "TSLX", "asset_class": "equity", "quote_currency": "USD"}
    )
    phantom = _lot("pos-gone", instrument_id=inst["id"])

    out = repair_orphan_lots()

    assert out == {"removed": [phantom["id"]], "left_alone": []}
    assert phantom["id"] not in _ids()
    notes = [n for n in NotificationLogStore().all() if n.get("trigger") == "data_repair"]
    assert len(notes) == 1 and "10 TSLX (bought 2026-06-08)" in notes[0]["summary"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"remaining": 4.0},  # partly sold: the rest may be real
        {"remaining": 0.0},  # fully sold: real history
        {"txn_id": "txn-1"},  # deleting it would also reverse a cash debit — not ours to guess
    ],
)
def test_anything_but_the_exact_signature_is_left_alone(isolated, kwargs):
    lot = _lot("pos-gone", **kwargs)
    out = repair_orphan_lots()
    assert out["removed"] == [] and out["left_alone"] == [lot["id"]]
    assert lot["id"] in _ids()


def test_a_lot_whose_removed_position_has_sale_history_is_left_alone(isolated):
    lot = _lot("pos-gone")
    RealizedEventStore().append(
        {
            "id": "rlz-1",
            "position_id": "pos-gone",
            "instrument_id": "inst-x",
            "quantity": 1,
            "disposed_at": "2026-06-20",
            "proceeds_per_unit": 1.0,
            "proceeds_currency": "USD",
        }
    )
    assert repair_orphan_lots()["left_alone"] == [lot["id"]]


def test_a_live_positions_lot_is_never_touched_and_a_second_run_does_nothing(isolated):
    live = PositionStore().insert(
        {"id": "pos-live", "instrument_id": "inst-x", "account_id": "acct"}
    )
    kept = _lot(live["id"])
    phantom = _lot("pos-gone")

    assert repair_orphan_lots()["removed"] == [phantom["id"]]
    assert repair_orphan_lots() == {"removed": [], "left_alone": []}
    assert _ids() == {kept["id"]}

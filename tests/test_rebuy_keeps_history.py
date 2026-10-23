"""A book split by a sell-out then rebuy keeps its history on the new position (synthetic data).

Live 2026-10-24: 60 shares were sold and 50 bought back minutes later at the same broker. The
rebuy is a real add (the duplicate-holding guard skips a closed record), so it minted a second
position — and the Position tab, scoped to the live position_id, showed "Realized P&L $0.00,
0 disposals" for a holding whose sale had booked a real gain.
"""

from __future__ import annotations

import pytest

from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    return tmp_path


def _instrument():
    return InstrumentStore().create(
        {
            "symbol": "TSTX",
            "asset_class": "equity",
            "price_source": "manual",
            "price_source_id": "TSTX",
            "quote_currency": "SGD",
            "currency": "SGD",
        }
    )


def _add(inst_id, qty, cost, acquired_at, account_name="Test Broker"):
    from backend.services import positions as ps

    res = ps.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst_id,
            "account_name": account_name,
            "account_type": "investment",
            "quantity": qty,
            "cost_basis": cost,
            "cost_currency": "SGD",
            "acquired_at": acquired_at,
        }
    )
    assert res.get("ok"), res
    return res["position_id"]


def _sell_all(position_id, qty, price, disposed_at):
    from backend.services import lots

    res = lots.apply_lot_change(
        {
            "type": "dispose",
            "position_id": position_id,
            "quantity": qty,
            "proceeds_per_unit": price,
            "proceeds_currency": "SGD",
            "disposed_at": disposed_at,
        }
    )
    assert not res.get("error"), res


def _dividend(position_id, paid_at):
    from backend.services import income

    pos = PositionStore().find_by_id(position_id)
    res = income.apply_income_change(
        {
            "type": "record",
            "position_id": position_id,
            "gross_amount": 12.0,
            "tax_withheld": 0.0,
            "currency": "SGD",
            "account_id": pos["account_id"],
            "paid_at": paid_at,
        }
    )
    assert res.get("ok"), res


def _legacy_rebuy(old_id, qty, cost, acquired_at):
    """The split book an add left before it reopened sold-out rows (2026-10-24): a fresh
    position beside the closed one, its buy lot on the new row."""
    from backend.services import lots
    from backend.services import positions as ps

    old = PositionStore().find_by_id(old_id)
    new = ps.add_position(
        instrument_id=old["instrument_id"], account_id=old["account_id"], cost_currency="SGD"
    )
    res = lots.apply_lot_change(
        {
            "type": "acquire",
            "position_id": new["id"],
            "quantity": qty,
            "unit_cost": cost,
            "cost_currency": "SGD",
            "acquired_at": acquired_at,
            "kind": "buy",
        }
    )
    assert not res.get("error"), res
    return new["id"]


def _sold_then_rebought():
    inst = _instrument()
    old = _add(inst["id"], 10, 100.0, "2026-06-01")
    _dividend(old, "2026-07-01")
    _sell_all(old, 10, 150.0, "2026-10-16")
    new = _legacy_rebuy(old, 8, 150.0, "2026-10-16")
    assert new != old  # the rebuy is its own position — the case this file covers
    return inst, old, new


def test_rebought_position_reads_the_sold_out_positions_sale(isolated):
    from backend.services import lots

    _, old, new = _sold_then_rebought()

    scoped = lots.realized_pnl(position_id=new, base="SGD")
    assert scoped["events"] == []  # the strict per-position read is unchanged

    history = lots.realized_pnl(position_id=new, base="SGD", with_predecessors=True)
    assert [ev["position_id"] for ev in history["events"]] == [old]
    assert history["realized_total_base"] == 500.0  # 10 × (150 − 100)


def test_rebought_position_reads_the_sold_out_positions_dividends(isolated):
    from backend.services import income

    _, old, new = _sold_then_rebought()

    assert income.income_summary(position_id=new, base="SGD")["events"] == []
    history = income.income_summary(position_id=new, base="SGD", with_predecessors=True)
    assert [ev["position_id"] for ev in history["events"]] == [old]
    assert history["total_base"] == 12.0


def test_a_sold_out_position_at_another_account_is_not_a_predecessor(isolated):
    from backend.services import lots

    inst = _instrument()
    elsewhere = _add(inst["id"], 10, 100.0, "2026-06-01", account_name="Other Broker")
    _sell_all(elsewhere, 10, 150.0, "2026-10-16")
    here = _add(inst["id"], 8, 150.0, "2026-10-16")

    history = lots.realized_pnl(position_id=here, base="SGD", with_predecessors=True)
    assert history["events"] == []


def test_an_unvested_row_is_never_a_predecessor(isolated):
    from backend.services.position_lineage import history_position_ids

    inst, old, new = _sold_then_rebought()
    locked = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": PositionStore().find_by_id(new)["account_id"],
            "quantity": 0,
            "vesting_state": "unvested",
        }
    )

    ids = history_position_ids(new)
    assert ids == [new, old]
    assert locked["id"] not in ids
    assert history_position_ids(locked["id"]) == [locked["id"]]

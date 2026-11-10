"""A buy add of a sold-out holding reopens its position (synthetic data only).

Live 2026-10-16 (and four holdings since June): Lucius booked a rebuy with add_positions after
the sell-out. The duplicate-holding guard skips a closed record, so every such add minted a
second position — while the Add-lot form, through `_ensure_acquire_position`, already reopened
the sold-out one. One rule now: both paths reopen it.
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
            "symbol": "TSTY",
            "asset_class": "equity",
            "price_source": "manual",
            "price_source_id": "TSTY",
            "quote_currency": "SGD",
            "currency": "SGD",
        }
    )


def _add(inst_id, qty, cost, acquired_at, account_name="Test Broker"):
    from backend.services import positions as ps

    return ps.apply_position_change(
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


def _sell_all(position_id, qty):
    from backend.services import lots

    res = lots.apply_lot_change(
        {
            "type": "dispose",
            "position_id": position_id,
            "quantity": qty,
            "proceeds_per_unit": 150.0,
            "proceeds_currency": "SGD",
            "disposed_at": "2026-10-16",
        }
    )
    assert not res.get("error"), res


def _sold_out():
    inst = _instrument()
    first = _add(inst["id"], 10, 100.0, "2026-06-01")
    assert first.get("ok"), first
    _sell_all(first["position_id"], 10)
    return inst, first["position_id"]


def test_a_rebuy_add_reopens_the_sold_out_position(isolated):
    from backend.services import lots

    inst, old = _sold_out()

    res = _add(inst["id"], 8, 150.0, "2026-10-16")

    assert res.get("ok"), res
    assert res["position_id"] == old
    assert res.get("reopened") is True
    rows = PositionStore().query_positions(instrument_id=inst["id"])
    assert [p["id"] for p in rows] == [old]
    assert float(rows[0]["quantity"]) == 8.0
    sale = lots.realized_pnl(position_id=old, base="SGD")
    assert sale["realized_total_base"] == 500.0  # the sale stays on the holding


def test_a_rebuy_at_another_account_is_a_new_position(isolated):
    inst, old = _sold_out()

    res = _add(inst["id"], 8, 150.0, "2026-10-16", account_name="Other Broker")

    assert res.get("ok"), res
    assert res["position_id"] != old
    assert "reopened" not in res


def test_a_failed_lot_never_deletes_the_reopened_position(isolated, monkeypatch):
    from backend.services import lots

    inst, old = _sold_out()
    monkeypatch.setattr(lots, "apply_lot_change", lambda change: {"error": "refused"})

    res = _add(inst["id"], 8, 150.0, "2026-10-16")

    assert res == {"error": "refused"}
    assert PositionStore().find_by_id(old) is not None  # its sale history is still there


def test_the_add_lot_form_and_add_positions_reopen_the_same_row(isolated):
    from backend.services import lots
    from backend.services.positions import find_sold_out_liquid_position

    inst, old = _sold_out()
    acct = PositionStore().find_by_id(old)["account_id"]

    assert find_sold_out_liquid_position(inst["id"], acct)["id"] == old
    assert (
        lots._ensure_acquire_position({"instrument_id": inst["id"], "account_id": acct})["id"]
        == old
    )

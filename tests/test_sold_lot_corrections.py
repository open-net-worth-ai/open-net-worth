# ruff: noqa: F811  (the shared `isolated` fixture is imported, then requested by name)
"""Correcting a lot a sale has already used (synthetic data only).

Found 2026-10-13: an opening-balance lot whose shares had all been sold could not be given its
real purchase date — sold lots were frozen. A correction now keeps everything the sale decided
(which lots, how many units, price, cash) and recomputes only that lot's lines on the sale.
"""

from __future__ import annotations

import pytest

from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.transaction_store import TransactionStore
from backend.services import lots as lots_service
from backend.services import position_figures
from tests.test_position_figures import _account, _instrument, isolated  # noqa: F401


def _buy(inst, acct, qty, cost, when, **extra) -> str:
    return lots_service.apply_lot_change(
        {
            "type": "acquire",
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": qty,
            "unit_cost": cost,
            "acquired_at": when,
            **extra,
        }
    )["lot_id"]


def _sell(pid, qty, price, when, **extra) -> dict:
    return lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pid,
            "quantity": qty,
            "proceeds_per_unit": price,
            "proceeds_currency": "SGD",
            "disposed_at": when,
            **extra,
        }
    )


def _amend(lot_id, **fields) -> dict:
    return lots_service.apply_lot_change({"type": "amend", "lot_id": lot_id, **fields})


def _event(event_id) -> dict:
    return RealizedEventStore().find_by_id(event_id)


@pytest.fixture
def sold_book(isolated):
    """An undated opening lot of 27 + a dated buy of 10, then one FIFO sale of 30."""
    inst, acct = _instrument(), _account()
    opening = _buy(inst, acct, 27, 100.0, "", kind="opening")
    buy = _buy(inst, acct, 10, 120.0, "2026-06-23")
    pid = LotStore().find_by_id(opening)["position_id"]
    sale = _sell(pid, 30, 150.0, "2026-10-12")
    return {"inst": inst, "acct": acct, "pid": pid, "opening": opening, "buy": buy, "sale": sale}


def test_dating_a_sold_lot_re_dates_the_sale_and_moves_no_gain(sold_book):
    before = _event(sold_book["sale"]["event_id"])
    assert {c["term"] for c in before["consumptions"]} == {"unknown", "short"}

    out = _amend(sold_book["opening"], acquired_at="2023-03-01")

    assert out["ok"] is True and len(out["sales_updated"]) == 1
    after = _event(sold_book["sale"]["event_id"])
    line = next(c for c in after["consumptions"] if c["lot_id"] == sold_book["opening"])
    assert (line["acquired_at"], line["term"]) == ("2023-03-01", "long")
    other = next(c for c in after["consumptions"] if c["lot_id"] == sold_book["buy"])
    assert other == next(c for c in before["consumptions"] if c["lot_id"] == sold_book["buy"])
    for key in ("realized_gain", "realized_gain_base", "quantity", "proceeds_per_unit"):
        assert after[key] == before[key], key
    assert LotStore().find_by_id(sold_book["opening"])["acquired_at"] == "2023-03-01"
    assert position_figures.check_parity() == []


def test_correcting_a_sold_lots_cost_recomputes_its_gain(sold_book):
    out = _amend(sold_book["opening"], unit_cost=90.0)

    after = _event(sold_book["sale"]["event_id"])
    line = next(c for c in after["consumptions"] if c["lot_id"] == sold_book["opening"])
    assert line["realized_gain"] == pytest.approx(27 * (150 - 90))
    assert after["realized_gain"] == pytest.approx(27 * 60 + 3 * 30)  # 3 more came from the buy
    summary = out["sales_updated"][0]
    assert summary["realized_gain_before"] == pytest.approx(27 * 50 + 3 * 30)
    assert summary["realized_gain_after"] == pytest.approx(27 * 60 + 3 * 30)


def test_a_cost_correction_converts_at_the_sales_own_rate_not_todays(isolated, monkeypatch):
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.3})
    inst, acct = _instrument(), _account()
    lot = _buy(inst, acct, 10, 100.0, "2025-01-02", cost_currency="USD")
    pid = LotStore().find_by_id(lot)["position_id"]
    sale = _sell(pid, 10, 150.0, "2026-10-12", proceeds_currency="USD")
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.5})

    _amend(lot, unit_cost=110.0)

    after = _event(sale["event_id"])
    assert after["realized_gain"] == pytest.approx(400.0)  # 10 × (150 − 110), in USD
    # Proceeds at the sale's rate (1.3) minus the corrected basis at its new stamp (1.5): the
    # sale-date half of the per-leg gain is never re-priced at today's rate.
    assert after["realized_gain_base"] == pytest.approx(1500 * 1.3 - 1100 * 1.5)


def test_a_sold_lot_can_hold_more_but_never_fewer_than_were_sold(sold_book):
    pid = sold_book["pid"]
    grown = _amend(sold_book["opening"], quantity=30)
    assert grown["ok"] is True
    lot = LotStore().find_by_id(sold_book["opening"])
    assert (lot["original_qty"], lot["remaining_qty"]) == (30, 3)
    assert PositionStore().find_by_id(pid)["quantity"] == pytest.approx(3 + 7)

    shrunk = _amend(sold_book["opening"], quantity=20)

    assert "already been sold" in shrunk["error"]
    assert LotStore().find_by_id(sold_book["opening"])["original_qty"] == 30
    assert position_figures.check_parity() == []


def test_the_sales_cash_is_never_touched(isolated):
    inst, acct, cash = _instrument(), _account(), _account("Cash")
    lot = _buy(inst, acct, 5, 100.0, "2025-01-02")
    pid = LotStore().find_by_id(lot)["position_id"]
    sale = _sell(pid, 5, 150.0, "2026-10-12", proceeds_account_id=cash["id"])
    txn_before = TransactionStore().find_by_id(sale["cash_leg_id"])

    _amend(lot, unit_cost=80.0, acquired_at="2024-01-02")

    after = _event(sale["event_id"])
    assert after["txn_id"] == sale["cash_leg_id"]
    assert TransactionStore().find_by_id(sale["cash_leg_id"]) == txn_before


def test_undoing_the_sale_after_a_correction_gives_the_units_back(sold_book):
    _amend(sold_book["opening"], quantity=30, acquired_at="2023-03-01")

    lots_service.apply_lot_change(
        {"type": "dispose_undo", "event_id": sold_book["sale"]["event_id"]}
    )

    assert LotStore().find_by_id(sold_book["opening"])["remaining_qty"] == 30
    assert PositionStore().find_by_id(sold_book["pid"])["quantity"] == pytest.approx(40)
    assert position_figures.check_parity() == []


def test_a_note_edit_keeps_the_purchase_transaction(isolated):
    """Editing a funded lot's note used to delete and re-book its purchase cash, dropping
    the transaction's category, cleared and reconciled state."""
    inst, acct, cash = _instrument(), _account(), _account("Cash")
    lot_id = lots_service.apply_lot_change(
        {
            "type": "acquire",
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": 2,
            "unit_cost": 50.0,
            "acquired_at": "2025-01-02",
            "funding_account_id": cash["id"],
        }
    )["lot_id"]
    txn_id = LotStore().find_by_id(lot_id)["txn_id"]
    lot = LotStore().find_by_id(lot_id)

    # The edit form resends every field, unchanged, beside the note.
    _amend(
        lot_id,
        note="from my bonus",
        quantity=lot["original_qty"],
        unit_cost=lot["unit_cost"],
        fees=lot.get("fees") or 0,
        acquired_at=lot["acquired_at"],
        kind=lot["kind"],
        cost_currency=lot.get("cost_currency"),
    )

    assert LotStore().find_by_id(lot_id)["txn_id"] == txn_id
    assert TransactionStore().find_by_id(txn_id) is not None

    _amend(lot_id, unit_cost=60.0)  # a real cost change edits the same row

    assert LotStore().find_by_id(lot_id)["txn_id"] == txn_id
    assert TransactionStore().find_by_id(txn_id)["from_amount"] == 120.0


# ── Review findings (2026-10-13) ─────────────────────────────────────────────────


def _resend_everything(lot_id: str, **override) -> dict:
    """What the Edit lot form sends: every field, unchanged unless overridden."""
    lot = LotStore().find_by_id(lot_id)
    body = {
        "quantity": lot["original_qty"],
        "unit_cost": lot["unit_cost"],
        "fees": lot.get("fees") or 0,
        "kind": lot["kind"],
        "note": lot.get("note") or "",
        **override,
    }
    return _amend(lot_id, **body)


def test_a_note_save_leaves_a_foreign_currency_sale_byte_identical(isolated, monkeypatch):
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.3})
    inst, acct = _instrument(), _account()
    lot_id = _buy(inst, acct, 10, 100.0, "2025-01-02", cost_currency="USD")
    pid = LotStore().find_by_id(lot_id)["position_id"]
    sale = _sell(pid, 5, 150.0, "2026-10-12", proceeds_currency="USD")
    event_before = _event(sale["event_id"])
    stamp_before = LotStore().find_by_id(lot_id)["fx_rate"]
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.5})

    out = _resend_everything(lot_id, note="from my 2025 statement")

    assert out["ok"] is True
    assert _event(sale["event_id"]) == event_before
    lot = LotStore().find_by_id(lot_id)
    assert (lot["note"], lot["fx_rate"]) == ("from my 2025 statement", stamp_before)


def test_an_open_lots_rate_stamp_survives_a_note_save(isolated, monkeypatch):
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.3})
    inst, acct = _instrument(), _account()
    lot_id = _buy(inst, acct, 10, 100.0, "2025-01-02", cost_currency="USD")
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.5})

    _resend_everything(lot_id, note="x")

    assert LotStore().find_by_id(lot_id)["fx_rate"] == pytest.approx(1.3)


def test_a_new_quantity_on_a_lot_with_fees_recomputes_its_sale(isolated):
    inst, acct = _instrument(), _account()
    lot_id = _buy(inst, acct, 10, 100.0, "2025-01-02", fees=50.0)  # 105 per unit with fees
    pid = LotStore().find_by_id(lot_id)["position_id"]
    sale = _sell(pid, 5, 150.0, "2026-10-12")
    assert _event(sale["event_id"])["realized_gain"] == pytest.approx(5 * 45)

    _amend(lot_id, quantity=20)  # 102.50 per unit now

    line = _event(sale["event_id"])["consumptions"][0]
    assert line["unit_cost"] == pytest.approx(102.5)
    assert _event(sale["event_id"])["realized_gain"] == pytest.approx(5 * 47.5)


def test_deduct_from_on_a_sold_lot_books_its_purchase(isolated):
    inst, acct, cash = _instrument(), _account(), _account("Cash")
    lot_id = _buy(inst, acct, 4, 50.0, "2025-01-02")
    pid = LotStore().find_by_id(lot_id)["position_id"]
    _sell(pid, 4, 60.0, "2026-10-12")
    assert LotStore().find_by_id(lot_id).get("txn_id") is None

    _amend(lot_id, funding_account_id=cash["id"])

    txn = TransactionStore().find_by_id(LotStore().find_by_id(lot_id)["txn_id"])
    assert txn["from_account_id"] == cash["id"]
    assert float(txn["from_amount"]) == pytest.approx(200.0)


def test_a_note_or_kind_edit_on_a_sold_lot_reports_no_sales(sold_book):
    """Re-review: a note save listed every sale as 're-dated — gain unchanged'."""
    assert _amend(sold_book["opening"], note="from my old broker")["sales_updated"] == []
    assert _amend(sold_book["opening"], kind="buy")["sales_updated"] == []
    dated = _amend(sold_book["opening"], acquired_at="2023-03-01")
    assert [s["event_id"] for s in dated["sales_updated"]] == [sold_book["sale"]["event_id"]]

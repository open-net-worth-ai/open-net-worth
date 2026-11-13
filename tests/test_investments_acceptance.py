"""
Investments engine acceptance regression suite — IE-* lock (I4).

One test per criterion in docs/qa/acceptance/investments-engine.md. All 13 IDs covered.
Pure-service layer tests — no routers, no LLM calls, no network. Every test is isolated
(tmp_path / monkeypatched stores) so live backend/*.json data is never touched.

Run: .venv/bin/python3 -m pytest -q tests/test_investments_acceptance.py
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

# ── shared helpers (mirrors test_lots_service.py) ─────────────────────────────

_USD = {"USD": 1.0}


def _lot(lot_id, qty, unit_cost, acquired_at, created_at=None, ccy="USD", fees=0.0):
    return {
        "id": lot_id,
        "position_id": "pos-1",
        "instrument_id": "inst-1",
        "account_id": "acc-1",
        "acquired_at": acquired_at,
        "original_qty": qty,
        "remaining_qty": qty,
        "unit_cost": unit_cost,
        "cost_currency": ccy,
        "fees": fees,
        "kind": "buy",
        "created_at": created_at or f"{acquired_at}T00:00:00Z",
    }


_POS = {
    "id": "pos-1",
    "instrument_id": "inst-1",
    "account_id": "acc-1",
    "cost_currency": "USD",
}


def _dispose(qty, price, method="fifo", lot_selection=None, disposed_at="2026-06-01", **kw):
    d = {
        "quantity": qty,
        "proceeds_per_unit": price,
        "proceeds_currency": "USD",
        "method": method,
        "disposed_at": disposed_at,
    }
    if lot_selection is not None:
        d["lot_selection"] = lot_selection
    d.update(kw)
    return d


# ── fake stores for pure-compute tests ───────────────────────────────────────


class _FakeLotStore:
    def __init__(self, lots):
        self._lots = lots

    def open_lots_for_position(self, position_id, *, conn=None):
        return [
            lot
            for lot in self._lots
            if lot.get("position_id") == position_id and float(lot.get("remaining_qty") or 0) > 0
        ]

    def lots_for_position(self, position_id, include_closed=True, *, conn=None):
        return [lot for lot in self._lots if lot.get("position_id") == position_id]


class _FakePos:
    def __init__(self):
        self.captured: dict = {}

    def touch(self, pid, changes, *, conn=None):
        self.captured.update(changes)
        return changes


# ── real-store write-path helper ─────────────────────────────────────────────


def _wire_write_stores(tmp_path, monkeypatch):
    """Wire LotStore + PositionStore + RealizedEventStore to tmp_path."""
    from backend.services import lots as svc

    monkeypatch.setattr(svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")


# ══════════════════════════════════════════════════════════════════════════════
# IE-VALUATION
# ══════════════════════════════════════════════════════════════════════════════


def test_IE_VALUATION():
    """IE-VALUATION: GIVEN a holding qty=10, price=100 in USD (quote_currency), cost in SGD
    (cost_currency), base SGD, FX USD→SGD=1.35
    WHEN market value is computed via the production wiring (_position_block)
    THEN it equals qty × price × qpu converted via the QUOTE currency = S$1350
    (if cost_currency were used instead: to_base(1000, SGD, SGD)=S$1000 — discriminating)."""
    from backend.services.asset_detail import _position_block

    # quote_currency=USD (market price), currency/cost_currency=SGD (acquisition cost)
    # FX pivot: SGD=1.0, USD=1.35 → 1 USD = 1.35 SGD
    rates = {"SGD": 1.0, "USD": 1.35}
    inst = {"id": "INST-VALUATION", "quote_currency": "USD"}
    row = {
        "instrument_id": "INST-VALUATION",
        "bucket": "liquid",
        "quantity": 10.0,
        "price": 100.0,
        "quote_per_unit": 1.0,
        "quote_currency": "USD",  # market value must use THIS rate
        "currency": "SGD",  # cost_currency (deliberately different)
        "cost_basis": 80.0,
        "position_id": None,  # skip _lot_rows
    }

    block = _position_block(inst, [row], fx=rates, base="SGD")

    assert block is not None
    # Correct:  to_base(10×100×1, "USD", "SGD") = 1000 × 1.35 = S$1350
    # Wrong:    to_base(1000, "SGD", "SGD")     = 1000 × 1.0  = S$1000  ← caught if bug exists
    assert block["market_value_base"] == pytest.approx(1350.0), (
        f"_position_block must use quote_currency (USD→SGD=1.35×1000=1350), "
        f"not cost_currency (SGD→SGD=1.0×1000=1000); got {block['market_value_base']}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# IE-FIFO
# ══════════════════════════════════════════════════════════════════════════════


def test_IE_FIFO():
    """IE-FIFO: GIVEN lot A (qty 5 @ 100, acquired earlier) then lot B (qty 5 @ 120)
    WHEN 7 units are disposed
    THEN FIFO consumes A(5) then B(2), ordered by (acquired_at, created_at)."""
    from backend.services.lots import select_lots_fifo

    lots = [
        _lot("L1", 5, 100.0, "2026-01-01"),  # earlier
        _lot("L2", 5, 120.0, "2026-03-01"),  # later
    ]
    plan = select_lots_fifo(lots, 7)

    assert [(lot["id"], qty) for lot, qty in plan] == [("L1", 5.0), ("L2", 2.0)], (
        "FIFO must exhaust L1 (earlier) before touching L2"
    )


# ══════════════════════════════════════════════════════════════════════════════
# IE-REALIZED-GAIN
# ══════════════════════════════════════════════════════════════════════════════


def test_IE_REALIZED_GAIN():
    """IE-REALIZED-GAIN: GIVEN a disposal consuming lots with known cost
    WHEN realized gain is computed
    THEN gain = proceeds − cost, with the cost converted as eff_cost × qty in one step
    (multiply-then-convert, no per-unit rounding); realized_gain_base = to_base(gain)."""
    from backend.services.lots import compute_disposal

    # Same-currency baseline: 10 @ 100 USD → sell 10 @ 130 USD, gain = 300
    lots = [_lot("L1", 10, 100.0, "2026-01-01")]
    draft = compute_disposal(_POS, lots, _dispose(10, 130.0), rates=_USD, base="USD")
    assert draft["realized_gain"] == pytest.approx(300.0)
    assert draft["cost_basis_known"] is True
    assert draft["realized_gain_base"] == pytest.approx(300.0)  # base == proceeds_ccy

    # Cross-currency: cost in SGD (fractional unit_cost=100.33), proceeds in USD.
    # rates: 1 SGD = 0.74 USD  →  {"USD": 1.0, "SGD": 0.74}
    # multiply-then-convert (correct):
    #   100.33 × 10 = 1003.3 SGD → to_base(1003.3, SGD, USD) = round(1003.3×0.74, 2) = 742.44 USD
    #   gain = 1300 − 742.44 = 557.56 USD
    # per-unit rounding (wrong — what a naïve implementation would do):
    #   to_base(100.33, SGD, USD) = round(74.2442, 2) = 74.24/unit × 10 = 742.40
    #   gain_wrong = 1300 − 742.40 = 557.60 (off by 0.04 — diverges visibly with fractional cost)
    pos_sgd = {**_POS, "cost_currency": "SGD"}
    lots_sgd = [_lot("L1", 10, 100.33, "2026-01-01", ccy="SGD")]
    rates_cross = {"USD": 1.0, "SGD": 0.74}
    disposal_usd = {
        "quantity": 10,
        "proceeds_per_unit": 130.0,
        "proceeds_currency": "USD",
        "method": "fifo",
        "disposed_at": "2026-06-01",
    }
    draft2 = compute_disposal(pos_sgd, lots_sgd, disposal_usd, rates=rates_cross, base="USD")
    assert draft2["realized_gain"] == pytest.approx(557.56), (
        "multiply-then-convert: 1003.3 SGD → 742.44 USD; gain=557.56; "
        "per-unit rounding would give 557.60 (0.04 off)"
    )
    assert draft2["realized_gain_base"] == pytest.approx(557.56)  # base=USD=proceeds_ccy


# ══════════════════════════════════════════════════════════════════════════════
# IE-UNKNOWN-BASIS
# ══════════════════════════════════════════════════════════════════════════════


def test_IE_UNKNOWN_BASIS():
    """IE-UNKNOWN-BASIS: GIVEN any consumed lot has unit_cost=None
    WHEN the disposal is computed
    THEN the disposal's realized_gain is None (never a fabricated 0)."""
    from backend.services.lots import compute_disposal

    lots = [_lot("L1", 10, None, "2026-01-01")]  # unknown cost basis
    draft = compute_disposal(
        _POS,
        lots,
        _dispose(10, 130.0),
        rates=_USD,
        base="USD",
    )

    assert draft["realized_gain"] is None, (
        f"unknown basis must yield realized_gain=None, got {draft['realized_gain']}"
    )
    assert draft["cost_basis_known"] is False


# ══════════════════════════════════════════════════════════════════════════════
# IE-ZERO-PROCEEDS-REFUSED
# ══════════════════════════════════════════════════════════════════════════════


def test_IE_ZERO_PROCEEDS_REFUSED():
    """IE-ZERO-PROCEEDS-REFUSED: GIVEN a TAXABLE disposal with fee-adjusted net proceeds/unit <= 0
    WHEN it is attempted
    THEN it is REFUSED (InvalidLotSelection); a transfer-out (is_taxable=False) instead
    books realized_gain=None and is allowed."""
    from backend.core.errors import InvalidLotSelection
    from backend.services.lots import compute_disposal

    lots = [_lot("L1", 10, 100.0, "2026-01-01")]

    # Taxable, zero proceeds → refused
    with pytest.raises(InvalidLotSelection):
        compute_disposal(_POS, lots, _dispose(10, 0.0), rates=_USD, base="USD")

    # Taxable, fee-adjusted net proceeds exactly zero → also refused (1.0 - 10/10 = 0)
    with pytest.raises(InvalidLotSelection):
        compute_disposal(_POS, lots, _dispose(10, 1.0, disposal_fees=10.0), rates=_USD, base="USD")

    # Transfer-out (is_taxable=False), zero proceeds → allowed, realized_gain=None
    draft = compute_disposal(
        _POS, lots, _dispose(10, 0.0, is_taxable=False), rates=_USD, base="USD"
    )
    assert draft["realized_gain"] is None
    assert draft["is_taxable"] is False


# ══════════════════════════════════════════════════════════════════════════════
# IE-DOUBLE-BOOK
# ══════════════════════════════════════════════════════════════════════════════


def test_IE_DOUBLE_BOOK(tmp_path, monkeypatch):
    """IE-DOUBLE-BOOK: GIVEN a buy identical to a fully-open lot (same kind=buy,
    acquired_at, qty, unit_cost), or a disposal matching an existing realized event
    (same date, qty, proceeds/unit, currency)
    WHEN applied without allow_duplicate
    THEN it is REFUSED as a double-book; a retry with the same idempotency_key is a
    no-op returning the existing id."""
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore
    from backend.db.realized_event_store import RealizedEventStore
    from backend.services import lots as svc

    _wire_write_stores(tmp_path, monkeypatch)

    pos = PositionStore().create(
        {
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "quantity": 10.0,
            "cost_basis": 100.0,
            "cost_currency": "USD",
            "bucket": "liquid",
        }
    )

    # ── Acquire double-book ───────────────────────────────────────────────────
    first_acquire = svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
            "kind": "buy",
        }
    )
    assert first_acquire["ok"]

    dup_acquire = svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
            "kind": "buy",  # identical → double-book
        }
    )
    assert "error" in dup_acquire, "identical acquire must be refused as a double-book"
    assert "existing_lot_id" in dup_acquire
    assert len(LotStore().lots_for_position(pos["id"])) == 1  # still only one lot

    # ── Dispose double-book ───────────────────────────────────────────────────
    first_dispose = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130.0,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
        }
    )
    assert first_dispose["ok"]

    dup_dispose = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130.0,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",  # same signature → double-book
        }
    )
    assert "error" in dup_dispose, "identical disposal must be refused as a double-book"
    assert "existing_event_id" in dup_dispose
    assert len(RealizedEventStore().query_events(position_id=pos["id"])) == 1

    # ── Idempotency key: second call is a no-op returning the same id ─────────
    idem1 = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 3,
            "proceeds_per_unit": 140.0,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-02",
            "idempotency_key": "trade-abc",
        }
    )
    assert idem1["ok"]
    event_id = idem1["event_id"]

    idem_retry = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 3,
            "proceeds_per_unit": 140.0,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-02",
            "idempotency_key": "trade-abc",  # same key → no-op
        }
    )
    assert idem_retry["ok"]
    assert idem_retry["event_id"] == event_id, (
        "retry with same idempotency_key must return the existing event id"
    )


# ══════════════════════════════════════════════════════════════════════════════
# IE-DISPOSE-UNDO
# ══════════════════════════════════════════════════════════════════════════════


def test_IE_DISPOSE_UNDO(tmp_path, monkeypatch, db_isolated):
    """IE-DISPOSE-UNDO: GIVEN a disposal event
    WHEN dispose_undo runs
    THEN it pre-validates every consumed lot still exists, re-opens them (capped at
    original_qty), soft-deletes the cash-proceeds leg if any, deletes the realized event,
    and resyncs — REFUSED (no mutation) if a consumed lot is gone."""
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore
    from backend.db.realized_event_store import RealizedEventStore
    from backend.services import lots as svc

    _wire_write_stores(tmp_path, monkeypatch)

    pos = PositionStore().create(
        {
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "quantity": 10.0,
            "cost_basis": 100.0,
            "cost_currency": "USD",
            "bucket": "liquid",
        }
    )
    LotStore().create(
        {
            "position_id": pos["id"],
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "acquired_at": "2026-01-01",
            "original_qty": 10.0,
            "remaining_qty": 10.0,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "kind": "buy",
        }
    )

    # Dispose 4 units
    res = svc.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 4,
            "proceeds_per_unit": 130.0,
            "proceeds_currency": "USD",
            "method": "fifo",
            "disposed_at": "2026-06-01",
        }
    )
    assert res["ok"]
    event_id = res["event_id"]
    assert PositionStore().find_by_id(pos["id"])["quantity"] == pytest.approx(6.0)
    assert RealizedEventStore().find_by_id(event_id) is not None

    # Undo: re-opens the lot, restores position qty, deletes the event
    undo = svc.apply_lot_change({"type": "dispose_undo", "event_id": event_id})
    assert undo["ok"]
    assert undo["event_id"] == event_id
    assert PositionStore().find_by_id(pos["id"])["quantity"] == pytest.approx(10.0), (
        "dispose_undo must restore the position to its pre-disposal quantity"
    )
    assert RealizedEventStore().find_by_id(event_id) is None, (
        "dispose_undo must delete the realized event"
    )

    # ── cash-leg sub-criterion: _undo_dispose soft-deletes the txn_id leg ───────
    # transactions is DB-backed now (plan 3 task 4) — db_isolated (above) covers it.
    # Silence the audit so bulk_delete_transactions doesn't try to write to the live
    # txn_audit.json.
    from backend.db.transaction_store import TransactionStore
    from backend.services import audit as audit_svc
    from backend.services import ledger as ledger_svc
    from tests.conftest import seed_stub_accounts

    cash_ts = TransactionStore()
    # to_account_id is a real FK now — "acc-1" was only ever a Position/Lot label
    # here, never a created Account row. The row has the shape a sale books
    # (`_book_deposit_proceeds`): the deposit sits on the TO leg. A row whose money leaves on
    # the from leg is no longer the sale's own deposit, and the undo keeps it.
    seed_stub_accounts([{"to_account_id": "acc-1"}])
    cash_txn = cash_ts.append(
        {
            "date": "2026-06-01",
            "kind": "investment",
            "description": "Sale proceeds",
            "to_account_id": "acc-1",
            "to_amount": 520.0,
            "to_currency": "USD",
        }
    )
    monkeypatch.setattr(ledger_svc, "transactions", cash_ts)
    monkeypatch.setattr(audit_svc, "record_txn_events", lambda *a, **kw: None)

    # A fresh lot for this sub-test (so the lots-exist validation passes)
    cash_lot = LotStore().create(
        {
            "position_id": pos["id"],
            "instrument_id": "inst-1",
            "account_id": "acc-1",
            "acquired_at": "2026-02-01",
            "original_qty": 5.0,
            "remaining_qty": 5.0,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "kind": "buy",
        }
    )

    # Append a realized event that links to the cash-proceeds transaction
    ev_cash = RealizedEventStore().append(
        {
            "position_id": pos["id"],
            "instrument_id": "inst-1",
            "quantity": 3.0,
            "txn_id": cash_txn["id"],
            "consumptions": [{"lot_id": cash_lot["id"], "qty": 3.0}],
        }
    )

    undo_cash = svc.apply_lot_change({"type": "dispose_undo", "event_id": ev_cash["id"]})
    assert undo_cash["ok"]
    assert undo_cash.get("txn_id") == cash_txn["id"], (
        "dispose_undo must report the voided txn_id in its return value"
    )

    deleted_txn = cash_ts.find_by_id(cash_txn["id"])
    assert deleted_txn is not None, "soft-delete keeps the row in the store for the audit trail"
    assert deleted_txn.get("deleted") is True, (
        "dispose_undo must soft-delete the cash-proceeds transaction (deleted=True)"
    )

    # ── REFUSED when a consumed lot no longer exists (no mutation before the refusal)
    ev_ghost = RealizedEventStore().append(
        {
            "position_id": pos["id"],
            "instrument_id": "inst-1",
            "quantity": 2.0,
            "consumptions": [{"lot_id": "ghost-lot", "qty": 2.0}],
        }
    )
    refused = svc.apply_lot_change({"type": "dispose_undo", "event_id": ev_ghost["id"]})
    assert "error" in refused and "no longer exist" in refused["error"]
    assert RealizedEventStore().find_by_id(ev_ghost["id"]) is not None, (
        "dispose_undo must NOT delete the event when the pre-validate step fails"
    )


# ══════════════════════════════════════════════════════════════════════════════
# IE-RESYNC
# ══════════════════════════════════════════════════════════════════════════════


def test_IE_RESYNC(monkeypatch):
    """IE-RESYNC: GIVEN lots change on a position
    WHEN its figures are re-derived (position_figures — the one writer)
    THEN position.quantity = Σ remaining_qty and cost_basis = remaining-qty-weighted
    average of cost-known lots' effective_unit_cost (None if any open lot's cost is
    unknown); cost_currency synced only when the open lots agree on exactly one currency."""
    from backend.services import position_figures as pf

    pos = {"id": "pos-1", "instrument_id": "inst-1"}
    # Two lots: L1 partially sold (6 remaining), L2 fully open (10 remaining)
    lots = [
        {**_lot("L1", 10, 100.0, "2026-01-01"), "remaining_qty": 6},
        _lot("L2", 10, 120.0, "2026-03-01"),
    ]
    monkeypatch.setattr(pf, "LotStore", lambda: _FakeLotStore(lots))
    agg = pf._owned_figures(pos, conn=None)

    assert agg["quantity"] == pytest.approx(16.0), "quantity must equal Σ remaining_qty (6 + 10)"
    # basis-weighted avg: (6 × 100 + 10 × 120) / 16 = (600 + 1200) / 16 = 112.5
    assert agg["cost_basis"] == pytest.approx(112.5)
    assert agg.get("cost_currency") == "USD", "single-currency open lots must sync cost_currency"

    # cost_basis = None when all open lots have unknown cost
    lots_unknown = [_lot("L1", 10, None, "2026-01-01")]
    monkeypatch.setattr(pf, "LotStore", lambda: _FakeLotStore(lots_unknown))

    agg2 = pf._owned_figures(pos, conn=None)
    assert agg2["quantity"] == pytest.approx(10.0)
    assert agg2["cost_basis"] is None, "no cost-known qty → cost_basis must be None, not 0"


# ══════════════════════════════════════════════════════════════════════════════
# IE-UNREALIZED-PNL
# ══════════════════════════════════════════════════════════════════════════════


def test_IE_UNREALIZED_PNL():
    """IE-UNREALIZED-PNL: GIVEN a held position where quote_currency ≠ cost_currency
    WHEN unrealized P&L is computed via the production wiring (_position_block)
    THEN market value uses the QUOTE rate and cost basis uses the COST rate independently —
    unrealized_pl_base = S$1350 (market, USD FX) − S$800 (cost, SGD FX) = S$550."""
    from backend.services.asset_detail import _position_block

    # quote_currency=USD (market price in USD), currency/cost_currency=SGD (cost booked in SGD)
    # FX pivot: SGD=1.0, USD=1.35 → 1 USD = 1.35 SGD
    # market: qty=10, price=100 USD → 1000 USD → to_base(1000, USD, SGD) = S$1350
    # cost:   cost_basis=80/unit SGD, qty=10 → 800 SGD → to_base(800, SGD, SGD) = S$800
    # unrealized = S$1350 − S$800 = S$550
    rates = {"SGD": 1.0, "USD": 1.35}
    inst = {"id": "INST-PLTEST", "quote_currency": "USD"}
    row = {
        "instrument_id": "INST-PLTEST",
        "bucket": "liquid",
        "quantity": 10.0,
        "price": 100.0,
        "quote_per_unit": 1.0,
        "quote_currency": "USD",  # market-value FX path
        "currency": "SGD",  # cost-basis FX path (independent leg)
        "cost_basis": 80.0,  # per-unit cost in SGD
        "position_id": None,  # skip _lot_rows
    }

    block = _position_block(inst, [row], fx=rates, base="SGD")

    assert block is not None
    assert block["market_value_base"] == pytest.approx(1350.0), (
        "market value must use quote_currency (USD) rate, not cost_currency (SGD)"
    )
    assert block["unrealized_pl_base"] == pytest.approx(550.0), (
        "unrealized P&L = S$1350 (market, USD FX) − S$800 (cost, SGD FX) = S$550; "
        "if the same FX path were used for both legs the figure would be wrong"
    )


# ══════════════════════════════════════════════════════════════════════════════
# IE-RETURNS-TWR-MWR
# ══════════════════════════════════════════════════════════════════════════════


def test_IE_RETURNS_TWR_MWR():
    """IE-RETURNS-TWR-MWR: GIVEN a portfolio value series with mid-period external cash flows
    WHEN returns are computed
    THEN TWR neutralizes flows (manager-skill), MWR/XIRR reflects flow timing (personal return);
    period_gain_base nets out external flows so injections don't inflate the gain."""
    from backend.services import returns as r
    from backend.services.returns import _net_external_flow

    # Scenario: V0=1000, user deposits 500 mid-period (pocket-convention: -500), V1=1800.
    # TWR sub-period: (1800 − 500) / 1000 − 1 = 0.30 (30% market gain only).
    # MWR (XIRR): personal return including deposit timing → must differ from TWR.
    # period_gain_base: 1800 − 1000 + (−500) = 300 (not 800, which would include the deposit).
    series = [
        {"date": "2025-06-05", "value": 1_000.0},
        {"date": "2026-06-05", "value": 1_800.0},
    ]
    flows = [("2025-12-05", -500.0)]  # pocket-convention: deposit → negative

    twr_block = r.twr(series, flows, annualize=False)
    assert twr_block is not None
    assert twr_block["period_return"] == pytest.approx(0.30), (
        "TWR must neutralize the 500 deposit — only the 30% market move counts"
    )

    # MWR: XIRR over [-1000 opening, -500 deposit, +1800 ending value]
    opening = [("2025-06-05", -1_000.0)]
    mwr = r.xirr(opening + flows, 1_800.0, "2026-06-05")
    assert mwr is not None
    # TWR and MWR must diverge because the deposit arrived mid-period
    assert abs(mwr - twr_block["period_return"]) > 1e-6, (
        "MWR and TWR must diverge when a mid-period deposit shifts the timing (textbook case)"
    )

    # period_gain_base nets out external flows so the 500 deposit does not inflate the figure
    period_gain_base = round(1_800.0 - 1_000.0 + _net_external_flow(flows), 2)
    assert period_gain_base == pytest.approx(300.0), (
        "period_gain_base must be 300 (market gain), not 800 (which includes the 500 deposit)"
    )


# ══════════════════════════════════════════════════════════════════════════════
# IE-BENCHMARK-SUFFICIENT (I1)
# ══════════════════════════════════════════════════════════════════════════════

_TODAY_BENCH = date(2026, 6, 5)


@pytest.fixture
def bench_isolated(tmp_path, monkeypatch, db_isolated):
    """InstrumentStore and PriceHistoryStore both resolve against the per-test isolated
    database (db_isolated) — both are DB-backed (plan 2, tasks 3 and 5)."""

    def seed(instruments, price_history):
        from sqlalchemy import delete

        from backend.db.engine import get_engine
        from backend.db.instrument_store import InstrumentStore
        from backend.db.price_history_io import import_price_history
        from backend.db.tables import price_points

        InstrumentStore().replace_all(instruments)  # full replace, like the old file write
        with get_engine().begin() as conn:
            conn.execute(delete(price_points))  # full replace, like the old file write
            import_price_history(conn, price_history)

    return seed


def test_IE_BENCHMARK_SUFFICIENT(bench_isolated):
    """IE-BENCHMARK-SUFFICIENT (I1): GIVEN a benchmark with less price history than the window
    (90 days on a 1Y window), while another instrument has older history
    WHEN the benchmark comparison is computed
    THEN it is marked INSUFFICIENT (sufficient=False, twr=None) — it does NOT present the
    short-period return as the full-period figure.
    A benchmark with real coverage at/before the window start still computes correctly."""
    from backend.services import returns as r

    bench_inst = {
        "id": "I-SPY",
        "symbol": "SPY",
        "name": "SPDR S&P 500",
        "price_source": "yahoo",
        "price_source_id": "SPY",
    }
    other_inst = {
        "id": "I-OTHER",
        "symbol": "OTHER",
        "name": "Old Instrument",
        "price_source": "yahoo",
        "price_source_id": "OTHER",
    }

    bench_isolated(
        [bench_inst, other_inst],
        {
            # OTHER has ancient history — makes PriceHistoryStore().earliest_date() very old.
            # The pre-I1 bug used this global earliest to declare SPY sufficient; the fix checks
            # SPY's OWN series for a real price at/before the 1Y window start (2025-06-05).
            "OTHER": [
                {"date": "2020-01-01", "price": 10.0},
                {"date": "2026-06-05", "price": 20.0},
            ],
            # SPY: only 90 days of history — no real price on/before 2025-06-05
            "SPY": [
                {"date": "2026-03-05", "price": 500.0},
                {"date": "2026-06-05", "price": 550.0},
            ],
        },
    )

    out = r.benchmark_return("I-SPY", "1Y", today=_TODAY_BENCH)
    assert out is not None
    assert out["data_quality"]["sufficient"] is False, (
        "90-day benchmark on a 1Y window must be INSUFFICIENT "
        "(the global earliest_date from OTHER must NOT make it appear sufficient)"
    )
    assert out["twr"] is None, (
        "insufficient benchmark must produce twr=None, not a fabricated figure"
    )

    # Regression guard: a benchmark WITH a real price at/before the window start still computes
    bench_isolated(
        [bench_inst],
        {
            "SPY": [
                {"date": "2025-06-05", "price": 100.0},  # real price AT 1Y window start
                {"date": "2026-06-05", "price": 110.0},
            ]
        },
    )
    ok_out = r.benchmark_return("I-SPY", "1Y", today=_TODAY_BENCH)
    assert ok_out is not None
    assert ok_out["data_quality"]["sufficient"] is True
    assert ok_out["twr"] is not None
    assert ok_out["twr"]["period_return"] == pytest.approx(0.10)


# ══════════════════════════════════════════════════════════════════════════════
# IE-LOT-FEES (I2)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def lot_rows_isolated(db_isolated):
    """LotStore is DB-backed (plan 4b task 2) — db_isolated gives _lot_rows tests a fresh
    per-test database instead of a tmp_path file redirect. Seed via
    LotStore().bulk_insert(...) (verbatim rows, no _make_lot coercion)."""
    return None


def _make_lot(position_id: str, **overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "position_id": position_id,
        "acquired_at": (date.today() - timedelta(days=400)).isoformat(),
        "original_qty": 5.0,
        "remaining_qty": 5.0,
        "unit_cost": 100.0,
        "fees": 0.0,
        "closed": False,
    }
    base.update(overrides)
    return base


def test_IE_LOT_FEES(lot_rows_isolated):
    """IE-LOT-FEES (I2): GIVEN a lot with acquisition fees (unit_cost=100, fees=10, original_qty=5)
    WHEN the asset-detail lot rows are built
    THEN effective_unit_cost = 102 (fee-amortized: 100 + 10/5) and remaining_basis = 510
    (not the raw 100 and 500)."""
    from backend.db.lot_store import LotStore
    from backend.services.asset_detail import _lot_rows

    pos_id = str(uuid.uuid4())
    lot = _make_lot(
        pos_id,
        unit_cost=100.0,
        fees=10.0,  # 10 / 5 units = 2.0 per unit amortization
        original_qty=5.0,
        remaining_qty=5.0,
    )
    LotStore().bulk_insert([lot])

    rows = _lot_rows(pos_id)
    assert len(rows) == 1
    row = rows[0]

    assert row["effective_unit_cost"] == pytest.approx(102.0), (
        f"expected 102.0 (100 + 10/5), got {row['effective_unit_cost']} — "
        "fees must be amortized into effective_unit_cost, not left at raw unit_cost"
    )
    assert row["remaining_basis"] == pytest.approx(510.0), (
        f"expected 510.0 (5 × 102), got {row['remaining_basis']} — "
        "remaining_basis must use fee-amortized cost"
    )


# ══════════════════════════════════════════════════════════════════════════════
# IE-LOT-TERM (I3)
# ══════════════════════════════════════════════════════════════════════════════


def test_IE_LOT_TERM(lot_rows_isolated):
    """IE-LOT-TERM (I3): GIVEN a lot held exactly 365 days
    WHEN its term is classified in the asset-detail lot rows
    THEN it is 'long' (>= 365), consistent with lots.classify_term."""
    from backend.db.lot_store import LotStore
    from backend.services.asset_detail import _lot_rows
    from backend.services.lots import classify_term

    pos_id = str(uuid.uuid4())
    today = date.today()
    acquired = (today - timedelta(days=365)).isoformat()
    lot = _make_lot(pos_id, acquired_at=acquired)
    LotStore().bulk_insert([lot])

    rows = _lot_rows(pos_id)
    assert len(rows) == 1
    assert rows[0]["term_to_date"] == "long", (
        f"exactly 365 days held must be 'long' (>= threshold), "
        f"got '{rows[0]['term_to_date']}' — off-by-one vs classify_term"
    )

    # Confirm consistency with the lots.classify_term function
    assert classify_term(acquired, today.isoformat()) == "long", (
        "classify_term must also treat exactly 365 days as 'long'"
    )

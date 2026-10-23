"""
lots_service — tax lots + realized P&L (the investment keystone, P2-INV1).

ALL realized-gain arithmetic, lot selection (FIFO + specific-ID), holding-period
classification and multi-currency cost-basis conversion live HERE — never in a router,
AI tool, or the UI (mirrors how ledger_service owns the balance/net-worth fold). The
position's own figures are re-derived from its lots by services.position_figures. FX uses backend.core.fx.to_base (the pivot math). Base
figures follow the historical-rate lock (ADR 2026-07-15, mirroring the ledger):
lots stamp the cost→base rate at acquire, events stamp a per-leg realized_gain_base
at disposal, and reads TRUST those stamps while base_currency matches the current
base — recomputing from the native gain only on a base change or unstamped legacy rows.

Design: .claude/rfcs/phase2/inv1-tax-lots-pnl.md. Key invariants:
  - Money rounds to 2dp only at the reported boundary (like ledger.account_balance);
    quantities and intermediate per-unit costs stay exact floats.
  - Unknown cost basis propagates as None (never a fabricated 0) — a disposal against
    an unknown-cost lot books realized_gain=None + cost_basis_known=False (AC-12).
  - compute_disposal is PURE (validates before any write); apply_lot_change is the
    single checkpoint-covered write choke point (acquire | dispose), idempotent on a
    client key (AC-16). Lot consumption + event append + position resync happen in
    sequence so a turn checkpoint reverts all three files together.

Imports are downward only (db + core) — no api/ai import (Contract 3).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Connection

from backend.core.errors import InsufficientLots, InvalidLotSelection
from backend.core.fx import base_currency, to_base
from backend.core.trade_text import trade_text
from backend.core.user_time import user_today
from backend.core.write_receipt import record_write
from backend.db.engine import locked_unit_of_work
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.tables import lots as lots_tbl
from backend.db.tables import positions as positions_tbl
from backend.db.tables import realized_events as realized_tbl
from backend.db.tables import transactions as txns_tbl
from backend.db.tables import txn_audit as txn_audit_tbl
from backend.services import position_figures

# Lots may be split/consumed at floating-point granularity; treat sub-dust as zero so a
# fully-consumed lot closes cleanly instead of leaving 1e-12 behind.
_QTY_EPS = 1e-9

# Default holding-period threshold (days). Long ≥ threshold, short below. Jurisdiction
# rules (SG = no CGT, US 1yr, AU 12mo + discount) are out of scope (P4-TX1) — the
# boundary is this single configurable threshold + a per-line `term` string (D7).
DEFAULT_TERM_THRESHOLD_DAYS = 365


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Selection ──────────────────────────────────────────────────────────────────


def select_lots_fifo(open_lots: list[dict], qty: float) -> list[tuple[dict, float]]:
    """Take `qty` units off the FIFO queue. `open_lots` MUST already be FIFO-ordered
    (LotStore.open_lots_for_position returns them so). Returns [(lot, take_qty), ...].

    Raises InsufficientLots if the open lots don't hold enough units (AC-13) — and does
    so BEFORE returning any partial plan, so the caller never half-consumes.
    """
    remaining = float(qty)
    if remaining <= 0:
        raise InvalidLotSelection("disposal quantity must be positive", qty=qty)
    available = sum(float(lot.get("remaining_qty") or 0) for lot in open_lots)
    if remaining - available > _QTY_EPS:
        raise InsufficientLots(
            "not enough open lot quantity to cover the disposal",
            requested=qty,
            available=available,
        )
    plan: list[tuple[dict, float]] = []
    for lot in open_lots:
        if remaining <= _QTY_EPS:
            break
        lot_remaining = float(lot.get("remaining_qty") or 0)
        if lot_remaining <= 0:
            continue
        take = min(lot_remaining, remaining)
        plan.append((lot, take))
        remaining -= take
    return plan


def select_lots_specific(open_lots: list[dict], selection: list[dict]) -> list[tuple[dict, float]]:
    """Specific-ID selection: the caller names lot ids + qty per lot. Validates each lot
    is in the position's open lots and take ≤ that lot's remaining_qty — raising
    InvalidLotSelection BEFORE returning any plan (atomicity, AC-7). Returns
    [(lot, take_qty), ...] in the caller's order.

    `selection` is a list of {"lot_id": str, "qty": float}.
    """
    by_id = {lot.get("id"): lot for lot in open_lots}
    plan: list[tuple[dict, float]] = []
    # Accumulate per-lot asks first so two lines naming the same lot are summed and
    # validated against ONE remaining_qty (can't sneak past by splitting the ask).
    asks: dict[str, float] = {}
    order: list[str] = []
    for sel in selection:
        lot_id = sel.get("lot_id")
        take = float(sel.get("qty") or 0)
        if take <= 0:
            raise InvalidLotSelection("lot selection qty must be positive", lot_id=lot_id)
        if lot_id not in by_id:
            raise InvalidLotSelection("lot not found among the position's open lots", lot_id=lot_id)
        if lot_id not in asks:
            order.append(lot_id)
        asks[lot_id] = asks.get(lot_id, 0.0) + take
    for lot_id in order:
        lot = by_id[lot_id]
        take = asks[lot_id]
        lot_remaining = float(lot.get("remaining_qty") or 0)
        if take - lot_remaining > _QTY_EPS:
            raise InvalidLotSelection(
                "requested qty exceeds the lot's remaining quantity",
                lot_id=lot_id,
                requested=take,
                remaining=lot_remaining,
            )
        plan.append((lot, take))
    return plan


# ── Gain math (the testable core) ───────────────────────────────────────────────


def effective_unit_cost(lot: dict) -> Optional[float]:
    """Cost per unit including amortized acquisition fees: unit_cost + fees/original_qty.
    Returns None when the lot's unit_cost is unknown (None) — never a fabricated 0 (D8)."""
    unit_cost = lot.get("unit_cost")
    if unit_cost is None:
        return None
    fees = float(lot.get("fees", 0) or 0)
    original_qty = float(lot.get("original_qty") or 0)
    if original_qty and fees:
        return float(unit_cost) + fees / original_qty
    return float(unit_cost)


def classify_term(
    acquired_at: str | None,
    disposed_at: str | None,
    threshold_days: int = DEFAULT_TERM_THRESHOLD_DAYS,
) -> str:
    """ "long" if held ≥ threshold days, "short" if below, "unknown" if either date is
    missing/unparseable (no fabricated holding period). Dates are ISO 'YYYY-MM-DD'."""
    if not acquired_at or not disposed_at:
        return "unknown"
    try:
        held = (date.fromisoformat(disposed_at[:10]) - date.fromisoformat(acquired_at[:10])).days
    except (ValueError, TypeError):
        return "unknown"
    return "long" if held >= threshold_days else "short"


def line_realized_gain(
    take_qty: float,
    proceeds_per_unit: float,
    lot: dict,
    proceeds_ccy: str,
    rates: dict | None,
    disposed_at: str = "",
    disposal_fee_per_unit: float = 0.0,
    threshold_days: int = DEFAULT_TERM_THRESHOLD_DAYS,
) -> dict:
    """Per-consumed-lot realized-gain line, denominated in the PROCEEDS currency.

    The lot's basis (in its own cost_currency) is converted INTO the proceeds currency
    via core.fx before subtracting — currencies are never mixed in a subtraction (AC-11).
    Unknown cost (effective_unit_cost is None) ⇒ realized_gain=None, not 0 (AC-12).
    `disposal_fee_per_unit` reduces proceeds (already in proceeds_ccy).
    Returns a Consumption-shaped dict.
    """
    eff_cost = effective_unit_cost(lot)
    cost_ccy = lot.get("cost_currency") or proceeds_ccy
    net_proceeds_per_unit = float(proceeds_per_unit) - float(disposal_fee_per_unit)
    if eff_cost is None:
        gain = None
    else:
        # Convert the FULL lot basis (eff_cost × take_qty) into the proceeds currency ONCE,
        # then subtract from the total net proceeds — rounding only at this line boundary.
        # Converting the per-unit cost first would round to 2dp (inside to_base) BEFORE the
        # ×qty multiply, magnifying the cent-rounding by quantity and breaking the module's
        # "intermediate per-unit costs stay exact floats" invariant (bites cross-currency
        # disposals and any non-2dp unit cost, e.g. crypto / a migrated weighted-avg basis).
        total_cost_in_proceeds = to_base(
            eff_cost * float(take_qty), cost_ccy, proceeds_ccy, rates=rates
        )
        gain = round(net_proceeds_per_unit * float(take_qty) - total_cost_in_proceeds, 2)
    return {
        "lot_id": lot.get("id"),
        "qty": float(take_qty),
        "unit_cost": eff_cost,
        "cost_currency": (cost_ccy or "").upper() or None,
        "acquired_at": lot.get("acquired_at") or "",
        "term": classify_term(lot.get("acquired_at"), disposed_at, threshold_days),
        "realized_gain": gain,
        # Audit copy of the consumed lot's historical-rate stamp (ADR 2026-07-15) so the
        # event stays re-derivable even if the lot is later deleted. None on legacy lots.
        "fx_rate_acquire": lot.get("fx_rate"),
    }


def _per_leg_gain_base(
    plan: list,
    net_proceeds_per_unit: float,
    qty: float,
    proceeds_ccy: str,
    base: str,
    rates: dict | None,
) -> Optional[float]:
    """Tax-standard base gain: total net proceeds at the DISPOSAL-date rate minus each
    consumed lot's basis at its own ACQUISITION-date rate (the lot's historical-rate
    stamp). None unless EVERY consumed lot has a known cost AND a trusted stamp
    (fx_rate present, base_currency == the current base) — a partial per-leg number
    would silently mix methodologies."""
    cost_base = 0.0
    for lot, take in plan:
        eff = effective_unit_cost(lot)
        fx = lot.get("fx_rate")
        if eff is None or not fx or (lot.get("base_currency") or "").upper() != base:
            return None
        cost_base += eff * float(take) * float(fx)
    proceeds_base = to_base(net_proceeds_per_unit * qty, proceeds_ccy, base, rates=rates)
    return round(proceeds_base - cost_base, 2)


def compute_disposal(
    position: dict,
    open_lots: list[dict],
    disposal: dict,
    rates: dict | None = None,
    base: str | None = None,
    threshold_days: int = DEFAULT_TERM_THRESHOLD_DAYS,
) -> dict:
    """PURE: validate + compute a disposal DRAFT (no writes). Picks the lot-selection
    method, selects lots, computes per-line gains, aggregates, and converts the
    aggregate gain to the base currency via core.fx (D4). Raises InvalidLotSelection /
    InsufficientLots before producing any plan (AC-7, AC-13) so apply only mutates after
    a clean draft.

    `disposal` keys: quantity, proceeds_per_unit, proceeds_currency, disposal_fees,
    disposed_at, method ("fifo"|"specific_id"), lot_selection [{lot_id, qty}],
    is_taxable, transfer_to_account_id (an already-RESOLVED id — see
    resolve_transfer_destination). Returns a RealizedEvent-shaped draft dict (no
    id/created_at).
    """
    base = (base or base_currency()).upper()
    qty = float(disposal.get("quantity") or 0)
    if qty <= 0:
        raise InvalidLotSelection("disposal quantity must be positive", qty=qty)

    method = disposal.get("method") or "fifo"
    proceeds_ccy = (
        disposal.get("proceeds_currency") or position.get("cost_currency") or base
    ).upper()
    proceeds_per_unit = float(disposal.get("proceeds_per_unit") or 0)
    disposal_fees = float(disposal.get("disposal_fees", 0) or 0)
    disposed_at = disposal.get("disposed_at") or user_today().isoformat()
    # is_taxable defaults True; a transfer-out / gift passes False (carries basis out).
    is_taxable = bool(disposal.get("is_taxable", True))

    # Money guard (P2-INV1c): a TAXABLE disposal with zero net proceeds would book the full
    # cost basis as a fabricated loss with no value received — almost always a forgotten sale
    # price or a transfer the user meant to flag. The intentional zero-proceeds path is a
    # transfer-out/gift (is_taxable=False, which carries basis out and realizes nothing). So we
    # refuse a taxable, zero-net-proceeds sale here (before any write) rather than silently
    # book a large fake loss; a real $0 disposal (worthless/written-off) is recorded as a
    # transfer-out. Net of fees so e.g. proceeds=fee nets to 0 and is caught too.
    net_proceeds_per_unit = proceeds_per_unit - (disposal_fees / qty if qty else 0.0)
    if is_taxable and net_proceeds_per_unit <= 0:
        raise InvalidLotSelection(
            "a taxable sale needs a positive proceeds/unit (booking a $0 sale would record the "
            "full cost basis as a fabricated loss); to write off / transfer out the holding with "
            "no value received, mark it a transfer-out (is_taxable=false)",
            proceeds_per_unit=proceeds_per_unit,
            disposal_fees=disposal_fees,
        )

    # A destination turns the non-taxable disposal into a move between the user's own
    # accounts (Activity shows "→ IBKR"). It is only coherent on a pure share movement — a
    # sale, a price, or a cash deposit alongside it means the caller mixed two operations.
    transfer_to = disposal.get("transfer_to_account_id") or None
    if transfer_to and (
        is_taxable or proceeds_per_unit != 0 or disposal.get("proceeds_account_id")
    ):
        raise InvalidLotSelection(
            "a transfer moves shares, not money — transfer_to_account_id needs "
            "is_taxable=false, proceeds_per_unit=0 and no proceeds_account_id",
            transfer_to_account_id=transfer_to,
        )

    if method == "specific_id":
        selection = disposal.get("lot_selection") or []
        if not selection:
            raise InvalidLotSelection("specific_id disposal requires a lot_selection")
        plan = select_lots_specific(open_lots, selection)
        # The selected qty must equal the disposal qty — no silent under/over fill.
        selected_qty = sum(take for _, take in plan)
        if abs(selected_qty - qty) > _QTY_EPS:
            raise InvalidLotSelection(
                "lot_selection quantities must sum to the disposal quantity",
                selected=selected_qty,
                requested=qty,
            )
    elif method == "fifo":
        plan = select_lots_fifo(open_lots, qty)
    else:
        raise InvalidLotSelection("unknown lot-selection method", method=method)

    fee_per_unit = disposal_fees / qty if qty else 0.0
    consumptions = [
        line_realized_gain(
            take,
            proceeds_per_unit,
            lot,
            proceeds_ccy,
            rates,
            disposed_at=disposed_at,
            disposal_fee_per_unit=fee_per_unit,
            threshold_days=threshold_days,
        )
        for lot, take in plan
    ]

    # Aggregate. If ANY consumed lot has unknown cost, the whole disposal's realized gain
    # is unknown (None) — we don't book a partial/fabricated number (D8, AC-12).
    cost_basis_known = all(c["realized_gain"] is not None for c in consumptions)
    if not is_taxable:
        # Transfer-out / gift: carries basis out, realizes nothing (AC-15).
        realized_gain: Optional[float] = None
        realized_gain_base: Optional[float] = None
        fx_rate: Optional[float] = None
    elif cost_basis_known:
        realized_gain = round(sum(c["realized_gain"] for c in consumptions), 2)
        # Per-leg base gain (ADR 2026-07-15, the tax-standard decomposition): proceeds at
        # the disposal-date rate MINUS each consumed lot's basis at its own
        # acquisition-date rate (the acquire stamp). Captures the FX gain on the basis
        # that a single-rate conversion misses. Falls back to converting the native gain
        # at the disposal rate when any consumed lot lacks a trusted stamp.
        per_leg = _per_leg_gain_base(plan, net_proceeds_per_unit, qty, proceeds_ccy, base, rates)
        realized_gain_base = (
            per_leg
            if per_leg is not None
            else to_base(realized_gain, proceeds_ccy, base, rates=rates)
        )
        fx_rate = _fx_rate_only(proceeds_ccy, base, rates)
    else:
        realized_gain = None
        realized_gain_base = None
        fx_rate = _fx_rate_only(proceeds_ccy, base, rates)

    return {
        "position_id": position.get("id", ""),
        "instrument_id": position.get("instrument_id", ""),
        "account_id": position.get("account_id", ""),
        "disposed_at": disposed_at,
        "quantity": qty,
        "proceeds_per_unit": proceeds_per_unit,
        "proceeds_currency": proceeds_ccy,
        "disposal_fees": disposal_fees,
        "method": method,
        "is_taxable": is_taxable,
        "transfer_to_account_id": transfer_to,
        "cost_basis_known": cost_basis_known,
        "consumptions": consumptions,
        "realized_gain": realized_gain,
        "realized_gain_base": realized_gain_base,
        "base_currency": base,
        "fx_rate": fx_rate,
    }


def _rate_between(currency: str, base: str, rates: dict | None) -> Optional[float]:
    """Exact currency→base multiplier from the rates table. Deliberately NOT via
    to_base(1.0, …) — to_base rounds AMOUNTS to 2dp, which is far too coarse for a
    per-unit rate (JPY→USD ≈ 0.0074 would collapse to 0.01). None when a leg is
    missing from the table."""
    cur = (currency or "").upper()
    b = (base or "").upper()
    if cur == b:
        return 1.0
    if rates is None:
        rates = _load_rates()
    cur_rate = rates.get(cur)
    base_rate = rates.get(b)
    if not cur_rate or not base_rate:
        return None
    return cur_rate / base_rate


def _fx_rate_only(currency: str, base: str, rates: dict | None) -> Optional[float]:
    """The proceeds→base FX rate to snapshot on an event when the gain is 0 or unknown
    (so we still record the rate in effect). None if the rate can't be resolved."""
    rate = _rate_between(currency, base, rates)
    return round(rate, 6) if rate else None


# ── Write choke point (checkpoint-covered, approve-tier) ─────────────────────────


# The change types that carry a cash-account ref. A named set, not an inline `ctype in (...)`:
# domain_introspect_ast reads a literal membership test on the discriminator as a dispatch
# branch, and listed these four ops twice (the second copy with no fields).
_CASH_REF_TYPES = frozenset({"acquire", "dispose", "amend", "dispose_amend"})


def apply_lot_change(change: dict) -> dict:
    """The single write entrypoint for lot mutations (D5). `change["type"]` ∈
    {"acquire", "dispose", "delete", "dispose_undo", "amend", "dispose_amend"}. Shared by the AI apply tools and the REST routes so
    turn-checkpoints cover every lot write (the writes funnel through
    JsonStore._save_raw → fire_write_hook).

    acquire: create one lot, resync the position aggregates. Idempotent on a client
             `idempotency_key` (same contract as dispose): a second apply with the
             same key is a no-op returning the existing lot id — pass a deterministic
             key (broker order id / email id + symbol + date) from automated flows so
             reprocessing the same source can never double-book a buy. Optional
             `funding_account_id` books a kind=investment cash debit (ADR 2026-09-11).
    dispose: recompute a fresh draft (re-validates against current lot state), consume
             lots, append the realized event, resync — all-or-nothing. Idempotent on a
             client `idempotency_key` (AC-16): a second apply with the same key is a
             no-op returning the existing event id.
    delete:  remove a fully-open (not partially consumed) lot and resync aggregates.
             Reverses a linked cash debit when `txn_id` is set.
    dispose_undo: reverse a disposal — re-open the lots it consumed, soft-delete its
             cash-proceeds leg (if any), delete the realized event, resync. Refuses if a
             consumed lot no longer exists.
    amend:   correct a lot's date, quantity, cost, fees, kind or note. A lot a sale has used
             keeps the sale's allocation; that sale's lines follow (services/lot_corrections).
             Linked purchase cash is rewritten only when a cash-bearing field changes.
    dispose_amend: undo then rebook a disposal in ONE unit of work (sale edit).
    """
    ctype = change.get("type")
    if ctype in _CASH_REF_TYPES:
        change = resolve_cash_account_refs(change)
    if ctype == "acquire":
        return _record(_apply_acquire(change), ctype)
    if ctype == "dispose":
        return _record(_apply_dispose(change), ctype)
    if ctype == "delete":
        return _record(_delete_lot(change.get("lot_id", "")), ctype)
    if ctype == "dispose_undo":
        return _record(_undo_dispose(change), ctype)
    if ctype == "amend":
        return _record(_apply_amend(change), ctype)
    if ctype == "dispose_amend":
        return _record(_apply_dispose_amend(change), ctype)
    return {"error": f"unknown change type: {ctype}"}


# (op, entity_type, result key carrying the id) per change type, for the write-receipt chip.
# A domain verb maps onto the closed create|update|delete vocabulary and rides in
# ``meta["verb"]`` — the chip head must never learn per-domain verbs.
_RECEIPT_MAP = {
    "acquire": ("create", "lot", "lot_id"),
    "delete": ("delete", "lot", "lot_id"),
    "amend": ("update", "lot", "lot_id"),
    "dispose": ("create", "disposal", "event_id"),
    "dispose_undo": ("delete", "disposal", "event_id"),
    "dispose_amend": ("update", "disposal", "event_id"),
}


def _record(result: dict, ctype: str) -> dict:
    """Report this lot write onto the turn's receipt (a no-op outside an assistant turn).

    Records ONLY the entity this choke IS the choke for. A dispose also books a cash
    transaction leg — that leg funnels through the ledger's own audit choke and reports
    itself, so recording it here too would double-count the same economic event in the
    chip's head."""
    try:
        mapped = _RECEIPT_MAP.get(ctype)
        if not mapped or not isinstance(result, dict) or result.get("error"):
            return result
        op, entity_type, id_key = mapped
        record_write(op, result.get(id_key) or "", entity_type=entity_type, meta={"verb": ctype})
    except Exception:  # noqa: BLE001 - describing a write must never be able to fail it
        pass
    return result


def _delete_lot_core(lot_id: str, *, conn: Connection | None = None) -> dict:
    """conn=-accepting core of _delete_lot: validate (partial-consumption refusal) +
    delete + resync, all optionally through the SAME conn. Extracted (Task 4, storage
    plan 4b) so a composing choke (``equity._vest_undo``'s delivered-lot-delete branch)
    can remove a lot WITHOUT re-entering ``apply_lot_change``'s own top-level transaction
    — the same self-deadlock guard ``_mint_lot`` exists for on the mint side."""
    lot_store = LotStore()
    lot = lot_store.find_by_id(lot_id, conn=conn)
    if not lot:
        return {"error": "lot not found"}
    remaining = float(lot.get("remaining_qty") or 0)
    original = float(lot.get("original_qty") or 0)
    if original > 0 and remaining < original:
        return {
            "error": (
                "shares from this lot have been sold, so it can't be removed — undo that sale "
                "first (or correct the lot's date, quantity or cost instead)"
            )
        }
    position_id = lot.get("position_id", "")
    lot_store.delete(lot_id, conn=conn)
    if position_id:
        position_figures.rederive(position_id, conn=conn)
    return {"ok": True, "lot_id": lot_id}


def _delete_lot(lot_id: str) -> dict:
    """Remove a lot that has not been partially disposed. Safe for orphaned or duplicate
    lots. Resyncs the parent position's aggregates after deletion. Reverses a linked
    cash debit (ADR 2026-09-11) when the lot carries ``txn_id``.

    TASK 4 (storage plan 4b sweep finding — not in the brief's binding list, found by
    tracing every write path): the delete + resync is a genuine 2-table write
    (lots + positions), composed the same way as every other choke in this module."""
    lot = LotStore().find_by_id(lot_id)
    txn_id = (lot or {}).get("txn_id")
    tables = [lots_tbl, positions_tbl]
    if txn_id:
        tables.extend((txns_tbl, txn_audit_tbl))
    with locked_unit_of_work(*tables) as conn:
        if txn_id:
            from backend.services import ledger as ledger_service

            ledger_service.bulk_delete_transactions([txn_id], conn=conn, reject_identities=False)
        return _delete_lot_core(lot_id, conn=conn)


def find_realized_event(event_id: str) -> Optional[dict]:
    """Read one realized (disposal) event by id — the propose_lot_dispose_undo tool's read
    so it can build the confirm card without the tool reaching into the store directly."""
    if not event_id:
        return None
    return RealizedEventStore().find_by_id(event_id)


def _undo_dispose(change: dict) -> dict:
    """Reverse a disposal (the inverse of _apply_dispose): re-open the lots it consumed,
    soft-delete its cash-proceeds leg if one was booked, delete the realized event, and
    resync the position. Refuses (no mutation) if a consumed lot no longer exists, since the
    shares can't then be restored cleanly. Mirrors equity._vest_undo's reverse-order undo;
    the whole turn is checkpoint-snapshotted so all touched files revert together."""
    event_id = change.get("event_id") or change.get("id")
    events = RealizedEventStore()
    ev = events.find_by_id(event_id) if event_id else None
    if not ev:
        return {"error": "disposal event not found"}

    lots = LotStore()
    consumptions = ev.get("consumptions") or []
    # Validate every consumed lot still exists BEFORE mutating (all-or-nothing).
    missing = [c.get("lot_id") for c in consumptions if not lots.find_by_id(c.get("lot_id", ""))]
    if missing:
        return {
            "error": (
                "cannot undo — one or more consumed lots no longer exist "
                f"({missing}); the shares can't be restored automatically"
            )
        }

    # TASK 4 (storage plan 4b): the lot restore + the cash-leg reversal + the realized-
    # event delete + the position resync now commit as ONE transaction. txn_audit is
    # ALSO locked (beyond the brief's literal 4-table list) for the same reason
    # _apply_dispose locks it — bulk_delete_transactions, composed via conn=, writes
    # `transactions` AND `txn_audit` as one unit.
    pos_id = ev.get("position_id", "")
    txn_id = ev.get("txn_id")
    with locked_unit_of_work(
        lots_tbl, positions_tbl, realized_tbl, txns_tbl, txn_audit_tbl
    ) as conn:
        _restore_disposal(ev, conn=conn)
        agg = position_figures.rederive(pos_id, conn=conn) if pos_id else {}
    return {"ok": True, "event_id": event_id, "position_id": pos_id, "txn_id": txn_id, **agg}


def _restore_disposal(ev: dict, *, conn: Connection) -> None:
    """Undo a disposal's writes on an already-open unit of work: restore consumed qty,
    reverse the proceeds cash leg, delete the event. Caller validated lots exist."""
    lots = LotStore()
    consumptions = ev.get("consumptions") or []
    lots.bulk_restore([(c["lot_id"], float(c.get("qty") or 0)) for c in consumptions], conn=conn)
    txn_id = ev.get("txn_id")
    if txn_id:
        from backend.services import ledger as ledger_service

        ledger_service.bulk_delete_transactions([txn_id], conn=conn, reject_identities=False)
    RealizedEventStore().delete(ev["id"], conn=conn)


def _find_identical_open_lot(lots: "LotStore", position_id: str, change: dict) -> dict | None:
    """The double-book signature: a fully-open lot on the same position with the same
    kind, acquired_at, quantity, and unit_cost. Two tool calls booking one trade (the
    SPCX incident: add_positions auto-lot + acquire_lots seconds later) hit
    this; two genuinely identical same-day fills are rare enough to require the caller's
    explicit allow_duplicate. Scoped to kind=buy/drp (a DRP reinvestment is a purchase and
    its record path RELIES on this refusal for dedupe) — grant/vest lots have their own
    per-tranche dedupe and identical same-day vests are legitimate. Undated acquires
    carry no signature to match on; a partially consumed match is an older real fill,
    not a double-book."""
    kind = change.get("kind", "buy")
    if kind not in ("buy", "drp"):
        return None
    acquired_at = change.get("acquired_at") or ""
    if not acquired_at:
        return None
    qty = float(change.get("quantity") or 0)
    unit_cost = change.get("unit_cost") or None  # store normalizes 0 → None (unknown)
    for lot in lots.open_lots_for_position(position_id):
        if lot.get("kind") != kind or (lot.get("acquired_at") or "") != acquired_at:
            continue
        original = float(lot.get("original_qty") or 0)
        if float(lot.get("remaining_qty") or 0) != original:
            continue
        if abs(original - qty) > 1e-9:
            continue
        lot_cost = lot.get("unit_cost")
        if (lot_cost is None) != (unit_cost is None):
            continue
        if lot_cost is not None and abs(float(lot_cost) - float(unit_cost)) > 1e-9:
            continue
        return lot
    return None


def find_double_book_candidate(position_id: str, change: dict) -> dict | None:
    """Public read for propose_lot_acquire: surface the identical-lot refusal at propose
    time (apply enforces it too), so the user never confirms a diff the choke point will
    reject."""
    return _find_identical_open_lot(LotStore(), position_id, change)


# Price-equality tolerance for the duplicate-disposal signature (proceeds_per_unit floats).
_PRICE_EPS = 1e-6


def _disposal_signature(position: dict, change: dict) -> tuple[str, str, float, float, str]:
    """The (position, date, qty, price, currency) tuple that identifies "the same sale".
    Resolves disposed_at + proceeds_currency the SAME way compute_disposal does, so the
    signature matches what would actually be stored. `method` and `disposal_fees` are
    deliberately EXCLUDED — the ONWAI double-book was one sale recorded once Specific (with a
    €3.50 fee) and once FIFO (no fee), so keying on those would have missed it."""
    disposed_at = change.get("disposed_at") or user_today().isoformat()
    proceeds_ccy = (
        change.get("proceeds_currency") or position.get("cost_currency") or base_currency()
    ).upper()
    return (
        position.get("id", ""),
        disposed_at,
        float(change.get("quantity") or 0),
        float(change.get("proceeds_per_unit") or 0),
        proceeds_ccy,
    )


def _find_duplicate_disposal(
    position: dict, change: dict, *, exclude_event_id: str | None = None
) -> dict | None:
    """An already-recorded disposal on the same position matching on date, quantity,
    proceeds/unit and currency — the "same sale entered twice" signature. The client
    `idempotency_key` dedupes automated retries; THIS catches a second manual/AI entry that
    carries no key (the ONWAI incident). Returns the matching realized event, or None.
    ``exclude_event_id`` skips the event being amended so a save of the same figures is
    not refused as a duplicate of itself."""
    pos_id, disposed_at, qty, price, ccy = _disposal_signature(position, change)
    for ev in RealizedEventStore().query_events(position_id=pos_id):
        if exclude_event_id and ev.get("id") == exclude_event_id:
            continue
        if (ev.get("disposed_at") or "") != disposed_at:
            continue
        if abs(float(ev.get("quantity") or 0) - qty) > _QTY_EPS:
            continue
        if abs(float(ev.get("proceeds_per_unit") or 0) - price) > _PRICE_EPS:
            continue
        if (ev.get("proceeds_currency") or "").upper() != ccy:
            continue
        return ev
    return None


def find_duplicate_disposal_candidate(change: dict) -> dict | None:
    """Public read for propose_lot_dispose: surface the duplicate-sale refusal at propose
    time (apply enforces it too), so the user never confirms a sale the choke point rejects."""
    pos = PositionStore().find_by_id(change.get("position_id", ""))
    if not pos:
        return None
    return _find_duplicate_disposal(pos, change)


def _mint_lot(lot_data: dict, *, conn: Connection | None = None) -> tuple[dict, dict]:
    """conn=-accepting lot-INSERT core: build the stored row via
    ``db.lot_store._make_lot``, insert it, and resync the parent position's aggregates —
    both optionally through the SAME caller-supplied conn. Extracted from
    ``_apply_acquire``'s tail (Task 4, storage plan 4b) so a composing choke
    (``equity._vest_apply``'s FMV-lot mint) can mint a lot WITHOUT re-entering
    ``apply_lot_change``'s own top-level transaction — which would re-acquire the
    lots/positions locks already held by this thread and self-deadlock (threading.Lock is
    not reentrant). ``lot_data`` must already carry any fx_rate/base_currency stamp the
    caller wants persisted (the historical-rate lock, ADR 2026-07-15) — this helper
    performs NO validation, only insert+resync. Returns (lot, resync_agg)."""
    from backend.db.lot_store import _make_lot

    row = _make_lot(lot_data)
    lot = LotStore().insert(row, conn=conn)
    agg = position_figures.rederive(lot_data.get("position_id", ""), conn=conn)
    return lot, agg


def _acquisition_gross(change: dict) -> Optional[float]:
    """qty × unit_cost + fees, or None when cost is unknown / not a positive figure."""
    unit = change.get("unit_cost")
    if unit in (None, ""):
        return None
    qty = float(change.get("quantity") or 0)
    fees = float(change.get("fees") or 0)
    gross = qty * float(unit) + fees
    if not math.isfinite(gross) or gross <= 0:
        return None
    return round(gross, 2)


def _will_fund_acquire(change: dict) -> bool:
    return bool(change.get("funding_account_id")) and _acquisition_gross(change) is not None


def _ensure_acquire_position(change: dict) -> dict | None:
    """Resolve the position an acquire lands on. Existing ``position_id`` wins.
    Otherwise find-or-create a vested liquid row for instrument_id × account_id,
    reusing a sold-out (qty 0) row so a closed holding re-opens instead of minting
    a sibling."""
    pos_id = change.get("position_id") or ""
    if pos_id:
        return PositionStore().find_by_id(pos_id)
    inst_id = change.get("instrument_id") or ""
    acct_id = change.get("account_id") or ""
    if not inst_id or not acct_id:
        return None
    inst = InstrumentStore().find_by_id(inst_id)
    if not inst:
        return None
    from backend.db.account_store import AccountStore
    from backend.db.grant_store import GrantStore
    from backend.services import positions as positions_service
    from backend.services.positions import vesting_state_of

    if not AccountStore().find_by_id(acct_id):
        return None
    active = positions_service.find_active_liquid_position(inst_id, acct_id)
    if active:
        return active
    grant_owned: set[str] = set()
    for grant in GrantStore().all():
        for key in ("locked_position_id", "pending_position_id"):
            if grant.get(key):
                grant_owned.add(grant[key])
    for pos in PositionStore().query_positions(instrument_id=inst_id, account_id=acct_id):
        if pos["id"] in grant_owned:
            continue
        if vesting_state_of(pos) == "vested":
            return pos
    return positions_service.add_position(
        instrument_id=inst_id,
        account_id=acct_id,
        cost_currency=change.get("cost_currency") or inst.get("quote_currency"),
        note=change.get("note", ""),
    )


def _book_funding_debit(
    change: dict, lot: dict, payee_id: Optional[str], *, conn: Connection | None = None
) -> Optional[str]:
    """Debit qty × unit_cost + fees from funding_account_id as a kind=investment outflow."""
    dest_id = change.get("funding_account_id")
    gross = _acquisition_gross(change)
    if not dest_id or gross is None:
        return None
    from backend.services import ledger as ledger_service

    cost_ccy = (change.get("cost_currency") or lot.get("cost_currency") or "").upper()
    txn_data = {
        "date": change.get("acquired_at") or user_today().isoformat(),
        "kind": "investment",
        "description": "Share purchase",
        "from_account_id": dest_id,
        "from_amount": gross,
        "from_currency": cost_ccy,
        "source": change.get("source", "manual"),
    }
    if payee_id:
        txn_data["payee_id"] = payee_id
    txn_data.update(
        _traded(
            lot.get("instrument_id"),
            lot.get("original_qty") or change.get("quantity"),
            verb="Bought",
            price=change.get("unit_cost"),
            currency=cost_ccy,
            fees=change.get("fees"),
        )
    )
    txn = ledger_service.create_transaction(
        txn_data, allow_duplicate=bool(change.get("allow_duplicate")), conn=conn
    )
    return txn.get("id")


def _traded(
    instrument_id: Optional[str],
    quantity,
    *,
    verb: str,
    price,
    currency: str,
    fees=None,
) -> dict:
    """The cash leg's own record of what it traded: ``ticker`` + ``quantity`` (unsigned — the
    leg's direction says buy or sell) and the trade as its ``description`` ("Sold 60 KSTL @
    150.00 USD"), replacing the bare "Share purchase"/"Sale proceeds" placeholder. The memo
    stays free for the owner's own note."""
    symbol = (InstrumentStore().find_by_id(instrument_id or "") or {}).get("symbol")
    if not symbol:
        return {}
    qty = abs(float(quantity or 0))
    if not qty:
        return {"ticker": symbol}
    price_f = float(price) if price not in (None, "") else None
    text = trade_text(verb, symbol, qty, price_f, currency, float(fees or 0))
    return {"ticker": symbol, "quantity": qty, "description": text}


def _future_acquisition(acquired_at) -> Optional[str]:
    """A lot records units already held, so its date can't be after the user's today — found
    2026-10-12 driving Add holding: a 2027 purchase was accepted and counted in today's
    quantity. An unreadable date is left to the existing checks."""
    if not acquired_at:
        return None
    try:
        when = date.fromisoformat(str(acquired_at)[:10])
    except ValueError:
        return None
    if when <= user_today():
        return None
    return (
        f"the acquired date {when.isoformat()} is in the future — a lot records units you "
        "already hold; record it on or after the day you buy"
    )


def _apply_acquire(change: dict) -> dict:
    lots = LotStore()
    idempotency_key = change.get("idempotency_key")
    if idempotency_key:
        existing = lots.find_by_idempotency_key(idempotency_key)
        if existing:  # already booked (agent retry / email reprocess) → no-op
            return {
                "ok": True,
                "lot_id": existing["id"],
                "position_id": existing.get("position_id"),
                "idempotent": True,
            }
    future = _future_acquisition(change.get("acquired_at"))
    if future:
        return {"error": future}
    pos = _ensure_acquire_position(change)
    if not pos:
        return {"error": "position not found"}
    change = {**change, "position_id": pos["id"]}
    if not change.get("allow_duplicate"):
        dup = _find_identical_open_lot(lots, pos["id"], change)
        if dup:
            return {
                "error": (
                    f"an identical open lot already exists on this position "
                    f"(lot_id={dup['id']}, {dup.get('acquired_at')}, "
                    f"qty {dup.get('original_qty')} @ {dup.get('unit_cost')}) — this trade "
                    "looks already booked; do NOT book it again. Only if these are truly "
                    "two separate identical fills, re-apply with allow_duplicate=true."
                ),
                "existing_lot_id": dup["id"],
                "position_id": pos["id"],
            }
    qty = float(change.get("quantity") or 0)
    if not math.isfinite(qty) or qty <= 0:
        return {
            "error": (
                "quantity must be a positive number — an acquire needs the real quantity "
                "of units bought (a missing/zero/negative quantity would silently book a "
                "zero-unit lot)"
            )
        }
    # Historical-rate lock (ADR 2026-07-15): stamp the cost→base rate in effect NOW — the
    # write-time rate is the acquisition-date rate when the trade is entered promptly
    # (same accepted semantics as the ledger's write-time base_amount stamp).
    base = base_currency().upper()
    cost_ccy = (change.get("cost_currency") or pos.get("cost_currency") or base).upper()
    fx_rate = _rate_between(cost_ccy, base, _load_rates())
    lot_data = {
        "position_id": pos["id"],
        "instrument_id": pos.get("instrument_id", ""),
        "account_id": pos.get("account_id", ""),
        "acquired_at": change.get("acquired_at") or "",
        "original_qty": qty,
        "remaining_qty": qty,
        "unit_cost": change.get("unit_cost"),
        "cost_currency": change.get("cost_currency") or pos.get("cost_currency"),
        "fees": change.get("fees", 0),
        "fx_rate": fx_rate,
        "base_currency": base,
        "kind": change.get("kind", "buy"),
        "txn_id": change.get("txn_id"),
        "idempotency_key": idempotency_key,
        "note": change.get("note", ""),
    }
    will_fund = _will_fund_acquire(change)
    payee_id = (
        _resolve_deposit_payee_if_needed({"proceeds_account_id": change.get("funding_account_id")})
        if will_fund
        else None
    )
    tables = [lots_tbl, positions_tbl]
    if will_fund:
        tables.extend((txns_tbl, txn_audit_tbl))
    with locked_unit_of_work(*tables) as conn:
        lot, agg = _mint_lot(lot_data, conn=conn)
        cash_leg_id = _book_funding_debit(change, lot, payee_id, conn=conn) if will_fund else None
        if cash_leg_id:
            lot = LotStore().touch(lot["id"], {"txn_id": cash_leg_id}, conn=conn) or lot
    return {
        "ok": True,
        "lot_id": lot["id"],
        "position_id": pos["id"],
        "funding_account_id": change.get("funding_account_id") if will_fund else None,
        "cash_leg_id": cash_leg_id if will_fund else None,
        **agg,
    }


def preview_disposal(change: dict) -> dict:
    """PURE preview of a disposal (NO WRITES) — the realized-gain DRAFT the approve-tier
    propose flow shows the user before they confirm. Loads the position + its
    open lots + FX, then runs `compute_disposal` (which validates and raises on a bad selection / oversell BEFORE
    returning, so the preview surfaces the same error the apply would).

    Used by the AI `propose_lot_dispose` tool and (optionally) a REST dry-run; the actual
    write still goes through `apply_lot_change`. All gain math stays here — the caller only
    shapes the returned draft into a confirm card.

    Returns the RealizedEvent-shaped draft, or {"error": ...} if the position is unknown.
    """
    pos = PositionStore().find_by_id(change.get("position_id", ""))
    if not pos:
        return {"error": "position not found"}
    change = {**change, "transfer_to_account_id": resolve_transfer_destination(change, pos)}
    rates = _load_rates()
    return compute_disposal(pos, LotStore().open_lots_for_position(pos["id"]), change, rates=rates)


_CASH_ACCOUNT_FIELDS = ("funding_account_id", "proceeds_account_id")


def resolve_cash_account_refs(change: dict) -> dict:
    """Turn ``funding_account_id`` / ``proceeds_account_id`` — an account id OR its exact
    name — into the account's id before any lot path reads them. Every one of those paths
    STORES the field (on the lot, on the realized event) and looks the account up by id (the
    broker payee), while the cash leg's ledger write accepts a name; without this a valid
    name booked the cash correctly but persisted the raw name as an "id" and dropped the
    payee (found live 2026-10-13: an automation passed "IBKR (USD)"). RAISES
    ``ValidationError`` for an unknown or ambiguous ref, so an id field only ever holds an
    id. Returns ``change`` untouched when neither field is set."""
    pending = [f for f in _CASH_ACCOUNT_FIELDS if change.get(f)]
    if not pending:
        return change
    from backend.services.accounts import all_account_rows, require_account_id

    rows = all_account_rows()
    resolved = dict(change)
    for field in pending:
        resolved[field] = require_account_id(change[field], field=field, rows=rows)
    return resolved


def resolve_transfer_destination(change: dict, pos: dict) -> Optional[str]:
    """The store-backed pre-step for a share move: turn ``transfer_to_account_id`` (an
    account id OR its exact name, the same ladder every account ref uses) into the id the
    draft carries, or None when the disposal names no destination. RAISES
    ``ValidationError`` for an unknown or ambiguous ref and for the position's own account —
    "moved to where it already is" is always a mistaken pick. Runs before
    ``compute_disposal`` in both the preview and the apply path, so the confirm card and the
    write agree on the resolved account; it does NOT touch any store."""
    ref = change.get("transfer_to_account_id") or None
    if not ref:
        return None
    from backend.core.errors import ValidationError, recovery_hint
    from backend.services.accounts import all_account_rows, resolve_account_ref

    rows = all_account_rows()
    dest_id = resolve_account_ref(str(ref), rows=rows)
    if not dest_id:
        raise ValidationError(
            f"Unknown account {ref!r} for transfer_to_account_id."
            + recovery_hint(
                str(ref), (a.get("name") or "" for a in rows), tool_hint="list_accounts"
            ),
            transfer_to_account_id=ref,
        )
    if dest_id == pos.get("account_id"):
        raise ValidationError(
            f"transfer_to_account_id {ref!r} already holds this position — a transfer "
            "moves shares to a DIFFERENT account",
            transfer_to_account_id=dest_id,
        )
    return dest_id


def _apply_dispose(change: dict) -> dict:
    events = RealizedEventStore()
    idempotency_key = change.get("idempotency_key")
    if idempotency_key:
        existing = events.find_by_idempotency_key(idempotency_key)
        if existing:  # already applied (re-apply / post-restore) → no-op (AC-16)
            return {
                "ok": True,
                "event_id": existing["id"],
                "position_id": existing.get("position_id"),
                "idempotent": True,
            }

    positions = PositionStore()
    pos = positions.find_by_id(change.get("position_id", ""))
    if not pos:
        return {"error": "position not found"}

    # Duplicate-sale guard (mirrors the acquire-side double-book refusal): a disposal whose
    # (position, date, qty, proceeds/unit, currency) already exists is REFUSED before any
    # write — unless the caller passes allow_duplicate=true (genuinely two separate fills).
    # This is the recurrence fix for the ONWAI double-book; idempotency-key dedup only catches
    # automated retries, not a second manual/AI entry.
    if not change.get("allow_duplicate"):
        dup = _find_duplicate_disposal(pos, change)
        if dup:
            return {
                "error": (
                    f"a disposal of {dup.get('quantity')} units on {dup.get('disposed_at')} @ "
                    f"{dup.get('proceeds_per_unit')} {dup.get('proceeds_currency')} is already "
                    f"recorded on this position (event_id={dup['id']}) — this sale looks already "
                    "booked; do NOT book it again. Only if these are genuinely two separate "
                    "fills, re-apply with allow_duplicate=true."
                ),
                "existing_event_id": dup["id"],
                "position_id": pos["id"],
            }

    lots = LotStore()
    open_lots = lots.open_lots_for_position(pos["id"])
    rates = _load_rates()
    # A share move names its destination by id or name; resolve it (raises on unknown /
    # same-account) before the draft so nothing below sees an unresolved ref.
    change = {**change, "transfer_to_account_id": resolve_transfer_destination(change, pos)}
    # compute_disposal validates + raises BEFORE we touch any store (AC-7, AC-13).
    draft = compute_disposal(pos, open_lots, change, rates=rates)
    # PRE-transaction: resolve (or lazily mint) the deposit-leg payee. Payee writes
    # (PayeeStore().create, called by payee_registry when nothing matches) don't accept
    # `conn=` and can't compose into the locked transaction below — calling them from
    # INSIDE it self-contends against the SQLite writer lock the same thread already
    # holds, and payee_registry swallows the resulting exception and returns None
    # silently (found live: a real deposit landed with no payee at all). Kept outside
    # the lock set because it is idempotent and harmless if orphaned by a later rollback.
    payee_id = _resolve_deposit_payee_if_needed(change)
    # TASK 4 (storage plan 4b): lot consumption + the realized event + the proceeds cash
    # leg + the position resync now commit as ONE transaction — a mid-op failure rolls
    # ALL FOUR back, never "lots persists, the realized event never happens". txn_audit is
    # ALSO locked here (beyond the brief's literal 4-table list): create_transaction, when
    # composed via conn=, writes both `transactions` AND `txn_audit` as one unit — omitting
    # txn_audit's lock would let a concurrent solo txn_audit writer race unprotected
    # against this thread's unlocked write. Deadlock-safe: `txn_audit` sorts alphabetically
    # AFTER every other table in this set, matching create_transaction's own pre-existing
    # internal acquisition order, so no ordering inversion is introduced.
    with locked_unit_of_work(
        lots_tbl, positions_tbl, realized_tbl, txns_tbl, txn_audit_tbl
    ) as conn:
        lots.bulk_consume([(c["lot_id"], c["qty"]) for c in draft["consumptions"]], conn=conn)
        event = events.append(
            {
                **draft,
                "txn_id": change.get("txn_id"),
                "proceeds_account_id": change.get("proceeds_account_id") or None,
                "idempotency_key": idempotency_key,
                "source": change.get("source", "manual"),
                "note": change.get("note", ""),
            },
            conn=conn,
        )
        # INV1-2: optionally credit the NET proceeds to a chosen cash account. The lot
        # ledger and the cash ledger are deliberately separate (D3), so selling books no
        # cash by default — which silently sinks net worth. When the user picks a
        # destination account (proceeds_account_id), bridge the gap HERE (the single
        # disposal choke point, shared by REST + AI) by crediting it directly through
        # ledger_service.create_transaction (the normal money path, so it's
        # checkpoint-covered + FX-converted into the destination's native currency).
        # kind=investment (not transfer) keeps the inflow out of income stats without
        # needing a counterparty leg — the position's quantity already dropped to zero
        # above, which is the disposal's real offsetting entry; a synthetic "Market"
        # account transfer used to double up that bookkeeping AND broke the ledger UI
        # whenever it went archived (its `from_account_id` became unresolvable — see
        # docs/adr/2026-08-25-sale-proceeds-are-an-investment-inflow-not-a-transfer.md).
        # Skipped → no leg.
        cash_leg_id = _book_deposit_proceeds(draft, change, payee_id, conn=conn)
        if cash_leg_id:
            event = events.update_by_id(event["id"], {"txn_id": cash_leg_id}, conn=conn) or event
        agg = position_figures.rederive(pos["id"], conn=conn)
    return {
        "ok": True,
        "event_id": event["id"],
        "position_id": pos["id"],
        "realized_gain": draft["realized_gain"],
        "realized_gain_base": draft["realized_gain_base"],
        "proceeds_account_id": change.get("proceeds_account_id") or None,
        "transfer_to_account_id": draft.get("transfer_to_account_id"),
        "cash_leg_id": cash_leg_id,
        **agg,
    }


def _net_proceeds(draft: dict) -> float:
    """Net cash proceeds (quantity × proceeds_per_unit − disposal_fees) in the proceeds
    currency — the shared formula between resolving whether a deposit is needed and
    actually booking it (_book_deposit_proceeds)."""
    qty = float(draft.get("quantity") or 0)
    per_unit = float(draft.get("proceeds_per_unit") or 0)
    fees = float(draft.get("disposal_fees") or 0)
    return round(qty * per_unit - fees, 2)


def _resolve_deposit_payee_if_needed(change: dict) -> Optional[str]:
    """PRE-transaction: resolve — or lazily mint — the payee for a proceeds deposit, or
    None when no destination is chosen or it has no `institution`. Payee is the destination
    account's own venue (e.g. "IBKR") — the same entity every trade on that account
    already uses, so this REUSES it by NAME (``resolve_payee_id_by_name``, not the raw
    ``resolve_payee_id`` alias matcher — a payee's own display name is never
    auto-added as one of its aliases, so alias matching on "IBKR" would miss the
    existing "IBKR" payee and mint a duplicate). No institution on the account → no payee
    (never mint one from the generic "Sale proceeds" description; that would invent
    junk with no default category)."""
    dest_id = change.get("proceeds_account_id")
    if not dest_id:
        return None
    from backend.db.account_store import AccountStore
    from backend.services import payee_registry

    dest_account = AccountStore().find_by_id(dest_id) or {}
    institution = dest_account.get("institution") or None
    if not institution:
        return None
    return payee_registry.resolve_payee_id_by_name(institution, create=True)


def _book_deposit_proceeds(
    draft: dict, change: dict, payee_id: Optional[str], *, conn: Connection | None = None
) -> Optional[str]:
    """The COMPOSED half: credit the NET proceeds to the chosen destination account,
    using the ALREADY-resolved ``payee_id`` (see _resolve_deposit_payee_if_needed) — or
    no-op when no destination was chosen or there is nothing positive to deposit (e.g. a
    transfer-out). ``conn`` threads this into the caller's open transaction (Task 4).
    Returns the new txn id, or None.

    A single-leg ``kind=investment`` inflow — no counterparty account. The disposal's real
    offsetting entry is the position's quantity dropping to zero (already applied above);
    this row's only job is recording that cash arrived. ``payee_id`` is passed as an id,
    not a `payee` name: create_transaction's name→id resolution only runs for kind in
    (expense, income) — investment silently drops a `payee` name unresolved. A lazy
    import of ledger_service keeps this off the module-level import graph — both modules
    are services-tier (allowed to depend on each other under the layered spine), and
    ledger does NOT import lots, so there is no cycle (the same lazy-import pattern as
    _load_rates → services.fx_cache).
    """
    dest_id = change.get("proceeds_account_id")
    if not dest_id:
        return None
    net_proceeds = _net_proceeds(draft)
    if net_proceeds <= 0:
        return None  # nothing to deposit (e.g. a transfer-out with no proceeds)
    proceeds_ccy = (draft.get("proceeds_currency") or "").upper()

    from backend.services import ledger as ledger_service

    txn_data = {
        "date": draft.get("disposed_at") or user_today().isoformat(),
        "kind": "investment",
        "description": "Sale proceeds",
        "to_account_id": dest_id,
        "to_amount": net_proceeds,
        "to_currency": proceeds_ccy,
        "source": change.get("source", "manual"),
    }
    if payee_id:
        txn_data["payee_id"] = payee_id
    txn_data.update(
        _traded(
            draft.get("instrument_id"),
            draft.get("quantity"),
            verb="Sold",
            price=draft.get("proceeds_per_unit"),
            currency=proceeds_ccy,
            fees=draft.get("disposal_fees"),
        )
    )

    txn = ledger_service.create_transaction(
        txn_data, allow_duplicate=bool(change.get("allow_duplicate")), conn=conn
    )
    return txn.get("id")


_AMEND_IDENTITY_KEYS = (
    "quantity",
    "unit_cost",
    "fees",
    "acquired_at",
    "kind",
    "cost_currency",
    "funding_account_id",
)


def _lot_is_partial(lot: dict) -> bool:
    original = float(lot.get("original_qty") or 0)
    remaining = float(lot.get("remaining_qty") or 0)
    return original > 0 and remaining + _QTY_EPS < original


def _funding_account_from_txn(txn_id: str | None) -> Optional[str]:
    if not txn_id:
        return None
    from backend.db.transaction_store import TransactionStore

    txn = TransactionStore().find_by_id(txn_id) or {}
    return txn.get("from_account_id")


def _apply_amend(change: dict) -> dict:
    """Patch a fully-open lot (or any lot's note). Identity edits on a partially
    consumed lot are refused — those facts are frozen on realized consumptions."""
    lot_id = change.get("lot_id") or ""
    lots = LotStore()
    lot = lots.find_by_id(lot_id)
    if not lot:
        return {"error": "lot not found"}
    future = _future_acquisition(change.get("acquired_at"))
    if future:
        return {"error": future}
    change = _real_changes(lot, change)
    if _lot_is_partial(lot):
        return _amend_sold_lot(lot, change)
    return _amend_open_lot(lot, change)


def _lot_value(lot: dict, key: str):
    if key == "quantity":
        return lot.get("original_qty")
    if key == "funding_account_id":
        return _funding_account_from_txn(lot.get("txn_id"))
    return lot.get(key)


def _same_value(key: str, new, old) -> bool:
    if key in ("quantity", "unit_cost", "fees"):
        if key == "fees":
            new, old = new or 0, old or 0
        if new is None or old is None:
            return new is None and old is None
        try:
            return abs(float(new) - float(old)) <= _QTY_EPS
        except (TypeError, ValueError):
            return False
    if key == "cost_currency":
        return (new or "").upper() == (old or "").upper()
    return (new or None) == (old or None)


def _real_changes(lot: dict, change: dict) -> dict:
    """The amend with every field whose value equals the lot's current one removed.

    The edit form resends every field on every save, and a model may repeat an unchanged
    one; treating "present" as "changed" made a note save restamp the lot's acquire rate and
    recompute a sold lot's sales at today's rate (review, 2026-10-13). Only a real change
    reaches the cash rewrite, the rate restamp, or a sale."""
    return {
        k: v
        for k, v in change.items()
        if k not in _AMENDABLE_KEYS or not _same_value(k, v, _lot_value(lot, k))
    }


# What a lot's funding debit is made of — only a real change to one of these rewrites the
# linked purchase transaction. Rewriting it on every edit (a note included) deleted and
# re-booked the cash leg, dropping its category, cleared and reconciled state (2026-10-13).
_CASH_KEYS = ("quantity", "unit_cost", "fees", "acquired_at", "cost_currency", "funding_account_id")
_AMENDABLE_KEYS = (*_CASH_KEYS, "kind", "note")


def _cash_changed(lot: dict, change: dict) -> bool:
    """True when a cash-bearing field really changed (``change`` is ``_real_changes``-filtered;
    re-filtering keeps the answer right for a caller that didn't)."""
    return any(k in _CASH_KEYS for k in _real_changes(lot, change))


def preview_sold_lot_amend(lot: dict, change: dict) -> list[dict]:
    """The sales a correction to this lot would update — [] for a lot nothing has sold from.
    PURE: the confirm card shows exactly what ``_amend_sold_lot`` will write."""
    if not _lot_is_partial(lot):
        return []
    from backend.services import lot_corrections

    events = RealizedEventStore().query_events(position_id=lot.get("position_id") or "")
    plan = lot_corrections.plan_sold_lot_amend(
        lot, _real_changes(lot, change), events, base=base_currency().upper(), rates=_load_rates()
    )
    return plan.get("sales") or []


def _amend_sold_lot(lot: dict, change: dict) -> dict:
    """Correct a lot a sale has used — see services/lot_corrections.py. The sale keeps the
    lots and units it took; its lines for THIS lot (and its gain, when cost changed) follow."""
    from backend.services import lot_corrections

    events = RealizedEventStore().query_events(position_id=lot.get("position_id") or "")
    plan = lot_corrections.plan_sold_lot_amend(
        lot, change, events, base=base_currency().upper(), rates=_load_rates()
    )
    if plan.get("error"):
        return {"error": plan["error"]}
    funding = (
        change["funding_account_id"]
        if "funding_account_id" in change
        else _funding_account_from_txn(lot.get("txn_id"))
    )
    # A linked purchase is rewritten when its figures change; a sold lot with none gets one
    # when the user now picks "Deduct from" (the open-lot path does the same).
    rewrite_cash = _cash_changed(lot, change) and bool(lot.get("txn_id") or funding)
    # PRE-transaction, like the open path: resolving may create the payee on its own
    # connection, which would wait on this unit of work's write lock (review, 2026-10-13).
    payee_id = (
        _resolve_deposit_payee_if_needed({"proceeds_account_id": funding}) if rewrite_cash else None
    )
    with locked_unit_of_work(
        lots_tbl, positions_tbl, realized_tbl, txns_tbl, txn_audit_tbl
    ) as conn:
        patched = LotStore().touch(lot["id"], plan["lot_updates"], conn=conn) or lot
        for event_id, patch in plan["event_updates"].items():
            RealizedEventStore().update_by_id(event_id, patch, conn=conn)
        if rewrite_cash:
            _rewrite_funding_debit(lot, patched, change, funding, payee_id, conn=conn)
        agg = position_figures.rederive(lot.get("position_id", ""), conn=conn)
    return {
        "ok": True,
        "lot_id": lot["id"],
        "position_id": lot.get("position_id"),
        "sales_updated": plan["sales"],
        **agg,
    }


def _rewrite_funding_debit(
    lot: dict,
    patched: dict,
    change: dict,
    funding: Optional[str],
    payee_id: Optional[str],
    *,
    conn: Connection,
) -> None:
    """Re-book a corrected lot's purchase cash so it matches the lot's new figures."""
    from backend.services import ledger as ledger_service

    fund_change = {
        **change,
        "quantity": patched.get("original_qty"),
        "unit_cost": patched.get("unit_cost"),
        "fees": patched.get("fees", 0),
        "acquired_at": patched.get("acquired_at") or "",
        "cost_currency": patched.get("cost_currency"),
        "funding_account_id": funding,
        "source": change.get("source", "manual"),
    }
    if lot.get("txn_id"):
        ledger_service.bulk_delete_transactions([lot["txn_id"]], conn=conn, reject_identities=False)
        LotStore().touch(lot["id"], {"txn_id": None}, conn=conn)
    if not _will_fund_acquire(fund_change):
        return
    cash_leg_id = _book_funding_debit(fund_change, patched, payee_id, conn=conn)
    if cash_leg_id:
        LotStore().touch(lot["id"], {"txn_id": cash_leg_id}, conn=conn)


def _amend_open_lot(lot: dict, change: dict) -> dict:
    updates: dict = {}
    if "quantity" in change:
        qty = float(change.get("quantity") or 0)
        if not math.isfinite(qty) or qty <= 0:
            return {"error": "quantity must be a positive number"}
        updates["original_qty"] = qty
        updates["remaining_qty"] = qty
    for key in ("unit_cost", "fees", "acquired_at", "kind", "cost_currency", "note"):
        if key in change:
            updates[key] = change[key]
    if "unit_cost" in updates or "cost_currency" in updates:
        base = base_currency().upper()
        cost_ccy = (updates.get("cost_currency") or lot.get("cost_currency") or base).upper()
        updates["fx_rate"] = _rate_between(cost_ccy, base, _load_rates())
        updates["base_currency"] = base
    funding = (
        change["funding_account_id"]
        if "funding_account_id" in change
        else _funding_account_from_txn(lot.get("txn_id"))
    )
    fund_change = {
        **change,
        "quantity": updates.get("original_qty", lot.get("original_qty")),
        "unit_cost": updates["unit_cost"] if "unit_cost" in updates else lot.get("unit_cost"),
        "fees": updates["fees"] if "fees" in updates else lot.get("fees", 0),
        "acquired_at": updates.get("acquired_at", lot.get("acquired_at") or ""),
        "cost_currency": updates.get("cost_currency", lot.get("cost_currency")),
        "funding_account_id": funding,
        "source": change.get("source", "manual"),
    }
    if not _cash_changed(lot, change):
        with locked_unit_of_work(lots_tbl, positions_tbl) as conn:
            LotStore().touch(lot["id"], updates, conn=conn)
            agg = position_figures.rederive(lot.get("position_id", ""), conn=conn)
        return {
            "ok": True,
            "lot_id": lot["id"],
            "position_id": lot.get("position_id"),
            "funding_account_id": funding,
            "cash_leg_id": lot.get("txn_id"),
            **agg,
        }
    will_fund = _will_fund_acquire(fund_change)
    payee_id = (
        _resolve_deposit_payee_if_needed({"proceeds_account_id": funding}) if will_fund else None
    )
    tables = [lots_tbl, positions_tbl, txns_tbl, txn_audit_tbl]
    with locked_unit_of_work(*tables) as conn:
        if lot.get("txn_id"):
            from backend.services import ledger as ledger_service

            ledger_service.bulk_delete_transactions(
                [lot["txn_id"]], conn=conn, reject_identities=False
            )
            updates["txn_id"] = None
        patched = LotStore().touch(lot["id"], updates, conn=conn) or lot
        cash_leg_id = _book_funding_debit(fund_change, patched, payee_id, conn=conn)
        if cash_leg_id:
            patched = LotStore().touch(lot["id"], {"txn_id": cash_leg_id}, conn=conn) or patched
        agg = position_figures.rederive(lot.get("position_id", ""), conn=conn)
    return {
        "ok": True,
        "lot_id": lot["id"],
        "position_id": lot.get("position_id"),
        "funding_account_id": funding if will_fund else None,
        "cash_leg_id": cash_leg_id if will_fund else None,
        **agg,
    }


def _apply_dispose_amend(change: dict) -> dict:
    """Undo a disposal then rebook it in ONE unit of work. A failed new disposal
    rolls the restore back too."""
    event_id = change.get("event_id") or ""
    ev = RealizedEventStore().find_by_id(event_id)
    if not ev:
        return {"error": "disposal event not found"}
    pos = PositionStore().find_by_id(ev.get("position_id") or "")
    if not pos:
        return {"error": "position not found"}
    lots = LotStore()
    consumptions = ev.get("consumptions") or []
    missing = [c.get("lot_id") for c in consumptions if not lots.find_by_id(c.get("lot_id", ""))]
    if missing:
        return {
            "error": (
                "cannot amend — one or more consumed lots no longer exist "
                f"({missing}); the shares can't be restored automatically"
            )
        }
    new_change = {
        k: v for k, v in change.items() if k not in ("type", "event_id", "idempotency_key")
    }
    new_change["position_id"] = pos["id"]
    new_change["source"] = change.get("source", "manual")
    if not new_change.get("allow_duplicate"):
        dup = _find_duplicate_disposal(pos, new_change, exclude_event_id=event_id)
        if dup:
            return {
                "error": (
                    f"a disposal of {dup.get('quantity')} units on {dup.get('disposed_at')} @ "
                    f"{dup.get('proceeds_per_unit')} {dup.get('proceeds_currency')} is already "
                    f"recorded on this position (event_id={dup['id']}) — this sale looks already "
                    "booked; do NOT book it again. Only if these are genuinely two separate "
                    "fills, re-apply with allow_duplicate=true."
                ),
                "existing_event_id": dup["id"],
                "position_id": pos["id"],
            }
    new_change = {
        **new_change,
        "transfer_to_account_id": resolve_transfer_destination(new_change, pos),
    }
    payee_id = _resolve_deposit_payee_if_needed(new_change)
    rates = _load_rates()
    with locked_unit_of_work(
        lots_tbl, positions_tbl, realized_tbl, txns_tbl, txn_audit_tbl
    ) as conn:
        _restore_disposal(ev, conn=conn)
        open_lots = lots.open_lots_for_position(pos["id"], conn=conn)
        draft = compute_disposal(pos, open_lots, new_change, rates=rates)
        lots.bulk_consume([(c["lot_id"], c["qty"]) for c in draft["consumptions"]], conn=conn)
        event = RealizedEventStore().append(
            {
                **draft,
                "proceeds_account_id": new_change.get("proceeds_account_id") or None,
                "source": new_change.get("source", "manual"),
                "note": new_change.get("note", ""),
            },
            conn=conn,
        )
        cash_leg_id = _book_deposit_proceeds(draft, new_change, payee_id, conn=conn)
        if cash_leg_id:
            event = (
                RealizedEventStore().update_by_id(event["id"], {"txn_id": cash_leg_id}, conn=conn)
                or event
            )
        agg = position_figures.rederive(pos["id"], conn=conn)
    return {
        "ok": True,
        "event_id": event["id"],
        "position_id": pos["id"],
        "realized_gain": draft["realized_gain"],
        "realized_gain_base": draft["realized_gain_base"],
        "proceeds_account_id": new_change.get("proceeds_account_id") or None,
        "transfer_to_account_id": draft.get("transfer_to_account_id"),
        "cash_leg_id": cash_leg_id,
        **agg,
    }


# ── Reads ────────────────────────────────────────────────────────────────────────


def lots_for_position(
    position_id: str, threshold_days: int = DEFAULT_TERM_THRESHOLD_DAYS
) -> list[dict]:
    """Open + closed lots for a position, FIFO-ordered, each annotated with its
    effective unit cost, remaining basis, and term-to-date (long/short as of today).

    Pure read. Every holding has its lot from the moment it is added (ADR
    2026-10-07-a-position-quantity-is-derived-from-its-records), so nothing is seeded here."""
    today = user_today().isoformat()
    out: list[dict] = []
    for lot in LotStore().lots_for_position(position_id, include_closed=True):
        eff = effective_unit_cost(lot)
        rq = float(lot.get("remaining_qty") or 0)
        out.append(
            {
                **lot,
                "effective_unit_cost": eff,
                "remaining_basis": round(rq * eff, 2) if eff is not None else None,
                "term_to_date": classify_term(lot.get("acquired_at"), today, threshold_days),
            }
        )
    return out


def position_remaining_basis(position_id: str) -> float:
    """Σ over open lots of remaining_qty × effective unit cost, for cost-KNOWN lots only
    (AC-18). Cost-unknown lots contribute 0 (they have no basis to report). Rounded 2dp."""
    total = 0.0
    for lot in LotStore().open_lots_for_position(position_id):
        eff = effective_unit_cost(lot)
        if eff is None:
            continue
        total += float(lot.get("remaining_qty") or 0) * eff
    return round(total, 2)


def realized_pnl(
    date_from: str | None = None,
    date_to: str | None = None,
    instrument_id: str | None = None,
    account_id: str | None = None,
    position_id: str | None = None,
    base: str | None = None,
    with_predecessors: bool = False,
) -> dict:
    """Realized gains over a date range / instrument / account / position. Walks the
    realized-event ledger. Each event's base figure follows the historical-rate lock
    (ADR 2026-07-15, mirroring ledger.txn_base_amount): the stored realized_gain_base is
    TRUSTED while the event's base_currency still matches the current base — a completed
    sale's base gain is a historical fact (proceeds at the disposal-date rate minus basis
    at each lot's acquisition-date rate), not revalued as FX drifts. Only on a
    base-currency change or a legacy/unstamped event is the base figure recomputed from
    the native gain at current rates. Returns {events, realized_total_base, by_term, by_instrument};
    each event is hydrated with instrument_name/instrument_symbol (joined here so a
    fully-sold holding — which has left the live portfolio — still labels by name, not its
    raw UUID).

    `with_predecessors` widens a `position_id` read to the holding's sold-out predecessors
    (`position_lineage.history_position_ids`) — a sell-out then rebuy keeps its sale.

    AC-14: an empty ledger returns [] + realized_total_base 0.0 (no crash / div-by-zero).
    """
    from backend.services.position_lineage import history_position_ids

    base = (base or base_currency()).upper()
    rates = _load_rates()
    lineage = history_position_ids(position_id) if with_predecessors and position_id else None
    events = RealizedEventStore().query_events(
        date_from=date_from,
        date_to=date_to,
        instrument_id=instrument_id,
        account_id=account_id,
        position_id=None if lineage else position_id,
    )
    if lineage:
        events = [ev for ev in events if ev.get("position_id") in lineage]

    # Hydrate instrument name/symbol HERE so the read is self-sufficient: a fully-sold
    # holding (qty→0) drops out of the live portfolio, so the UI can no longer resolve its
    # instrument_id from held rows and would otherwise show the raw UUID. Join once.
    inst_by_id = {i.get("id"): i for i in instruments.all()}

    def _names(iid: str) -> tuple[str, str]:
        inst = inst_by_id.get(iid) or {}
        symbol = inst.get("symbol") or ""
        name = inst.get("name") or symbol or ""
        return name, symbol

    out_events: list[dict] = []
    total_base = 0.0
    by_term: dict[str, float] = {"long": 0.0, "short": 0.0, "unknown": 0.0}
    by_instrument: dict[str, float] = {}

    for ev in events:
        gain = ev.get("realized_gain")
        proceeds_ccy = (ev.get("proceeds_currency") or base).upper()
        iid = ev.get("instrument_id") or ""
        inst_name, inst_symbol = _names(iid)
        if gain is None:
            gain_base: Optional[float] = None
        else:
            stored_base = ev.get("realized_gain_base")
            if stored_base is not None and (ev.get("base_currency") or "").upper() == base:
                gain_base = float(stored_base)  # historical-rate lock: trust the stamp
            else:
                gain_base = to_base(float(gain), proceeds_ccy, base, rates=rates)
            total_base += gain_base
            by_instrument[iid] = round(by_instrument.get(iid, 0.0) + gain_base, 2)
            # Split base gain across the event's per-line terms by their qty share.
            _accumulate_terms(ev, gain_base, by_term)
        out_events.append(
            {
                **ev,
                "realized_gain_base": gain_base,
                "instrument_name": inst_name,
                "instrument_symbol": inst_symbol,
            }
        )

    return {
        "base_currency": base,
        "events": out_events,
        "realized_total_base": round(total_base, 2),
        "by_term": {k: round(v, 2) for k, v in by_term.items()},
        "by_instrument": by_instrument,
    }


def fees_summary(
    date_from: str | None = None,
    date_to: str | None = None,
    instrument_id: str | None = None,
    account_id: str | None = None,
    base: str | None = None,
) -> dict:
    """Informational total of trading costs already paid: acquisition fees (Σ lot `fees`,
    in each lot's cost_currency) + disposal fees (Σ realized-event `disposal_fees`, in each
    event's proceeds_currency), each normalized to the base currency via current FX
    (RECOMPUTED from the native fee, never a stored base snapshot — mirrors realized_pnl /
    txn_base_amount, so a base-currency change can't stale the report).

    IMPORTANT — these fees are presented as "fees paid", NOT subtracted from gains again:
    acquisition fees already amortize into cost basis (effective_unit_cost) and disposal
    fees already reduce proceeds in compute_disposal/realized_pnl. This summary is an
    INDEPENDENT informational tally; double-subtracting would mis-state P&L (RFC §8).

    Filters mirror realized_pnl (date range / instrument / account). For acquisition fees a
    lot's disposal date is irrelevant — a lot's acquired_at is used for the date filter (so a
    fee paid on a buy outside the window is excluded). Returns
    {base_currency, acquisition_fees_base, disposal_fees_base, total_fees_base, by_instrument}.

    AC: an empty book returns all-zeros (no div-by-zero / crash).
    """
    base = (base or base_currency()).upper()
    rates = _load_rates()

    acq_total = 0.0
    disp_total = 0.0
    by_instrument: dict[str, dict] = {}
    # Per-account (≈ per-broker) rollup with traded NOTIONAL, so the fee RATE per venue
    # is comparable — the seed of "you pay 0.4% at X vs 0.05% at Y" insights (fee-capture
    # sweep 2026-07-13). Notional counts EVERY in-window trade whose value is known (fee
    # or not); fee_rate_pct = fees / notional. A venue with fees but unknown notional
    # reads rate None, never a fabricated figure.
    by_account: dict[str, dict] = {}

    def _bucket(iid: str) -> dict:
        return by_instrument.setdefault(
            iid, {"acquisition_fees_base": 0.0, "disposal_fees_base": 0.0, "total_fees_base": 0.0}
        )

    def _acct(aid: str) -> dict:
        return by_account.setdefault(
            aid, {"fees_base": 0.0, "notional_base": 0.0, "trade_count": 0}
        )

    # Acquisition side — walk all lots (open + closed; a closed lot still cost a fee).
    for lot in LotStore().all():
        if instrument_id is not None and lot.get("instrument_id") != instrument_id:
            continue
        if account_id is not None and lot.get("account_id") != account_id:
            continue
        acq_at = lot.get("acquired_at") or ""
        if date_from is not None and (not acq_at or acq_at < date_from):
            continue
        if date_to is not None and (not acq_at or acq_at > date_to):
            continue
        ab = _acct(lot.get("account_id") or "")
        ab["trade_count"] += 1
        if lot.get("unit_cost") is not None:
            notional = float(lot.get("original_qty") or 0) * float(lot.get("unit_cost") or 0)
            ab["notional_base"] = round(
                ab["notional_base"]
                + to_base(notional, lot.get("cost_currency") or base, base, rates=rates),
                2,
            )
        fee = float(lot.get("fees", 0) or 0)
        if not fee:
            continue
        fee_base = to_base(fee, lot.get("cost_currency") or base, base, rates=rates)
        acq_total += fee_base
        ab["fees_base"] = round(ab["fees_base"] + fee_base, 2)
        b = _bucket(lot.get("instrument_id") or "")
        b["acquisition_fees_base"] = round(b["acquisition_fees_base"] + fee_base, 2)

    # Disposal side — walk the realized-event ledger (date filter = disposed_at, handled by query).
    events = RealizedEventStore().query_events(
        date_from=date_from,
        date_to=date_to,
        instrument_id=instrument_id,
        account_id=account_id,
    )
    for ev in events:
        ab = _acct(ev.get("account_id") or "")
        ab["trade_count"] += 1
        if ev.get("proceeds_per_unit") is not None:
            notional = float(ev.get("quantity") or 0) * float(ev.get("proceeds_per_unit") or 0)
            ab["notional_base"] = round(
                ab["notional_base"]
                + to_base(
                    notional, (ev.get("proceeds_currency") or base).upper(), base, rates=rates
                ),
                2,
            )
        fee = float(ev.get("disposal_fees", 0) or 0)
        if not fee:
            continue
        fee_base = to_base(fee, (ev.get("proceeds_currency") or base).upper(), base, rates=rates)
        disp_total += fee_base
        ab["fees_base"] = round(ab["fees_base"] + fee_base, 2)
        b = _bucket(ev.get("instrument_id") or "")
        b["disposal_fees_base"] = round(b["disposal_fees_base"] + fee_base, 2)

    for b in by_instrument.values():
        b["total_fees_base"] = round(b["acquisition_fees_base"] + b["disposal_fees_base"], 2)

    # Name the venues + derive the comparable rate.
    from backend.db.account_store import AccountStore

    acct_idx = {a["id"]: a for a in AccountStore().all()}
    for aid, ab in by_account.items():
        acct = acct_idx.get(aid) or {}
        ab["account_name"] = acct.get("name") or aid or "(unknown)"
        ab["institution"] = acct.get("institution") or ""
        ab["fee_rate_pct"] = (
            round(ab["fees_base"] / ab["notional_base"] * 100, 4)
            if ab["notional_base"] > 0
            else None
        )

    return {
        "base_currency": base,
        "acquisition_fees_base": round(acq_total, 2),
        "disposal_fees_base": round(disp_total, 2),
        "total_fees_base": round(acq_total + disp_total, 2),
        "by_instrument": by_instrument,
        "by_account": by_account,
    }


def _accumulate_terms(event: dict, gain_base: float, by_term: dict[str, float]) -> None:
    """Apportion an event's base gain across its consumed-lot terms.

    The per-line `realized_gain` is in the proceeds currency; we split the already
    base-converted aggregate gain by each line's share of the total line gain (falling
    back to a qty share if line gains are unavailable) so the long/short totals stay
    consistent with `realized_total_base`.
    """
    lines = event.get("consumptions") or []
    line_gains = [c.get("realized_gain") for c in lines]
    if lines and all(g is not None for g in line_gains):
        denom = sum(line_gains)
        if denom:
            for c in lines:
                share = c["realized_gain"] / denom
                by_term[c.get("term", "unknown")] = (
                    by_term.get(c.get("term", "unknown"), 0.0) + gain_base * share
                )
            return
    # Fallback: split by qty share (e.g. all line gains equal/zero).
    total_qty = sum(float(c.get("qty") or 0) for c in lines)
    if total_qty:
        for c in lines:
            share = float(c.get("qty") or 0) / total_qty
            by_term[c.get("term", "unknown")] = (
                by_term.get(c.get("term", "unknown"), 0.0) + gain_base * share
            )
    else:
        by_term["unknown"] = by_term.get("unknown", 0.0) + gain_base


def _load_rates() -> dict:
    """Current cached FX rates (one read), or the pivot-only fallback. Routed through
    services.fx_cache so core.fx stays leaf-pure and we don't up-import api/."""
    try:
        from backend.services.fx_cache import load_fx_cache

        return load_fx_cache()
    except Exception:  # pragma: no cover - defensive; to_base falls back to 1:1
        return {}


# Convenience handle so tests/callers monkeypatch a single module surface, mirroring
# ledger_service's module-level store instances.
instruments = InstrumentStore()

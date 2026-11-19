"""Lot math — the pure half of tax lots: selection (FIFO, specific-ID), effective cost, holding
term and realised gain. No store, no write: the real book's ``services.lots`` and the paper book
(``services.paper_trading.book``) both fold lots with these same functions, so a paper sale and
a real one can never compute a gain differently. ``services.lots`` re-exports every name here, so
its callers are untouched.
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Optional

from backend.core.errors import InsufficientLots, InvalidLotSelection
from backend.core.fx import base_currency, to_base
from backend.core.user_time import user_today

# Lots may be split/consumed at floating-point granularity; treat sub-dust as zero so a
# fully-consumed lot closes cleanly instead of leaving 1e-12 behind.
_QTY_EPS = 1e-9

# Default holding-period threshold (days). Long ≥ threshold, short below. Jurisdiction
# rules (SG = no CGT, US 1yr, AU 12mo + discount) are out of scope (P4-TX1) — the
# boundary is this single configurable threshold + a per-line `term` string (D7).
DEFAULT_TERM_THRESHOLD_DAYS = 365


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
    *,
    today: Callable[[], date] | None = None,
    load_rates: Callable[[], dict] | None = None,
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
    disposed_at = disposal.get("disposed_at") or (today or user_today)().isoformat()
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
        fx_rate = _fx_rate_only(proceeds_ccy, base, rates, load_rates)
    else:
        realized_gain = None
        realized_gain_base = None
        fx_rate = _fx_rate_only(proceeds_ccy, base, rates, load_rates)

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


def _rate_between(
    currency: str, base: str, rates: dict | None, load_rates: Callable[[], dict] | None = None
) -> Optional[float]:
    """Exact currency→base multiplier from the rates table. Deliberately NOT via
    to_base(1.0, …) — to_base rounds AMOUNTS to 2dp, which is far too coarse for a
    per-unit rate (JPY→USD ≈ 0.0074 would collapse to 0.01). None when a leg is
    missing from the table."""
    cur = (currency or "").upper()
    b = (base or "").upper()
    if cur == b:
        return 1.0
    if rates is None:
        rates = (load_rates or _load_rates)()
    cur_rate = rates.get(cur)
    base_rate = rates.get(b)
    if not cur_rate or not base_rate:
        return None
    return cur_rate / base_rate


def _fx_rate_only(
    currency: str, base: str, rates: dict | None, load_rates: Callable[[], dict] | None = None
) -> Optional[float]:
    """The proceeds→base FX rate to snapshot on an event when the gain is 0 or unknown
    (so we still record the rate in effect). None if the rate can't be resolved."""
    rate = _rate_between(currency, base, rates, load_rates)
    return round(rate, 6) if rate else None


def _load_rates() -> dict:
    """Current cached FX rates (one read), or {} — the fallback when a caller passed no table."""
    try:
        from backend.services.fx_cache import load_fx_cache

        return load_fx_cache()
    except Exception:  # pragma: no cover - defensive; to_base falls back to 1:1
        return {}

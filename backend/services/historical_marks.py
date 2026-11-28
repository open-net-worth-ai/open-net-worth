"""What a holding is worth on a past day it has no recorded price for.

A historical value series looks each holding's price up in the recorded price history
(``PriceHistoryStore.as_of_maps``, keyed by ``price_source_id``). Two kinds of holding have
no price there on some days, and used to be valued at ZERO on them (issue #231):

  * a manually priced instrument has no ``price_source_id`` at all, so it has no history;
  * a market instrument bought before its feed's history begins (a fund Yahoo lists only
    from a later date) has no price before that first point.

A zero there is wrong twice: the holding is missing from the value, and the day its first
price arrives reads as a gain the size of the whole position, while its purchase read as a
loss bigger than the portfolio (a sub-period return below -100%, the /api/returns 500).

The ONE rule, in order: the recorded price on or before the day (unchanged), else what the
units held on that day cost — or, for a manually priced holding from its LATEST purchase
on, its manual mark. Carrying the cost is what makes a purchase value-neutral before prices
exist, so a buy is never a loss and the feed's first day only carries the real move from
cost. A manual mark is today's value (past marks are not stored), so applying it before the
last purchase would read every later buy at cost-versus-today's-mark as a gain or loss;
from the last purchase on, nothing is bought at it. Callers report which holdings were
valued this way.

Pure: no store reads; the caller passes the lots it already loaded.
"""

from __future__ import annotations

from backend.core.fx import to_base
from backend.services.lot_math import effective_unit_cost

# Where a historical value came from, for data-quality reporting.
SOURCE_PRICE = "price"
SOURCE_MANUAL = "manual"
SOURCE_COST = "cost"

# instrument_id -> [(date, signed qty, effective unit cost, cost currency)], date-sorted:
# +qty when a lot (or a parked vest) starts being held, -qty when a sale consumes it — so the
# entries up to a day are exactly the units still held then, each at its own cost.
CostIndex = dict[str, list[tuple[str, float, float, str]]]


def manual_mark(inst: dict | None) -> float | None:
    """The instrument's manual mark, when it is priced by hand (or has no feed to price
    it): ``manual_price`` > 0, for a ``price_source`` of ``manual`` or no
    ``price_source_id``. None otherwise — a feed-priced instrument's stale manual field
    must not override its cost before its feed's first point."""
    if not inst:
        return None
    manual = (inst.get("price_source") or "").lower() == "manual"
    if not (manual or not inst.get("price_source_id")):
        return None
    try:
        mark = float(inst.get("manual_price") or 0)
    except (TypeError, ValueError):
        return None
    return mark if mark > 0 else None


def cost_index(
    lots: list[dict],
    *,
    inception: str | None,
    locked_position_ids: frozenset[str] = frozenset(),
    disposals: list[dict] = (),
    parked: list[dict] = (),
) -> CostIndex:
    """Every costed unit per instrument, on the dates it is held: a lot from its
    ``acquired_at`` (or ``inception`` for an undated opening lot — the same pinning as
    ``portfolio_history.holdings_series``), minus what each sale consumed from it on its
    ``disposed_at``; a parked vest (``portfolio_history.parked_vests``) at its vest-date
    FMV, as its future lot will be. Lots of unvested positions are skipped."""
    out: CostIndex = {}
    by_lot: dict[str, tuple[str, float, str]] = {}
    for lot in lots:
        if lot.get("position_id") in locked_position_ids:
            continue
        iid = lot.get("instrument_id") or ""
        qty = float(lot.get("original_qty") or 0)
        eff = effective_unit_cost(lot)
        when = (lot.get("acquired_at") or "")[:10] or inception
        if not iid or qty <= 0 or eff is None or not when:
            continue
        ccy = lot.get("cost_currency") or ""
        out.setdefault(iid, []).append((when, qty, eff, ccy))
        if lot.get("id"):
            by_lot[lot["id"]] = (iid, eff, ccy)
    for ev in disposals:
        when = (ev.get("disposed_at") or "")[:10]
        for used in ev.get("consumptions") or ():
            held = by_lot.get(used.get("lot_id") or "")
            qty = float(used.get("qty") or 0)
            if held and when and qty > 0:
                iid, eff, ccy = held
                out[iid].append((when, -qty, eff, ccy))
    for vest in parked:
        fmv = vest.get("fmv_per_unit")
        if vest.get("instrument_id") and vest.get("date") and fmv:
            out.setdefault(vest["instrument_id"], []).append(
                (vest["date"], float(vest["units"]), float(fmv), vest.get("fmv_currency") or "")
            )
    for entries in out.values():
        entries.sort(key=lambda e: e[0])
    return out


def cost_value_base(
    entries: list[tuple[str, float, float, str]] | None,
    as_of: str,
    qty: float,
    base: str,
    rates: dict | None,
) -> float | None:
    """``qty`` valued at the quantity-weighted cost of the units still held on ``as_of``,
    in base. None when no costed unit was held by then."""
    held = [e for e in entries or () if e[0] <= as_of]
    total_qty = sum(e[1] for e in held)
    if total_qty <= 1e-9:
        return None
    total_base = sum(to_base(q * c, ccy or base, base, rates=rates) for _, q, c, ccy in held)
    return qty * total_base / total_qty


def last_acquired(entries: list[tuple[str, float, float, str]] | None) -> str | None:
    """The date of the latest purchase in ``entries`` (a positive entry), or None."""
    days = [e[0] for e in entries or () if e[1] > 0]
    return max(days) if days else None


class FallbackLog:
    """Which instruments a series valued without a recorded price, and over which days."""

    def __init__(self) -> None:
        self._spans: dict[str, dict] = {}

    def note(self, iid: str, source: str, as_of: str) -> None:
        span = self._spans.get(iid)
        if span is None:
            self._spans[iid] = {"source": source, "from": as_of, "to": as_of}
            return
        span["from"] = min(span["from"], as_of)
        span["to"] = max(span["to"], as_of)
        if source == SOURCE_COST:
            span["source"] = SOURCE_COST

    def entries(self, instruments: dict[str, dict]) -> list[dict]:
        """``[{instrument_id, symbol, name, source, from, to}]``, by symbol."""
        rows = []
        for iid, span in self._spans.items():
            inst = instruments.get(iid) or {}
            rows.append(
                {
                    "instrument_id": iid,
                    "symbol": inst.get("symbol") or "",
                    "name": inst.get("name") or inst.get("symbol") or iid,
                    **span,
                }
            )
        return sorted(rows, key=lambda r: (r["symbol"], r["instrument_id"]))

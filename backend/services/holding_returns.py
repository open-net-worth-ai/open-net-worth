"""Per-holding performance over a window: how each investment did between two dates.

Uses exactly the portfolio's own pieces — ``holdings_as_of`` for quantities,
``instrument_value_base`` for valuation and ``tagged_cash_flows`` for the money that moved —
so the holdings' gains add up to the portfolio's ``period_gain_base`` for the same window.

A holding's gain = end value − start value + its net flows (buys negative, sale proceeds
positive): a purchase is not a gain and a sale is not a loss. Its return is Modified Dietz —
the gain over the capital that was actually at work, each flow weighted by how long it was
in the window — the standard single-holding return when money moved mid-period.
"""

from __future__ import annotations

from datetime import date

from backend.core.fx import base_currency
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.price_history_store import PriceHistoryStore
from backend.db.realized_event_store import RealizedEventStore
from backend.services import historical_marks as hm


def holding_period_returns(
    start: str,
    end: str,
    *,
    rates: dict | None = None,
    instruments: dict[str, dict] | None = None,
    price_history: dict[str, dict[str, float]] | None = None,
    lots: list[dict] | None = None,
    disposals: list[dict] | None = None,
    inception: str | None = None,
    flows: list[tuple[str, float, str]] | None = None,
) -> list[dict]:
    """One row per holding that had a value or a flow in ``[start, end]``, largest gain first.

    ``start`` is the baseline date (its close is the opening value) and flows count in
    ``(start, end]`` — the same convention as ``returns.portfolio_returns``. Injectable
    stores for tests."""
    from backend.services import portfolio_history as ph
    from backend.services.fx_cache import load_fx_cache
    from backend.services.returns import tagged_cash_flows

    base = base_currency()
    if rates is None:
        rates = ph._load_rates_safe(load_fx_cache)
    if instruments is None:
        instruments = {i["id"]: i for i in InstrumentStore().all() if i.get("id")}
    if price_history is None:
        price_history = PriceHistoryStore().as_of_maps([start, end])
    parked = ph.parked_vest_events() if lots is None else []
    if lots is None:
        lots = LotStore().all()
    if disposals is None:
        disposals = RealizedEventStore().query_events()
    if inception is None:
        inception = ph.inception_date()

    held = {
        day: ph.holdings_as_of(
            day, inception=inception, lots=lots, disposals=disposals, parked=parked
        )
        for day in (start, end)
    }
    # Same fallback as the portfolio series (manual mark, else cost), so the holdings
    # still add up to the portfolio's period gain.
    costs = hm.cost_index(
        lots,
        inception=inception,
        locked_position_ids=ph.resolve_locked_position_ids(),
        disposals=disposals,
        parked=ph.parked_vests() if parked else [],
    )
    values = {
        day: {
            iid: ph.instrument_value_base(
                instruments.get(iid),
                qty,
                price_history.get(day, {}),
                base,
                rates,
                as_of=day,
                costs=costs,
            )
            for iid, qty in held[day].items()
        }
        for day in (start, end)
    }
    if flows is None:
        flows, _unknown = tagged_cash_flows(start, end, base, rates=rates)
    by_instrument: dict[str, list[tuple[str, float]]] = {}
    for when, amount, iid in flows:
        by_instrument.setdefault(iid, []).append((when, amount))

    ids = {i for day in (start, end) for i, v in values[day].items() if v is not None}
    ids |= set(by_instrument)
    rows = [
        _row(
            instruments.get(iid) or {},
            iid,
            values[start].get(iid) or 0.0,
            values[end].get(iid) or 0.0,
            by_instrument.get(iid, []),
            start,
            end,
            price_history,
        )
        for iid in ids
    ]
    return sorted(rows, key=lambda r: r["gain_base"], reverse=True)


def _row(
    inst: dict,
    iid: str,
    start_value: float,
    end_value: float,
    flows: list[tuple[str, float]],
    start: str,
    end: str,
    price_history: dict[str, dict[str, float]],
) -> dict:
    net_flow = sum(amount for _, amount in flows)
    gain = end_value - start_value + net_flow
    pid = inst.get("price_source_id") or ""
    first = price_history.get(start, {}).get(pid) if pid else None
    last = price_history.get(end, {}).get(pid) if pid else None
    return {
        "instrument_id": iid,
        "symbol": inst.get("symbol") or "",
        "name": inst.get("name") or inst.get("symbol") or iid,
        "asset_class": inst.get("asset_class") or "",
        "start_value_base": round(start_value, 2),
        "end_value_base": round(end_value, 2),
        "bought_base": round(-sum(a for _, a in flows if a < 0), 2),
        "sold_base": round(sum(a for _, a in flows if a > 0), 2),
        "gain_base": round(gain, 2),
        "return_pct": _modified_dietz(start_value, gain, flows, start, end),
        "price_change_pct": (
            round((float(last) / float(first) - 1) * 100, 2) if first and last else None
        ),
    }


def _modified_dietz(
    start_value: float, gain: float, flows: list[tuple[str, float]], start: str, end: str
) -> float | None:
    """Gain ÷ capital at work, in percent; None when no capital was at work (nothing to
    divide by — never a fabricated 0% or infinity)."""
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days
    invested = start_value
    for when, amount in flows:
        weight = (date.fromisoformat(end) - date.fromisoformat(when)).days / span if span else 0
        invested += -amount * weight  # a buy (negative flow) adds capital for its share
    if invested <= 0:
        return None
    return round(gain / invested * 100, 2)

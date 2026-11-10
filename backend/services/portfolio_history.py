"""
portfolio_history — shared primitive for reconstructed historical portfolio value.

The existing net_worth_history() and returns value_series() both rely on current-quantity
back-projection (Pillar-2 approximation) — wrong whenever the user traded inside the window.
This module provides the SINGLE source of truth for historical portfolio quantity and value,
intended to replace both approximations in later phases (RFC §"Shared primitive").

Three key design choices:
  * Quantity reconstruction is a FORWARD SWEEP over sorted lot/disposal events — O(events +
    dates) rather than O(dates × events). ``opening`` lots (no acquired_at) are pinned to
    inception so they contribute from the first book date onward; dated lots step in on their
    acquired_at; disposals step down on disposed_at.
  * Asset-class banding mirrors ``returns._MARKET_BANDS`` (equity/crypto/commodity only;
    property excluded) — both are replicated locally to avoid importing private symbols from
    sibling modules.
  * FX uses current cached rates — the same approximation as net_worth_history — with an
    ``fx_approximate`` flag on every point. As-of FX is deferred (RFC §Resolved).

Imports: downward/sideways only — db/, core/, other services/. No api/ or ai/ import.
"""

from __future__ import annotations

import logging

from backend.core.fx import base_currency, to_base
from backend.db import read_cache
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.price_history_store import PriceHistoryStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.transaction_store import TransactionStore
from backend.services._grain import _grain_dates

_log = logging.getLogger(__name__)

# Asset-class bands that represent market positions (lot-backed, priceable).
# Mirrors returns._MARKET_BANDS — cash/receivable/property are excluded because they are
# either account money or not lot-backed, so they don't belong in the investment value series.
_MARKET_BANDS = ("equity", "crypto", "commodity")

# Maps instrument.asset_class → the display band used in the value series.
# Mirrors returns._MARKET_BANDS (equity/crypto/commodity only; property excluded).
# bonds-first-class: bond now maps to its OWN band "bond" — and since "bond" is NOT in
# _MARKET_BANDS it drops cleanly out of the lot-backed investment performance value series
# (same exclusion as property/cash). Bond TWR/MWR is out of the first-class-holding scope
# (no historical marks for computed-mark bonds yet); the net-worth stacked chart shows the
# bond band via ledger._MARKET_BAND on the rows-path.
# None return from this dict means "exclude from market value" (property, cash, etc.).
_ASSET_CLASS_TO_BAND: dict[str, str] = {
    "equity": "equity",
    "bond": "bond",
    "crypto": "crypto",
    "commodity": "commodity",
    # option: mapped for classification completeness; excluded from lot-backed _MARKET_BANDS
    # (no lot spine for options — they have their own OptionContractStore). The signed mark
    # is injected into NW history via ledger._compute_options_mark_base (today-only).
    "option": "option",
}


# ── Inception date ──────────────────────────────────────────────────────────────


def inception_date(
    accounts: list[dict] | None = None,
    txns: list[dict] | None = None,
) -> str | None:
    """Earliest book date: the earliest non-deleted transaction date.

    This is the portfolio value floor — the date from which holdings_as_of can make
    sensible claims. Mirrors the ``candidate_dates`` logic in net_worth_history().
    Returns an ISO 'YYYY-MM-DD' string, or None when the book is completely empty.

    An account's tracking start IS its Starting Balance transaction's date, so the
    account term that used to sit in this min() is now redundant — every cutoff is
    already a transaction date. ``accounts`` is kept in the signature for the callers
    that pass it; it no longer contributes a candidate.
    """
    if txns is None:
        return TransactionStore().earliest_date()

    candidates = [d for t in txns if (d := (t.get("date") or "")[:10])]
    return min(candidates) if candidates else None


# ── Holdings reconstruction ─────────────────────────────────────────────────────


def resolve_locked_position_ids(positions: list[dict] | None = None) -> frozenset[str]:
    """Ids of `vesting_state="unvested"` positions (unvested equity — see equity.py's
    _create_locked_position; the entitlement axis itself was later split out of the
    overloaded `bucket` field into its own `vesting_state` field — ADR 2026-07-31-split-
    position-bucket). A lot attached to one of these must never contribute quantity to
    reconstructed holdings: unvested equity is excluded from net worth (ADR 2026-07-31-
    unvested-equity-excluded-from-net-worth), and this primitive also feeds returns/
    risk-metrics, so the exclusion belongs here rather than only in
    ledger._breakdown_as_of's rows-only fallback path. Current code never lot-backs an
    unvested position (a grant's unvested units live purely on the position's own
    ``quantity`` field), but a legacy/imported lot could still exist against one, so
    this is enforced at the read path rather than assumed. Name kept as-is post-split
    (not renamed to `resolve_unvested_position_ids`) — "locked" is still how the rest of
    this module and its callers refer to this position set.

    Public (not module-private): ``services.returns.cash_flows`` reuses this to keep
    its lot/disposal-event walk consistent with this module's own entitlement exclusion
    (ADR 2026-07-31-returns-fixed-income-locked-bucket-audit)."""
    if positions is None:
        positions = PositionStore().query_positions(vesting_state="unvested")
    return frozenset(p["id"] for p in positions if p.get("id"))


def parked_vests(parked: list[dict] | None = None) -> list[dict]:
    """Every vest still PARKED awaiting release, as ``{date, units, instrument_id,
    fmv_per_unit, fmv_currency}``.

    A two-step vest parks its units on the grant's pending position with NO lot until release
    (by design, 2026-09-23 — a parked unit is not sellable), and release mints the lot dated at
    the VEST date at the vest-date FMV. A rebuild that reads only lots therefore lost parked
    shares entirely — live, 2026-10-07: 488.75 KSTL vested 15 Sep, about SGD 25.7K, were in
    today's net worth but in no month of the history. Counting each parked vest exactly as its
    future lot will (from the vest date, at the vest-date FMV) keeps holdings AND returns' cash
    flows consistent on both sides of release.
    """
    from backend.db.vest_event_store import VestEventStore

    if parked is None:
        parked = [e for e in VestEventStore().all() if e.get("pending_release") is True]
    positions = {p.get("id"): p for p in PositionStore().all()} if parked else {}
    out = []
    for ev in parked:
        iid = (positions.get(ev.get("pending_position_id")) or {}).get("instrument_id") or ""
        units, when = float(ev.get("units") or 0), (ev.get("date") or "")[:10]
        if iid and units > 0 and when:
            out.append(
                {
                    "date": when,
                    "units": units,
                    "instrument_id": iid,
                    "fmv_per_unit": ev.get("fmv_per_unit"),
                    "fmv_currency": ev.get("fmv_currency") or "",
                }
            )
    return out


def parked_vest_events(parked: list[dict] | None = None) -> list[tuple[str, float, str]]:
    """``(vest_date, units, instrument_id)`` per still-parked vest — see ``parked_vests``."""
    return [(v["date"], v["units"], v["instrument_id"]) for v in parked_vests(parked)]


def holdings_as_of(
    as_of: str,
    *,
    inception: str | None = None,
    lots: list[dict] | None = None,
    disposals: list[dict] | None = None,
    locked_position_ids: frozenset[str] | None = None,
    parked: list[tuple[str, float, str]] | None = None,
) -> dict[str, float]:
    """Reconstructed net quantity per instrument_id as-of ``as_of`` (inclusive).

    The sweep works over three event types in ascending date order:
      * ``opening`` lots (acquired_at == "" or None) — count from inception forward.
        These are holdings already present at onboarding; we can't know their exact entry
        date so we pin them to inception. For any ``as_of >= inception`` they are held.
      * Dated lots (acquired_at present) — add original_qty when acquired_at <= as_of.
      * Disposals (RealizedEventStore events) — subtract quantity when disposed_at <= as_of.

    A lot (or disposal) whose ``position_id`` is a `bucket="locked"` position is skipped
    entirely — unvested equity never contributes to reconstructed holdings.

    Returns {instrument_id: net_qty} for instruments with abs(net_qty) > 1e-9. At today's
    date, Σ(net_qty) per instrument must equal Σ(remaining_qty) from LotStore over
    non-locked positions — verified in tests. ``inception``, ``lots``, ``disposals``,
    ``locked_position_ids`` can be injected for testing. Units of a vest still parked awaiting
    release (``parked_vest_events``) count from the vest date — they have no lot yet.

    When injecting ``lots``, pass ``inception`` explicitly; with ``inception=None`` undated/opening lots are excluded.
    """
    if inception is None:
        inception = inception_date()
    if parked is None:
        parked = parked_vest_events() if lots is None else []
    if lots is None:
        lots = LotStore().all()
    if disposals is None:
        disposals = RealizedEventStore().query_events()
    if locked_position_ids is None:
        locked_position_ids = resolve_locked_position_ids()

    qtys: dict[str, float] = {}

    for lot in lots:
        if lot.get("position_id") in locked_position_ids:
            continue
        iid = lot.get("instrument_id") or ""
        if not iid:
            continue
        original_qty = float(lot.get("original_qty") or 0)
        if original_qty <= 0:
            continue
        acquired = (lot.get("acquired_at") or "")[:10]

        if not acquired:
            # Undated / opening lot: treat as held from inception forward.
            # If there is no inception (empty book), skip — no date to pin to.
            if inception and as_of >= inception:
                qtys[iid] = qtys.get(iid, 0.0) + original_qty
        else:
            if acquired <= as_of:
                qtys[iid] = qtys.get(iid, 0.0) + original_qty

    for ev in disposals:
        if ev.get("position_id") in locked_position_ids:
            continue
        iid = ev.get("instrument_id") or ""
        if not iid:
            continue
        disposed_at = (ev.get("disposed_at") or "")[:10]
        if not disposed_at or disposed_at > as_of:
            continue
        qty = float(ev.get("quantity") or 0)
        qtys[iid] = qtys.get(iid, 0.0) - qty

    for vested_on, units, iid in parked:
        if vested_on <= as_of:
            qtys[iid] = qtys.get(iid, 0.0) + units

    return {iid: q for iid, q in qtys.items() if abs(q) > 1e-9}


# ── Holdings series (single-sweep, O(events + dates)) ──────────────────────────


def holdings_series(
    start: str,
    end: str,
    grain: str = "daily",
    *,
    inception: str | None = None,
    lots: list[dict] | None = None,
    disposals: list[dict] | None = None,
    locked_position_ids: frozenset[str] | None = None,
    parked: list[tuple[str, float, str]] | None = None,
) -> list[dict]:
    """Quantity snapshot per instrument at each grain date in [start, end].

    Returns [{"date": iso, "holdings": {instrument_id: qty}}] sorted ascending.
    Supported grains: "daily", "monthly" (month-end + today, matching net_worth_history grain).

    The algorithm does ONE forward sweep over sorted lot/disposal events, maintaining
    a rolling qty dict and snapshotting it at each grain date — O(events + dates),
    not O(dates × events). This makes daily grain cheap even over multi-year windows.

    A lot (or disposal) whose ``position_id`` is a `bucket="locked"` position is skipped
    entirely — unvested equity never contributes to reconstructed holdings (mirrors
    ``holdings_as_of``).

    When injecting ``lots``, pass ``inception`` explicitly; with ``inception=None`` undated/opening lots are excluded.
    """
    if inception is None:
        inception = inception_date(
            accounts=None if lots is None else [],
            txns=None if disposals is None else [],
        )

    grain_dates = _grain_dates(start, end, grain)
    if not grain_dates:
        return []

    if parked is None:
        parked = parked_vest_events() if lots is None else []
    if lots is None:
        lots = LotStore().all()
    if disposals is None:
        disposals = RealizedEventStore().query_events()
    if locked_position_ids is None:
        locked_position_ids = resolve_locked_position_ids()

    # Build a sorted list of (date, +qty, instrument_id) events.
    # Opening lots use inception as their effective date; if inception is None they are excluded
    # (matching holdings_as_of behaviour).
    events: list[tuple[str, float, str]] = []
    for lot in lots:
        if lot.get("position_id") in locked_position_ids:
            continue
        iid = lot.get("instrument_id") or ""
        if not iid:
            continue
        original_qty = float(lot.get("original_qty") or 0)
        if original_qty <= 0:
            continue
        acquired = (lot.get("acquired_at") or "")[:10]
        if not acquired:
            # Exclude undated lots when there is no inception — matches holdings_as_of.
            if inception is None:
                continue
            effective = inception
        else:
            effective = acquired
        events.append((effective, original_qty, iid))

    for ev in disposals:
        if ev.get("position_id") in locked_position_ids:
            continue
        iid = ev.get("instrument_id") or ""
        if not iid:
            continue
        disposed_at = (ev.get("disposed_at") or "")[:10]
        if not disposed_at:
            continue
        qty = float(ev.get("quantity") or 0)
        events.append((disposed_at, -qty, iid))

    events.extend(parked)  # still-parked vests: +units on the vest date, no lot yet
    events.sort(key=lambda e: e[0])

    # Single forward sweep: apply events up through each grain date.
    rolling: dict[str, float] = {}
    out: list[dict] = []
    ev_idx = 0
    n_events = len(events)

    for grain_date in grain_dates:
        while ev_idx < n_events and events[ev_idx][0] <= grain_date:
            _, delta, iid = events[ev_idx]
            rolling[iid] = rolling.get(iid, 0.0) + delta
            ev_idx += 1
        snapshot = {iid: q for iid, q in rolling.items() if abs(q) > 1e-9}
        out.append({"date": grain_date, "holdings": snapshot})

    return out


# ── Portfolio value series ─────────────────────────────────────────────────────


@read_cache.cached(
    lambda start, end, grain="daily", **kw: (start, end, grain),
    # Cache only the plain API path; the internal path passes pre-loaded mutable inputs
    # (lots/disposals/instruments/price_history/rates) that must not be memoised.
    when=lambda start, end, grain="daily", **kw: not any(kw.values()),
)
def portfolio_value_series(
    start: str,
    end: str,
    grain: str = "daily",
    *,
    inception: str | None = None,
    lots: list[dict] | None = None,
    disposals: list[dict] | None = None,
    instruments: dict[str, dict] | None = None,
    price_history: dict[str, dict[str, float]] | None = None,
    rates: dict | None = None,
) -> dict:
    """Investment-book value series at each grain date, restricted to _MARKET_BANDS.

    Returns:
        {
          "series": [{"date", "value_base", "classes"}],
          "data_quality": {
              "market_since": str | None,
              "inception": str | None,
              "tiers": {instrument_id: "exact" | "approximate"},
              "has_unrecorded_risk": bool,
              "has_unknown_cost": bool,
              "fx_approximate": bool,
          }
        }

    ``value_base`` = Σ over instruments  qty × price_as_of × fx_to_base.
    ``classes`` = per-band sub-totals at that date (matches net_worth_history shape).

    Prices are looked up via PriceHistoryStore.as_of_maps() (carry-forward latest ≤ date)
    keyed by the instrument's price_source_id. FX uses current cached rates; every series
    point is therefore ``fx_approximate=True`` when any contributing currency != base.

    Injectable parameters for testing: ``inception``, ``lots``, ``disposals``,
    ``instruments`` (map instrument_id→record), ``price_history`` (pre-built as_of_maps
    result), ``rates`` (FX rate dict).
    """
    from backend.services.fx_cache import load_fx_cache

    if inception is None:
        inception = inception_date()

    # The real book (no injected lots) also counts vests still parked awaiting release.
    parked = parked_vest_events() if lots is None else []
    if lots is None:
        lots = LotStore().all()
    if disposals is None:
        disposals = RealizedEventStore().query_events()

    h_series = holdings_series(
        start,
        end,
        grain,
        inception=inception,
        lots=lots,
        disposals=disposals,
        parked=parked,
    )
    grain_dates = [pt["date"] for pt in h_series]

    if instruments is None:
        inst_store = InstrumentStore()
        instruments = {i["id"]: i for i in inst_store.all() if i.get("id")}

    if price_history is None:
        if grain_dates:
            price_history = PriceHistoryStore().as_of_maps(grain_dates)
        else:
            price_history = {}

    if rates is None:
        rates = _load_rates_safe(load_fx_cache)

    base = base_currency()

    # Pre-compute per-instrument acquired_at presence for data_quality tiers.
    # An instrument is "exact" iff at least one lot has a non-blank acquired_at.
    lot_has_dated: dict[str, bool] = {}
    lot_has_undated: dict[str, bool] = {}
    for lot in lots:
        iid = lot.get("instrument_id") or ""
        if not iid:
            continue
        acquired = (lot.get("acquired_at") or "")[:10]
        if acquired:
            lot_has_dated[iid] = True
        else:
            lot_has_undated[iid] = True

    series: list[dict] = []
    for pt in h_series:
        d = pt["date"]
        holdings = pt["holdings"]
        as_of_prices = price_history.get(d, {})
        value_base = 0.0
        classes: dict[str, float] = {}

        for iid, qty in holdings.items():
            inst = instruments.get(iid)
            contrib = instrument_value_base(inst, qty, as_of_prices, base, rates)
            if contrib is None:
                continue
            band = _ASSET_CLASS_TO_BAND.get(inst.get("asset_class") or "")
            value_base += contrib
            classes[band] = classes.get(band, 0.0) + contrib

        series.append(
            {
                "date": d,
                "value_base": round(value_base, 2),
                "classes": {k: round(v, 2) for k, v in classes.items()},
            }
        )

    # ── data_quality ────────────────────────────────────────────────────────────
    # Determine which instruments actually contributed to the series.
    contributing_ids: set[str] = set()
    for pt in h_series:
        for iid in pt["holdings"]:
            inst = instruments.get(iid)
            if not inst:
                continue
            band = _ASSET_CLASS_TO_BAND.get(inst.get("asset_class") or "")
            if band in _MARKET_BANDS:
                contributing_ids.add(iid)

    tiers: dict[str, str] = {}
    has_unrecorded_risk = False
    has_unknown_cost = False
    fx_approximate = False

    for iid in contributing_ids:
        inst = instruments.get(iid) or {}
        # Tier is "exact" iff at least one lot has a non-blank acquired_at.
        tiers[iid] = "exact" if lot_has_dated.get(iid) else "approximate"
        # has_unrecorded_risk if ANY contributing instrument has ANY lot with blank acquired_at.
        if lot_has_undated.get(iid):
            has_unrecorded_risk = True

        # Check if any lot for this instrument has unknown unit_cost.
        for lot in lots:
            if lot.get("instrument_id") == iid and lot.get("unit_cost") is None:
                has_unknown_cost = True
                break

        quote_ccy = (inst.get("quote_currency") or inst.get("currency") or base).upper()
        if quote_ccy != base.upper():
            fx_approximate = True

    market_since = PriceHistoryStore().earliest_date()

    return {
        "series": series,
        "data_quality": {
            "market_since": market_since,
            "inception": inception,
            "tiers": tiers,
            "has_unrecorded_risk": has_unrecorded_risk,
            "has_unknown_cost": has_unknown_cost,
            "fx_approximate": fx_approximate,
        },
    }


def instrument_value_base(
    inst: dict | None, qty: float, as_of_prices: dict, base: str, rates: dict
) -> float | None:
    """One instrument's market value in base on a date, or None when it doesn't count.

    The ONE per-instrument fold behind the investment value series: an unknown instrument,
    a band outside ``_MARKET_BANDS`` or a missing price contributes nothing. Per-holding
    period returns use it too, so the holdings always add up to the portfolio."""
    if not inst:
        return None
    if _ASSET_CLASS_TO_BAND.get(inst.get("asset_class") or "") not in _MARKET_BANDS:
        return None
    pid = inst.get("price_source_id") or ""
    price = as_of_prices.get(pid) if pid else None
    if price is None:
        return None
    qpu = float(inst.get("quote_per_unit") or 1.0) or 1.0
    quote_ccy = inst.get("quote_currency") or inst.get("currency") or base
    return to_base(qty * float(price) * qpu, quote_ccy, base, rates=rates)


def _load_rates_safe(loader) -> dict:
    """Current cached FX rates, or pivot-only fallback — defensive so a missing/corrupt
    fx_cache never crashes a pure read. Mirrors the pattern in returns.py."""
    try:
        return loader()
    except Exception:
        return {}

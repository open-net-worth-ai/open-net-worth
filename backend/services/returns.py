"""
returns_service — period returns: TWR + MWR/XIRR over the investment book (P2-INV3).

The "return keystone". Day P&L and unrealized gain answer "what is it worth?"; this
module answers "how is it actually PERFORMING?" — separating contributions from genuine
return. Six standard windows (7D/1M/3M/YTD/1Y/ALL), three metric tiers based on the
effective span:

  * < 360 days  → cumulative TWR + period $ gain only (annualized=false, mwr=None).
                   Too short for MWR/XIRR (XIRR is inherently annualized → meaningless
                   and noisy on sub-year windows). A fresh book still sees a number,
                   clamped + relabeled to "Since <inception>".
  * ≥ 360 days  → annualized TWR + MWR/XIRR (annualized=true). The full picture.

TWR (time-weighted) — chain-links sub-period returns, neutralizing external cash flows
so only market performance counts (the "manager skill" number). MWR/XIRR (money-
weighted) — Newton's-method IRR over signed dated cash flows; the user's personal
return including when they added capital.

Phase 4 changes (RFC §"Changes to services/returns.py"):
  * value series now sourced from portfolio_history.portfolio_value_series (daily,
    reconstructed quantities) instead of the monthly current-qty net_worth_history.
    This makes TWR correct under mid-window trades.
  * Effective-window clamping: requested_start is floored to max(inception, price_floor)
    so a fresh/mid book degrades gracefully instead of refusing. effective_start + label
    are added to the payload.
  * 7D window added to PERIODS.
  * Span-based metric selection (see thresholds above) with explicit annualized: bool.
  * series[] chart payload: 0-based cumulative return fractions per day — portfolio uses
    the chain-linked TWR path (so the endpoint == TWR period_return and contributions are
    stripped); benchmark uses price/price_open - 1. Both lines are directly comparable
    on the same Y-axis in % units. The chart never shows a rebased-to-100 raw value.
  * data_quality passthrough: tiers, has_unrecorded_risk, fx_approximate from the
    primitive; inception + effective_start added alongside the existing market_since.

Computed-not-stored (the CLAUDE.md invariant): no new store, no persisted field, no
migration. All inputs already exist and are REUSED.

Imports are downward/sideways only (services → db → core; services↔services allowed):
uses portfolio_history + reads LotStore/RealizedEventStore directly. No api/ai import.
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, timedelta

from backend.core.errors import ValidationError
from backend.core.fx import base_currency, to_base
from backend.core.user_time import user_today
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.price_history_store import PriceHistoryStore
from backend.db.realized_event_store import RealizedEventStore

_log = logging.getLogger(__name__)

# Standard period windows exposed by the UI toggle / API enum. 7D added in Phase 4.
PERIODS = ("7D", "1M", "3M", "YTD", "1Y", "ALL")

# A window needs at least this many distinct value points to chain-link a meaningful TWR /
# solve a stable XIRR. Below it we return null rather than a fabricated number.
MIN_VALUE_POINTS = 2

# XIRR solver knobs. Newton is fast when it converges; bisection is the robust fallback.
_XIRR_MAX_ITER = 100
_XIRR_TOL = 1e-7
_DAYS_PER_YEAR = 365.0

# Effective-span thresholds that gate metric availability (Phase 4, RFC §Resolved):
#   < 30d  → no MWR (too noisy for a meaningful signal)
#   30–359d → MWR with de-annualized period_return: (1+xirr)^(span/365) - 1;
#             annualized=False so the stat card labels it as "your IRR"
#   ≥ 360d → annualized TWR + MWR; period_return == xirr (already annual)
_SPAN_MWR_MIN = 30  # days: add MWR/XIRR only at ≥ 30d
_SPAN_ANNUALIZE = 360  # days: annualize TWR + MWR at this effective span

# Asset-class bands in portfolio_history.portfolio_value_series `classes` map that ARE
# market positions (the TWR numerator/denominator) AND are lot-backed. Mirrors the constant
# in portfolio_history (replicated locally to avoid importing private symbols from a sibling).
_MARKET_BANDS = ("equity", "crypto", "commodity")

# An unfunded transfer-in changes custody, not invested capital. Its matching zero-proceeds
# transfer-out already leaves the reconstructed whole-book value unchanged, so treating
# the receiving lot as an acquisition flow creates a false loss in TWR/MWR. A transfer-in
# with a linked funding transaction remains an external flow because cash really left.
_INTERNAL_TRANSFER_LOT_KINDS = frozenset({"transfer_in"})

# Maps instrument.asset_class → the band _MARKET_BANDS filters against. Mirrors
# portfolio_history._ASSET_CLASS_TO_BAND (same replicate-locally reasoning as _MARKET_BANDS
# above) — bond maps to its own "bond" band, which is NOT in _MARKET_BANDS, so cash_flows()
# can exclude a bond lot/disposal the same way it excludes an unvested one (MATH-34: a
# bond is lot-backed but never counted in the value series it's meant to neutralize).
_ASSET_CLASS_TO_BAND: dict[str, str] = {
    "equity": "equity",
    "bond": "bond",
    "crypto": "crypto",
    "commodity": "commodity",
    "option": "option",
}


# ── Period windowing ─────────────────────────────────────────────────────────────


def resolve_period(period: str, today: date | None = None) -> tuple[str, str]:
    """Resolve a period enum to an inclusive (start, end) ISO date pair (end = today).

    7D     → today minus 7 days.
    1M/3M  → today minus 1/3 calendar months (day clamped to month length).
    YTD    → Jan 1 of the current year.
    1Y     → today minus 1 year (Feb-29 clamps to Feb-28).
    ALL    → the earliest recorded price-history date (or, if none yet, today).
    Unknown period defaults to 1Y (the API validates the enum upstream; this is defensive).
    """
    today = today or user_today()
    end = today.isoformat()
    p = (period or "1Y").upper()
    if p == "7D":
        start = today - timedelta(days=7)
    elif p == "1M":
        start = _shift_months(today, 1)
    elif p == "3M":
        start = _shift_months(today, 3)
    elif p == "YTD":
        start = date(today.year, 1, 1)
    elif p == "ALL":
        earliest = PriceHistoryStore().earliest_date()
        start = date.fromisoformat(earliest[:10]) if earliest else today
    else:  # "1Y" and any unknown value
        start = _shift_years(today, 1)
    if start > today:
        start = today
    return start.isoformat(), end


def resolve_window(
    period: str,
    today: date | None = None,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[str, str]:
    """``(start, end)`` for a period enum, or for an explicit ``date_from``..``date_to``.

    ``date_from`` is the FIRST day the window covers, so the baseline is the close of the day
    before: September is ``date_from=2026-09-01, date_to=2026-09-30``, measured from the
    31 Aug value. ``date_to`` past today is clamped to today. Raises ``ValidationError`` on
    a malformed or inverted range."""
    if not date_from and not date_to:
        return resolve_period(period, today=today)
    today = today or user_today()
    try:
        first = date.fromisoformat(str(date_from)[:10]) if date_from else None
        last = date.fromisoformat(str(date_to)[:10]) if date_to else today
    except ValueError as exc:
        raise ValidationError("date_from / date_to must be YYYY-MM-DD") from exc
    if first is None:
        raise ValidationError("date_to needs a date_from")
    last = min(last, today)
    if first > last:
        raise ValidationError("date_from must be on or before date_to (and not in the future)")
    return (first - timedelta(days=1)).isoformat(), last.isoformat()


def _shift_months(d: date, months: int) -> date:
    """`d` minus `months` calendar months, clamping the day to the target month's length."""
    total = d.year * 12 + (d.month - 1) - months
    y, m = divmod(total, 12)
    m += 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # Feb-29 → Feb-28 in a non-leap target year
        return d.replace(year=d.year - years, day=28)


def _effective_window(
    requested_start: str,
    end: str,
    inception: str | None,
    price_floor: str | None,
) -> str:
    """Clamp requested_start to the latest of (requested_start, inception, price_floor).

    This implements the Phase 4 effective-window rule: a fresh book or one whose price
    history is younger than the requested start just shifts the window forward rather than
    refusing. Returns the effective start date (ISO string).
    """
    floors = [requested_start]
    if inception:
        floors.append(inception[:10])
    if price_floor:
        floors.append(price_floor[:10])
    effective = max(floors)
    # Never let effective_start exceed end (the extreme case of a book with no history).
    return min(effective, end)


def _period_label(period: str, effective_start: str, requested_start: str) -> str:
    """Human label for the window. Shows "Since YYYY-MM-DD" when clamped (the effective
    start is later than what was requested), otherwise the standard period label."""
    if effective_start > requested_start:
        return f"Since {effective_start}"
    if period.upper() == "CUSTOM":
        return "Custom range"
    labels = {
        "7D": "Last 7 days",
        "1M": "Last 1 month",
        "3M": "Last 3 months",
        "YTD": "Year to date",
        "1Y": "Last 1 year",
        "ALL": "All time",
    }
    return labels.get(period.upper(), period.upper())


# ── Value series (sourced from the portfolio_history primitive — daily, reconstructed) ──


def _market_value(point: dict) -> float:
    """Sum of the market-position bands at one portfolio_value_series point.
    Excludes cash/receivable/liabilities/property — those are account money, not return."""
    classes = point.get("classes") or {}
    return round(sum(float(classes.get(b, 0) or 0) for b in _MARKET_BANDS), 2)


def value_series(start: str, end: str, history: list[dict] | None = None) -> list[dict]:
    """The dated investment-book market value series anchored to the requested window.

    Phase 4: delegates to portfolio_history.portfolio_value_series (daily, reconstructed
    quantities) when `history` is not injected. The injected `history` path remains for
    the old test fixtures (net_worth_history shape: {date, market_tracked, classes}) and
    for characterization tests that must not be broken by the Phase 4 rewrite.

    Returns [{date, value}] sorted ascending.
    """
    if history is not None:
        # Legacy / test injection: net_worth_history shape (market_tracked points).
        return _value_series_from_legacy_history(start, end, history)

    # Phase 4 path: reconstruct daily from the shared primitive.
    from backend.services import portfolio_history as ph

    result = ph.portfolio_value_series(start, end, "daily")
    pts = result.get("series") or []
    return [{"date": p["date"], "value": p["value_base"]} for p in pts]


def _value_series_from_legacy_history(start: str, end: str, history: list[dict]) -> list[dict]:
    """Build a value series from a net_worth_history-shaped list (market_tracked points).
    Kept for backward compatibility with characterization tests and old fixtures."""
    tracked = sorted(
        (
            {"date": pt["date"], "value": _market_value(pt)}
            for pt in history
            if pt.get("date") and pt.get("market_tracked")
        ),
        key=lambda p: p["date"],
    )
    if not tracked:
        return []

    out: list[dict] = []
    # START anchor: latest tracked point on or before the window start, dated AT start.
    before_or_at_start = [p for p in tracked if p["date"] <= start]
    if before_or_at_start:
        out.append({"date": start, "value": before_or_at_start[-1]["value"]})
    # In-window points strictly AFTER start (the start slot is the anchor) up to end.
    out.extend(p for p in tracked if start < p["date"] <= end)
    # END anchor: latest tracked point through end (== today), dated AT end.
    through_end = [p for p in tracked if p["date"] <= end]
    if through_end:
        end_point = {"date": end, "value": through_end[-1]["value"]}
        if out and out[-1]["date"] == end:
            out[-1] = end_point
        else:
            out.append(end_point)
    return out


# ── Cash flows (LotStore acquisitions + RealizedEventStore disposals) ──────────────


def cash_flows(
    start: str,
    end: str,
    base: str,
    rates: dict | None = None,
) -> tuple[list[tuple[str, float]], bool]:
    """Signed, dated investment cash flows in `(start, end]` (start-exclusive, end-inclusive).

    External flow sign convention (the IRR / TWR-neutralization convention): money INTO the
    investment book is a deposit (positive cash flow into investments == an outflow from the
    user's pocket), money OUT is a withdrawal. We adopt the user-pocket convention so XIRR's
    final term is +ending_value: an acquisition is a NEGATIVE flow (cash left the pocket), a
    disposal a POSITIVE flow (proceeds returned to the pocket).

      * External acquisitions (LotStore open+closed lots, excluding unfunded
        `transfer_in`) → -(original_qty x effective_unit_cost), in cost_currency → base.
        A lot with unknown unit_cost contributes NO flow (we can't fabricate a basis) and
        trips `has_unknown_cost`.
      * Disposals (RealizedEventStore) → +(quantity x proceeds_per_unit - disposal_fees), in
        proceeds_currency → base. RECOMPUTED from the native proceeds + current FX (never the
        stored realized_gain_base snapshot — same staleness rule as ledger.txn_base_amount).

    A lot or disposal whose ``position_id`` is a `vesting_state="unvested"` position
    (unvested equity-grant units — see portfolio_history.resolve_locked_position_ids) is
    EXCLUDED entirely, matching the value series this feeds: `value_series()` already
    sources from `portfolio_history.portfolio_value_series`, which drops an unvested
    position's lots from the reconstructed holdings (ADR 2026-07-31-unvested-equity-
    excluded-from-net-worth). Without this, a lot booked against an unvested position
    would show up here as a real acquisition cash flow with NO matching value in the
    series it's supposed to neutralize — corrupting TWR's flow-neutralization and
    MWR/XIRR alike.

    A lot or disposal whose instrument resolves (via `_ASSET_CLASS_TO_BAND`) to a band
    OUTSIDE `_MARKET_BANDS` — currently bonds — is likewise EXCLUDED (MATH-34). Bonds are
    lot-backed (a real LotStore row exists) but `portfolio_history.portfolio_value_series`
    never counts a bond position in the value series this function neutralizes, so an
    un-neutralized bond purchase mid-window injects a flow with no matching value,
    corrupting TWR/MWR the same way an unvested lot would. An instrument that can't be
    resolved (missing/unknown instrument_id or asset_class) is NOT excluded by this rule —
    only a band that's positively known to sit outside `_MARKET_BANDS` is dropped.

    A flow dated on/before the window start (or with an unknown/empty date) is DROPPED, NOT
    clamped to start: pre-window capital is already invested at period open, so its MARKET
    VALUE is captured by the opening flow (-start_value injected by `portfolio_returns`).
    Returns (flows, has_unknown_cost) where flows is [(iso_date, base_amount)] sorted asc.
    """
    tagged, has_unknown_cost = tagged_cash_flows(start, end, base, rates=rates)
    return [(when, amount) for when, amount, _iid in tagged], has_unknown_cost


def tagged_cash_flows(
    start: str,
    end: str,
    base: str,
    rates: dict | None = None,
) -> tuple[list[tuple[str, float, str]], bool]:
    """``cash_flows`` with each flow's ``instrument_id`` kept: ``[(iso_date, base, iid)]``.

    The single implementation behind both the portfolio's flows and the per-holding ones, so
    the two can never disagree about what money moved in the window."""
    from backend.services import lots as lots_service
    from backend.services.portfolio_history import resolve_locked_position_ids

    locked_ids = resolve_locked_position_ids()
    off_band_instrument_ids = _resolve_off_market_band_instrument_ids()
    flows: list[tuple[str, float, str]] = []
    has_unknown_cost = False

    openings: list[tuple[str, dict]] = []
    # Acquisitions → negative flows. Walk ALL lots (open + closed) so a lot fully sold inside
    # the window still records its purchase outflow.
    for lot in LotStore().all():
        if lot.get("kind") in _INTERNAL_TRANSFER_LOT_KINDS and not lot.get("txn_id"):
            continue
        if lot.get("position_id") in locked_ids:
            continue
        if lot.get("instrument_id") in off_band_instrument_ids:
            continue
        eff = lots_service.effective_unit_cost(lot)
        qty = float(lot.get("original_qty") or 0)
        if qty <= 0:
            continue
        when = _in_window(lot.get("acquired_at"), start, end)
        if when is None:
            continue  # outside (start, end] → not a period flow
        if lot.get("kind") == "opening" and not lot.get("txn_id"):
            openings.append((when, lot))  # held at tracking start — no money moved
            continue
        if eff is None:
            has_unknown_cost = True
            continue  # can't fabricate a basis
        cost_ccy = lot.get("cost_currency") or base
        amount = -to_base(eff * qty, cost_ccy, base, rates=rates)
        flows.append((when, amount, lot.get("instrument_id") or ""))

    unknown = _add_opening_flows(openings, flows, base, rates)
    has_unknown_cost = has_unknown_cost or unknown

    # A vest parked awaiting release has no lot yet, but its shares ARE in the value series
    # (portfolio_history.parked_vests) — so it is an acquisition at the vest-date FMV exactly
    # as its future lot will be. Without it, the parked shares read as market GAIN (live
    # 2026-10-07: +SGD 24.6K on KSTL, a +9.6% "return").
    from backend.services.portfolio_history import parked_vests

    for vest in parked_vests():
        if vest["instrument_id"] in off_band_instrument_ids:
            continue
        when = _in_window(vest["date"], start, end)
        if when is None:
            continue
        if not vest["fmv_per_unit"]:
            has_unknown_cost = True
            continue
        native = float(vest["fmv_per_unit"]) * vest["units"]
        amount = -to_base(native, vest["fmv_currency"] or base, base, rates=rates)
        flows.append((when, amount, vest["instrument_id"]))

    # Disposals → positive flows (net proceeds, recomputed in base from the native leg).
    for ev in RealizedEventStore().query_events(date_from=None, date_to=None):
        if ev.get("position_id") in locked_ids:
            continue
        if ev.get("instrument_id") in off_band_instrument_ids:
            continue
        qty = float(ev.get("quantity") or 0)
        per_unit = float(ev.get("proceeds_per_unit") or 0)
        fees = float(ev.get("disposal_fees") or 0)
        net_proceeds = qty * per_unit - fees
        if net_proceeds == 0:
            continue  # transfer-out / gift with no proceeds → no cash flow
        when = _in_window(ev.get("disposed_at"), start, end)
        if when is None:
            continue
        proceeds_ccy = ev.get("proceeds_currency") or base
        amount = to_base(net_proceeds, proceeds_ccy, base, rates=rates)
        flows.append((when, amount, ev.get("instrument_id") or ""))

    flows.sort(key=lambda f: f[0])
    return flows, has_unknown_cost


def _add_opening_flows(
    openings: list[tuple[str, dict]],
    flows: list[tuple[str, float, str]],
    base: str,
    rates: dict | None,
) -> bool:
    """An OPENING lot (held when tracking started — e.g. a holding added with no purchase
    date, dated the day it was added) enters at its MARKET value on its date, not its cost:
    no money moved in the tracked period, and pricing it at an old cost would read the
    whole gain since purchase as this period's return (the KSTL +9.6% bug class). A lot
    dated on its real purchase day prices at about its cost anyway. Falls back to cost when
    no price exists; returns True when neither is known."""
    if not openings:
        return False
    from backend.services.lots import effective_unit_cost
    from backend.services.portfolio_history import instrument_value_base

    prices = PriceHistoryStore().as_of_maps(sorted({when for when, _ in openings}))
    instruments = {i.get("id"): i for i in InstrumentStore().all()}
    unknown = False
    for when, lot in openings:
        iid = lot.get("instrument_id") or ""
        qty = float(lot.get("original_qty") or 0)
        value = instrument_value_base(instruments.get(iid), qty, prices.get(when, {}), base, rates)
        if value is None:
            eff = effective_unit_cost(lot)
            if eff is None:
                unknown = True
                continue
            value = to_base(eff * qty, lot.get("cost_currency") or base, base, rates=rates)
        flows.append((when, -value, iid))
    return unknown


def _resolve_off_market_band_instrument_ids() -> frozenset[str]:
    """Ids of instruments whose asset-class band is known and sits OUTSIDE `_MARKET_BANDS`
    (currently: bond). Used by `cash_flows` to exclude a lot/disposal the same way an
    unvested position's is excluded (MATH-34) — an instrument with no/unknown asset_class
    is NOT included here, so a lot with an unresolved instrument is left untouched."""
    return frozenset(
        i["id"]
        for i in InstrumentStore().all()
        if i.get("id")
        and (band := _ASSET_CLASS_TO_BAND.get(i.get("asset_class") or "")) is not None
        and band not in _MARKET_BANDS
    )


def _in_window(when: str | None, start: str, end: str) -> str | None:
    """The flow's ISO date if it falls strictly after `start` and on/before `end`.
    An empty/unknown date or a date on/before `start` or after `end` returns None."""
    d = (when or "")[:10]
    if not d or d <= start or d > end:
        return None
    return d


# ── TWR (time-weighted return — chain-linked, flow-neutralized) ────────────────────


def _subperiod_return(v_prev: float, v_now: float, net_added: float) -> float | None:
    """One sub-period total return with external flow neutralized.

    r = (V_now - net_added) / V_prev - 1
    None when V_prev <= 0 (no invested base → can't form a return).
    Shared by _twr_path and risk_metrics.daily_returns so the TWR chain and the
    daily-return vector can never diverge (single code path).
    """
    if v_prev <= 0:
        return None
    return (v_now - net_added) / v_prev - 1.0


def _twr_path(
    series: list[dict],
    flows: list[tuple[str, float]],
) -> tuple[list[tuple[str, float]], int] | None:
    """Compute the running chain-product path of the TWR and the total span in days.

    Returns a list of (date, cumulative_return_fraction) pairs — one per series point,
    starting with (series[0].date, 0.0) — and the total span in calendar days. Returns
    None when the series is too short to form a return (< MIN_VALUE_POINTS).

    The path endpoint equals the scalar TWR period_return; sharing this helper between
    _twr_path and twr() guarantees they can never diverge (one code path, not two).

    For each consecutive value pair (V_prev @ t0, V_now @ t1):
        net_added  = -Σ(flows in (t0, t1]) in pocket-convention (deposit = negative)
        r_i        = (V_now - net_added) / V_prev - 1
        chain      *= (1 + r_i)
    Sub-periods where V_prev ≤ 0 are skipped (no invested base → no sub-period return).
    span_days accumulates calendar time UNCONDITIONALLY (including skipped sub-periods), so
    the annualization exponent reflects the full calendar span even when the book opened from
    zero. This is intentional: calendar time is the correct denominator for annualizing TWR.
    """
    if len(series) < MIN_VALUE_POINTS:
        return None
    path: list[tuple[str, float]] = [(series[0]["date"], 0.0)]
    chain = 1.0
    span_days = 0
    for i in range(1, len(series)):
        v_prev = float(series[i - 1]["value"])
        v_now = float(series[i]["value"])
        t0 = series[i - 1]["date"]
        t1 = series[i]["date"]
        if v_prev > 0:
            # Net capital ADDED in (t0, t1]: a deposit is a negative pocket-flow, so
            # capital added = -Σ(flows in window). Subtract so only market move counts.
            net_added = -sum(amt for (d, amt) in flows if t0 < d <= t1)
            r = _subperiod_return(v_prev, v_now, net_added)
            if r is not None:
                chain *= 1.0 + r
        try:
            span_days += (date.fromisoformat(t1) - date.fromisoformat(t0)).days
        except (ValueError, TypeError):
            pass
        path.append((t1, round(chain - 1.0, 6)))
    return path, span_days


def twr(series: list[dict], flows: list[tuple[str, float]], annualize: bool) -> dict | None:
    """Chain-linked time-weighted return over the value `series`, neutralizing external
    `flows` on their dates.

    Delegates to _twr_path() for the chain computation so the scalar result and the
    per-day cumulative path used for the chart can never diverge. Returns
    {period_return, annualized} as fractions, or None when the series is too short /
    a denominator is non-positive (can't form a return).
    """
    result = _twr_path(series, flows)
    if result is None:
        return None
    path, span_days = result
    chain_minus_one = path[-1][1]  # cumulative return at the last point
    chain = 1.0 + chain_minus_one
    period_return = round(chain_minus_one, 6)
    ann = period_return
    if annualize and span_days > 0:
        years = span_days / _DAYS_PER_YEAR
        if years > 0:
            ann = round(chain ** (1.0 / years) - 1.0, 6)
    return {"period_return": period_return, "annualized": round(ann, 6)}


# ── MWR / XIRR (money-weighted internal rate of return) ────────────────────────────


def xirr(flows: list[tuple[str, float]], end_value: float, end_date: str) -> float | None:
    """Annualized money-weighted return (XIRR) over the dated cash-flow vector plus a final
    +end_value at `end_date`.

    Solved with Newton's method (fast), falling back to bisection on non-convergence /
    out-of-range derivative. Returns None when the vector has no sign change or is too short.
    """
    cfs = list(flows)
    if end_value:
        cfs.append((end_date, float(end_value)))
    if len(cfs) < 2:
        return None
    has_pos = any(a > 0 for _, a in cfs)
    has_neg = any(a < 0 for _, a in cfs)
    if not (has_pos and has_neg):
        return None

    try:
        t0 = min(date.fromisoformat(d[:10]) for d, _ in cfs)
    except (ValueError, TypeError):
        return None
    years = [(date.fromisoformat(d[:10]) - t0).days / _DAYS_PER_YEAR for d, _ in cfs]
    amounts = [a for _, a in cfs]

    def npv(rate: float) -> float:
        total = 0.0
        for amt, yr in zip(amounts, years):
            total += amt / (1.0 + rate) ** yr
        return total

    def dnpv(rate: float) -> float:
        total = 0.0
        for amt, yr in zip(amounts, years):
            if yr == 0:
                continue
            total += -yr * amt / (1.0 + rate) ** (yr + 1.0)
        return total

    rate = _xirr_newton(npv, dnpv)
    if rate is None:
        rate = _xirr_bisect(npv)
    return round(rate, 6) if rate is not None else None


def _xirr_newton(npv, dnpv, guess: float = 0.1) -> float | None:
    """Newton's method on the NPV(rate)=0 root. None if it diverges or doesn't converge."""
    rate = guess
    for _ in range(_XIRR_MAX_ITER):
        try:
            val = npv(rate)
        except (OverflowError, ZeroDivisionError, ValueError):
            return None
        if abs(val) < _XIRR_TOL:
            return rate
        deriv = dnpv(rate)
        if deriv == 0:
            return None
        step = val / deriv
        rate -= step
        if rate <= -1.0:  # left the valid domain (1+rate must stay > 0)
            return None
        if abs(step) < _XIRR_TOL:
            return rate
    return None


def _xirr_bisect(npv, low: float = -0.9999, high: float = 100.0) -> float | None:
    """Bisection fallback on [low, high]. Requires a sign change across the bracket."""
    try:
        f_low = npv(low)
        f_high = npv(high)
    except (OverflowError, ZeroDivisionError, ValueError):
        return None
    if f_low == 0:
        return low
    if f_high == 0:
        return high
    if f_low * f_high > 0:
        return None  # no sign change in the bracket → no root to find here
    for _ in range(_XIRR_MAX_ITER):
        mid = (low + high) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < _XIRR_TOL:
            return mid
        if f_low * f_mid < 0:
            high = mid
            f_high = f_mid
        else:
            low = mid
            f_low = f_mid
    return (low + high) / 2.0


# ── Benchmark comparison (price/TWR return of an index over the SAME window) ───────


def _resolve_benchmark_instrument(ref: str) -> dict | None:
    """Resolve a benchmark reference (instrument id OR human symbol) to its instrument record.

    Tries id first (the stable, UI-supplied key), then a case-insensitive symbol match.
    Returns None if neither hits — the caller then reports benchmark=null with a data_quality
    note rather than fabricating.
    """
    if not ref:
        return None
    store = InstrumentStore()
    inst = store.find_by_id(ref)
    if inst:
        return inst
    return store.find_by_symbol(ref)


def benchmark_price_series(price_source_id: str, start: str, end: str) -> list[dict]:
    """The benchmark instrument's daily price series anchored to the requested window,
    mirroring `_value_series_from_legacy_history`'s anchor logic so the benchmark return
    spans exactly [start, end].

    Returns [{date, price}] ascending, or [] when no price point exists on/before the window
    end (no benchmark price to anchor — the sufficiency gate then nulls the benchmark).

    Sparse-history fix (BTC / recently-listed benchmarks): if no price exists on or before
    the window start, carry the EARLIEST available price backward to the window start so
    the rebased series starts at 0% rather than showing a misleading flat-zero-then-jump.
    This means BTC selected as a benchmark on a 1Y window where BTC history only starts
    90 days ago still gives a correct 90-day comparison anchored at the window start.
    """
    pts = sorted(
        (
            {"date": p["date"], "price": float(p.get("price") or 0)}
            for p in PriceHistoryStore().series(price_source_id)
            if p.get("date") and p.get("price") is not None
        ),
        key=lambda p: p["date"],
    )
    if not pts:
        return []

    out: list[dict] = []
    before_or_at_start = [p for p in pts if p["date"] <= start]
    if before_or_at_start:
        # Carry the latest pre-window price forward to the window start.
        out.append({"date": start, "price": before_or_at_start[-1]["price"]})
    else:
        # No pre-window price — carry the EARLIEST available price backward to start.
        # This prevents a flat-zero prefix then an abrupt jump when the benchmark's history
        # begins after the window start (e.g. BTC on a 1Y window with 90 days of prices).
        earliest_in_window = next((p for p in pts if start < p["date"] <= end), None)
        if earliest_in_window:
            out.append({"date": start, "price": earliest_in_window["price"]})

    out.extend(p for p in pts if start < p["date"] <= end)
    through_end = [p for p in pts if p["date"] <= end]
    if through_end:
        end_point = {"date": end, "price": through_end[-1]["price"]}
        if out and out[-1]["date"] == end:
            out[-1] = end_point
        else:
            out.append(end_point)
    return out


def _compute_benchmark_twr(
    inst: dict,
    start: str,
    end: str,
    annualize: bool,
    series: list[dict] | None = None,
) -> tuple[dict | None, dict]:
    """Compute the benchmark price TWR for an explicit [start, end] window.

    Uses span-based annualization (the same `annualize` flag passed in) rather than
    deriving it from the period name, so the benchmark and portfolio always share the
    same annualization basis.

    ``series`` may be passed in by the caller to avoid a redundant store read when the
    price series was already fetched (e.g. for the chart in portfolio_returns_with_benchmark).

    Returns (twr_block | None, data_quality dict).
    """
    pid = inst.get("price_source_id") or ""
    if series is None:
        series = benchmark_price_series(pid, start, end) if pid else []
    market_since = PriceHistoryStore().earliest_date()

    # A synthetic carry-backward anchor (benchmark history shorter than the window) must be
    # treated as insufficient — the short-period return must not be labelled as the full
    # period return. Check the instrument's OWN series for a real price at/before start
    # rather than comparing against the global earliest_date() (which spans ALL instruments).
    has_real_anchor = pid and any(
        p.get("date") and p["date"] <= start and p.get("price") is not None
        for p in PriceHistoryStore().series(pid)
    )
    sufficient = (
        len(series) >= MIN_VALUE_POINTS and float(series[0]["price"]) > 0 and bool(has_real_anchor)
    )

    twr_block: dict | None = None
    if sufficient:
        start_price = float(series[0]["price"])
        end_price = float(series[-1]["price"])
        period_return = round(end_price / start_price - 1.0, 6)
        ann = period_return
        if annualize:
            try:
                span_days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            except (ValueError, TypeError):
                span_days = 0
            if span_days > 0:
                years = span_days / _DAYS_PER_YEAR
                ann = round((1.0 + period_return) ** (1.0 / years) - 1.0, 6)
        twr_block = {"period_return": period_return, "annualized": round(ann, 6)}

    data_quality = {
        "market_since": market_since,
        "value_points": len(series),
        "sufficient": sufficient,
    }
    return twr_block, data_quality


def benchmark_return(ref: str, period: str = "1Y", today: date | None = None) -> dict | None:
    """The benchmark instrument's pure price return over `period` (no cash flows — an index has
    none, so its return is just chain-linked/simple price growth over the window).

    Annualizes using the same span-based threshold as portfolio_returns (≥ 360 days), NOT by
    period name, so "1Y" and "ALL" on a clamped window still annualize consistently.

    Returns:
        {symbol, instrument_id, twr: {period_return, annualized}, data_quality: {...}}

    or None when `ref` doesn't resolve. When the series has too few/no points twr is null —
    NEVER a fabricated number.
    """
    inst = _resolve_benchmark_instrument(ref)
    if inst is None:
        return None

    start, end = resolve_period(period, today=today)
    try:
        span_days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except (ValueError, TypeError):
        span_days = 0
    annualize = span_days >= _SPAN_ANNUALIZE

    twr_block, data_quality = _compute_benchmark_twr(inst, start, end, annualize)
    return {
        "symbol": inst.get("symbol") or "",
        "instrument_id": inst.get("id") or "",
        "twr": twr_block,
        "data_quality": data_quality,
    }


def portfolio_vs_benchmark(
    portfolio: dict,
    ref: str,
    period: str = "1Y",
    today: date | None = None,
    _bench_series: list[dict] | None = None,
) -> dict | None:
    """Comparison block: portfolio TWR vs a benchmark's TWR over the SAME window + the excess.

    `portfolio` is an already-computed portfolio_returns() payload. The benchmark TWR is
    computed over the portfolio's effective (clamped) window — effective_start..end — using
    the portfolio's own annualized flag, so both sides always share the same window and
    annualization basis. A benchmark computed over the full requested period on a young book
    would compare a short portfolio return against a long benchmark return — wrong.

    ``_bench_series`` may be passed in by portfolio_returns_with_benchmark to avoid a second
    store read when the series was already fetched for the chart.

    Returns {benchmark, portfolio_twr, benchmark_twr, excess_return} or None when `ref`
    doesn't resolve. Excess is only defined when BOTH sides have a sufficient TWR — never
    fabricated.
    """
    inst = _resolve_benchmark_instrument(ref)
    if inst is None:
        return None

    # Use the exact clamped window and annualization flag the portfolio used.
    effective_start = portfolio.get("effective_start") or portfolio.get("start", "")
    end = portfolio.get("end", "")
    annualize = bool(portfolio.get("annualized", False))

    twr_block, data_quality = _compute_benchmark_twr(
        inst, effective_start, end, annualize, series=_bench_series
    )
    bench = {
        "symbol": inst.get("symbol") or "",
        "instrument_id": inst.get("id") or "",
        "twr": twr_block,
        "data_quality": data_quality,
    }

    p_twr = portfolio.get("twr")
    b_twr = bench.get("twr")
    # Both sides must be on the same basis: annualized when the portfolio annualizes, else
    # cumulative. This prevents a misleading comparison where one side is annualized and the
    # other is period cumulative on the same screen.
    twr_key = "annualized" if annualize else "period_return"
    portfolio_twr = p_twr[twr_key] if p_twr else None
    benchmark_twr = b_twr[twr_key] if b_twr else None
    excess = (
        round(portfolio_twr - benchmark_twr, 6)
        if portfolio_twr is not None and benchmark_twr is not None
        else None
    )
    return {
        "benchmark": bench,
        "portfolio_twr": portfolio_twr,
        "benchmark_twr": benchmark_twr,
        "excess_return": excess,
    }


# ── Chart series builder ────────────────────────────────────────────────────────────


def _rebase_series(
    portfolio_pts: list[dict],
    flows: list[tuple[str, float]],
    bench_series: list[dict],
) -> list[dict]:
    """Build a date-aligned chart series as 0-based cumulative return fractions.

    Both lines are 0-based so the window open is 0.0 (= 0%) and the endpoint of
    each line equals the period return directly (e.g. 0.15 = +15%). This makes them
    directly comparable on one Y-axis in % units without a "rebased to 100" offset.

      portfolio  — chain-linked TWR cumulative path (flows neutralized), so contributions
                   are stripped and the endpoint matches twr().period_return exactly. When
                   the series is too short to compute a path (<2 points), returns 0.0 flat.
      benchmark  — price/price_open - 1 per day (pure price return, no cash flows).

    portfolio_pts — [{date, value}] from the value_series
    flows         — signed cash flows (same list passed to twr()) for TWR path computation
    bench_series  — [{date, price}] from benchmark_price_series, or []

    Returns [{date, portfolio: float, benchmark: float | null}] sorted ascending.
    The benchmark column is null when no benchmark was provided or it has no series.
    """
    if not portfolio_pts:
        return []

    # Portfolio: use the TWR cumulative path so contributions are stripped and the
    # endpoint is the same number the TWR card shows (they share _twr_path).
    twr_result = _twr_path(portfolio_pts, flows)
    if twr_result is not None:
        path_pts, _ = twr_result
        portfolio_index: dict[str, float] = {d: v for d, v in path_pts}
    else:
        # Fewer than 2 points — can't compute a path; emit 0.0 for every date.
        portfolio_index = {p["date"]: 0.0 for p in portfolio_pts}

    # Benchmark: pure price return (price / open_price - 1), 0-based.
    bench_index: dict[str, float] = {}
    if bench_series and len(bench_series) >= 2:
        open_price = float(bench_series[0]["price"])
        if open_price > 0:
            bench_index = {
                p["date"]: round(float(p["price"]) / open_price - 1.0, 6) for p in bench_series
            }

    # Align on portfolio dates (the primary axis); fill benchmark by carry-forward.
    last_bench: float | None = None
    out: list[dict] = []
    for p in portfolio_pts:
        d = p["date"]
        p_frac = portfolio_index.get(d, 0.0)
        b_frac: float | None = bench_index.get(d)
        if b_frac is not None:
            last_bench = b_frac
        elif bench_index:
            # Carry forward the last known benchmark value to this date.
            b_frac = last_bench
        out.append({"date": d, "portfolio": p_frac, "benchmark": b_frac})
    return out


# ── Public entrypoint ──────────────────────────────────────────────────────────────


def _instrument_bands() -> dict[str, str]:
    """{instrument_id: band}, by the same asset-class rule the value series files each
    holding's value under (``portfolio_history._ASSET_CLASS_TO_BAND``)."""
    return {
        i["id"]: band
        for i in InstrumentStore().all()
        if i.get("id") and (band := _ASSET_CLASS_TO_BAND.get(i.get("asset_class") or ""))
    }


def _bands_held(raw_series: list[dict]) -> list[str]:
    """The market bands the book holds at the window's end, largest first: what a "compare
    by asset type" picker offers."""
    last = (raw_series[-1].get("classes") or {}) if raw_series else {}
    held = [(b, float(last.get(b) or 0)) for b in _MARKET_BANDS]
    return [b for b, v in sorted(held, key=lambda x: -x[1]) if v > 0]


def portfolio_returns(
    period: str = "1Y",
    today: date | None = None,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    band: str | None = None,
) -> dict:
    """Whole-investment-book TWR + MWR/XIRR + period $ gain over the requested window.

    Phase 4 changes:
      * Sources the value series from portfolio_history.portfolio_value_series (daily,
        reconstructed quantities) — TWR is now correct under mid-window trades.
      * Computes an effective window by clamping requested_start to max(inception, price_floor)
        so fresh books degrade gracefully rather than refusing.
      * Selects metrics by effective span (see _SPAN_* thresholds): annualized=True and
        MWR/XIRR both gated at ≥360 days (same threshold).
      * Adds series[], effective_start, label, annualized to the payload.
      * Passes through data_quality tiers + flags from the portfolio_history primitive.

    When data_quality.sufficient is False (< MIN_VALUE_POINTS effective value points),
    twr/mwr come back as null — NEVER a fabricated number.

    ``band`` (one of ``_MARKET_BANDS``) narrows the book to one asset type: its value is that
    band's subtotal on each day and its flows are only that band's buys and sells, so money
    moved from crypto into stocks is a withdrawal from one slice and a deposit into the other,
    never a gain or a loss of either (owner, 2026-11-13).
    """
    if band is not None and band not in _MARKET_BANDS:
        raise ValidationError(f"Unknown asset type '{band}'.")
    from backend.services import portfolio_history as ph
    from backend.services.fx_cache import load_fx_cache

    base = base_currency()
    if date_from or date_to:
        period = "CUSTOM"
    requested_start, end = resolve_window(period, today, date_from=date_from, date_to=date_to)
    rates = _load_rates_safe(load_fx_cache)

    # ── Effective window clamping ──────────────────────────────────────────────────
    market_since = PriceHistoryStore().earliest_date()
    inception = ph.inception_date()
    effective_start = _effective_window(requested_start, end, inception, market_since)
    label = _period_label(period, effective_start, requested_start)

    # ── Daily value series from the shared primitive ───────────────────────────────
    # We query [effective_start, end] so the series is already clamped.
    ph_result = ph.portfolio_value_series(effective_start, end, "daily")
    raw_series = ph_result.get("series") or []
    dq_prim = ph_result.get("data_quality") or {}

    # Map portfolio_value_series points → the {date, value} shape used by twr/period_gain.
    if band is None:
        series = [{"date": p["date"], "value": p["value_base"]} for p in raw_series]
        # Cash flows: still (effective_start, end] (start-exclusive) — matching the series.
        flows, has_unknown_cost = cash_flows(effective_start, end, base, rates=rates)
    else:
        series = [
            {"date": p["date"], "value": round(float((p.get("classes") or {}).get(band) or 0), 2)}
            for p in raw_series
        ]
        bands = _instrument_bands()
        tagged, has_unknown_cost = tagged_cash_flows(effective_start, end, base, rates=rates)
        flows = [(when, amount) for when, amount, iid in tagged if bands.get(iid) == band]

    # ── Effective span → metric tier selection ─────────────────────────────────────
    value_points = len(series)
    sufficient = value_points >= MIN_VALUE_POINTS

    try:
        span_days = (date.fromisoformat(end) - date.fromisoformat(effective_start)).days
    except (ValueError, TypeError):
        span_days = 0

    # Metric tiers: < 360d → cumulative TWR + period gain only; ≥ 360d → annualized + MWR.
    should_annualize = span_days >= _SPAN_ANNUALIZE
    should_compute_mwr = span_days >= _SPAN_MWR_MIN

    start_value = series[0]["value"] if series else 0.0
    end_value = series[-1]["value"] if series else 0.0
    # Period $ gain: end − start + net external flows (netting out deposits so a deposit
    # doesn't inflate the period gain — mirrors TWR's deposit-neutralization, as $).
    period_gain_base = round(end_value - start_value + _net_external_flow(flows), 2)

    if sufficient:
        twr_block = twr(series, flows, annualize=should_annualize)
        if should_compute_mwr:
            opening = [(effective_start, -start_value)] if start_value else []
            xirr_rate = xirr(opening + flows, end_value, end)
            if xirr_rate is not None:
                # For spans ≥ 360d xirr is already an annual rate; period_return == xirr.
                # For shorter spans de-annualize so the stat card shows the period's actual
                # money-weighted return (not the extrapolated annual figure).
                if should_annualize:
                    period_mwr = xirr_rate
                else:
                    period_mwr = round((1.0 + xirr_rate) ** (span_days / _DAYS_PER_YEAR) - 1.0, 6)
                mwr_block = {
                    "xirr": xirr_rate,
                    "period_return": period_mwr,
                    "annualized": should_annualize,
                }
            else:
                mwr_block = None
        else:
            mwr_block = None
    else:
        twr_block = None
        mwr_block = None

    return {
        "period": period.upper(),
        "band": band,
        "bands_held": _bands_held(raw_series),
        "start": requested_start,
        "end": end,
        "effective_start": effective_start,
        "label": label,
        "annualized": should_annualize,
        "base_currency": base,
        "twr": twr_block,
        "mwr": mwr_block,
        "period_gain_base": period_gain_base,
        "start_value": start_value,
        "end_value": end_value,
        # _value_pts / _flows are internal fields consumed by portfolio_returns_with_benchmark
        # to build the chart series without re-calling the primitive or re-reading LotStore.
        # Neither field is part of the public API contract.
        "_value_pts": series,
        "_flows": flows,
        "data_quality": {
            "market_since": market_since,
            "inception": inception,
            "effective_start": effective_start,
            "value_points": value_points,
            "sufficient": sufficient,
            "has_unknown_cost": has_unknown_cost or dq_prim.get("has_unknown_cost", False),
            "has_unrecorded_risk": dq_prim.get("has_unrecorded_risk", False),
            "fx_approximate": dq_prim.get("fx_approximate", False),
            "tiers": dq_prim.get("tiers") or {},
        },
    }


def portfolio_returns_with_benchmark(
    period: str = "1Y",
    benchmark: str | None = None,
    today: date | None = None,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    by_holding: bool = False,
    band: str | None = None,
) -> dict:
    """portfolio_returns() plus an optional `benchmark` comparison block and chart series.

    Phase 4: the payload also carries `series` — a date-aligned chart series of 0-based
    cumulative return fractions (NOT rebased to 100). Portfolio uses the chain-linked TWR
    path so the endpoint equals twr().period_return and contributions are stripped.
    Benchmark uses price/price_open - 1. Both lines start at 0.0 at the window open and
    are directly comparable on one Y-axis in % units.

    An unresolvable / insufficient benchmark yields comparison=null (key is present) rather
    than a fabricated figure or an error. With no benchmark, comparison is omitted.
    """
    out = portfolio_returns(period, today=today, date_from=date_from, date_to=date_to, band=band)
    period = out.get("period") or period

    # The value series + flows were already built inside portfolio_returns and are threaded
    # through as _value_pts / _flows so we don't call the O(days×instruments) primitive
    # a second time and don't re-read LotStore for the same window.
    chart_pts: list[dict] = out.pop("_value_pts", [])
    chart_flows: list[tuple[str, float]] = out.pop("_flows", [])
    effective_start = out.get("effective_start") or out["start"]
    end = out["end"]

    bench_pts: list[dict] = []
    if benchmark:
        inst = _resolve_benchmark_instrument(benchmark)
        if inst:
            pid = inst.get("price_source_id") or ""
            if pid:
                bench_pts = benchmark_price_series(pid, effective_start, end)

    out["series"] = _rebase_series(chart_pts, chart_flows, bench_pts)

    if benchmark:
        # Pass bench_pts through so _compute_benchmark_twr reuses the already-fetched series
        # rather than reading PriceHistoryStore a second time for the same window.
        out["comparison"] = portfolio_vs_benchmark(
            out, benchmark, period=period, today=today, _bench_series=bench_pts or None
        )

    if by_holding:
        from backend.services.holding_returns import holding_period_returns

        # Same window, same flows, same valuation as the portfolio figures above, so the
        # holdings' gains add up to period_gain_base.
        out["holdings"] = holding_period_returns(effective_start, end)

    return out


def _net_external_flow(flows: list[tuple[str, float]]) -> float:
    """Net pocket-flow over the window. The period $ gain = end_value - start_value +
    net_external_flow: a deposit raises end_value without being a gain, so we ADD the
    (negative) deposit flow back to net it out."""
    return round(sum(amt for _, amt in flows), 2)


def _load_rates_safe(loader) -> dict:
    """Current cached FX rates (one read), or the pivot-only fallback — defensive so a
    missing/corrupt fx_cache never crashes a pure read."""
    try:
        return loader()
    except Exception:  # pragma: no cover - defensive
        return {}

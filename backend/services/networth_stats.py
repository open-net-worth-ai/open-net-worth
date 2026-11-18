"""Net-worth-history-derived Overview figures: range deltas, saved-vs-market attribution,
month-to-date change, and which composition series actually carry data.

The monthly series itself is already backend-computed (`ledger.net_worth_history()`). What
used to happen in the browser is the SLICING AND DIFFERENCING of that series — subtracting
two points to get "up S$1,000 over 3 months". A subtraction of two money values is still
money math, and it silently paired a client-side headline against backend history points
before the single-source fix. It lives here now.
"""

from __future__ import annotations

from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.db import read_cache
from backend.services import ledger as ledger_service

# History is MONTHLY, so a range is a month count, not a day count. `None` = the full series.
RANGE_MONTHS: dict[str, int | None] = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "All": None}
COMPARISON_MONTHS = 7


def _slice(history: list[dict], months: int | None) -> list[dict]:
    if months is None:
        return history
    return history[max(0, len(history) - (months + 1)) :]


def _range_stats(history: list[dict], months: int | None) -> dict | None:
    """Delta over the range, split into `saved` (the account-only move) and `market`.

    `accounts` is the account-only figure `ledger` stamps specifically as "a consistent basis
    for the headline delta" — its move over the range IS the saved component; whatever is left
    of the net-worth move is the market component.
    """
    window = _slice(history, months)
    if len(window) < 2:
        return None
    first, last = window[0], window[-1]
    delta = round(last["net_worth"] - first["net_worth"], 2)
    saved = round(last["accounts"] - first["accounts"], 2)
    return {
        "delta": delta,
        "pct": (round((delta / abs(first["net_worth"])) * 100, 4) if first["net_worth"] else None),
        "saved": saved,
        "market": round(delta - saved, 2),
        "span_months": len(window) - 1,
        "from_date": first["date"],
        "to_date": last["date"],
    }


def _month_to_date(history: list[dict]) -> dict | None:
    """Change since the start of the current calendar month.

    The series is MONTHLY (one point per month, last entry = today), so "the value at the
    start of this month" is the last point dated BEFORE this month began — not an index
    offset, which treated months as if they were daily points.

    BOTH ends come from the history series, never from `net_worth()`. The two computations
    are known not to reconcile exactly (banding differences — the open follow-up on ADR
    2026-07-31-single-source-net-worth), so subtracting a history point from today's
    `net_worth()` charges that reconciliation gap to the month as if it were a real move.
    On this book that alone accounted for ~S$4K of a reported "this month" change. A delta
    is a comparison: both sides must come from the same computation. The headline figure
    beside it is still `net_worth()` — that is a level, not a comparison.
    """
    if not history:
        return None
    month_start = user_today().replace(day=1).isoformat()
    start_point = next((p for p in reversed(history) if p["date"] < month_start), history[0])
    latest = history[-1]
    return {
        "delta": round(latest["net_worth"] - start_point["net_worth"], 2),
        "from_date": start_point["date"],
        "from_net_worth": start_point["net_worth"],
        "to_date": latest["date"],
        "to_net_worth": latest["net_worth"],
        "basis": "history",
    }


def _composition(history: list[dict]) -> dict:
    """Which asset-class bands (and whether liabilities) actually carry a non-zero value —
    so a chart doesn't render a legend entry for a series that is flat zero."""
    present: list[str] = []
    for point in history:
        for key, value in (point.get("classes") or {}).items():
            if value and key not in present:
                present.append(key)
    return {
        "classes": present,
        "has_liabilities": any((p.get("liabilities") or 0) < -0.005 for p in history),
    }


@read_cache.cached(lambda base=None: base)
def networth_stats(base: str | None = None) -> dict:
    """The net-worth series plus every figure the Overview derives from it.

    Callers get the raw `history` too — a chart plots those points directly, which is
    rendering, not arithmetic.
    """
    base = (base or base_currency()).upper()
    epoch = read_cache.current_epoch()
    history = ledger_service.net_worth_history()
    net_worth = ledger_service.net_worth(base)["net_worth"]
    # The two reads are not one snapshot. A write landing between them (a live price refresh,
    # most often) left the series on the old prices, so every range change ended at a different
    # figure than the headline beside it. Re-read the series once when anything was written.
    if epoch is not None and read_cache.current_epoch() != epoch:
        history = ledger_service.net_worth_history()

    # Points before price collection began carry no market value, so a range that straddles
    # that boundary would read a pure bookkeeping artifact as a gain. Compare like with like.
    has_market = any(p.get("market_tracked") for p in history)
    comparable = [p for p in history if p.get("market_tracked")] if has_market else history

    return {
        "base_currency": base,
        "net_worth": net_worth,
        "history": history,
        "ranges": {name: _range_stats(comparable, months) for name, months in RANGE_MONTHS.items()},
        "comparison": _range_stats(history, COMPARISON_MONTHS - 1),
        "month_to_date": _month_to_date(history),
        "composition": _composition(history),
        "latest": history[-1] if history else None,
    }

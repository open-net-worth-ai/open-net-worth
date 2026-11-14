"""What copying a portfolio report by report would have returned, before costs and dividends.

On each report's filing date — the first day anyone outside could act on it — the copy is
rebalanced to that report's weights and held, untouched, until the next report is filed. The
path chains those holding periods. Unlike applying today's holdings to the past, it never uses
a fact nobody knew at the time: a stock bought last quarter earns nothing before it was filed,
and one sold since still earns what it did while it was held.
"""

from __future__ import annotations

from bisect import bisect_right

from backend.services.followed_portfolios.house_ptr import estimate_positions
from backend.services.followed_portfolios.model import normalize_symbol

# Each report is copied by its largest holdings: what a copy trades, and what keeps a fund with
# thousands of lines to a bounded number of price fetches.
TOP_PER_REPORT = 25


def _top(values: dict[str, float]) -> dict[str, float]:
    ranked = sorted(
        ((normalize_symbol(s), float(v)) for s, v in values.items() if s and v and v > 0),
        key=lambda kv: -kv[1],
    )
    top: dict[str, float] = {}
    for symbol, value in ranked:
        if symbol and len(top) < TOP_PER_REPORT:
            top[symbol] = top.get(symbol, 0.0) + value
    return top


def fund_points(history: list[dict]) -> list[tuple[str, dict[str, float]]]:
    """``[(filed_at, {symbol: value})]`` oldest first, one per quarterly report."""
    points = [
        (str(q["filed_at"]), _top(q.get("value") or {})) for q in history or [] if q.get("filed_at")
    ]
    return sorted((p for p in points if p[1]), key=lambda p: p[0])


def member_points(activity: list[dict]) -> list[tuple[str, dict[str, float]]]:
    """``[(filed_at, {symbol: value})]`` oldest first: after each report, what every counted
    trade filed by then adds up to (the same estimate the page shows today)."""
    counted = [t for t in activity or [] if t.get("counted") and t.get("filed_at")]
    out = []
    for day in sorted({str(t["filed_at"]) for t in counted}):
        known = [t for t in counted if str(t["filed_at"]) <= day]
        held = {p["symbol"]: p["value"] for p in estimate_positions(known, day)}
        if top := _top(held):
            out.append((day, top))
    return out


def symbols(points: list[tuple[str, dict[str, float]]]) -> set[str]:
    return {s for _, values in points for s in values}


def _close_on(closes: dict[str, float], days: list[str], day: str) -> float | None:
    """The last close on or before ``day`` (a holiday, a weekly point, a stock gone quiet)."""
    i = bisect_right(days, day)
    return closes[days[i - 1]] if i else None


def copied_path(
    points: list[tuple[str, dict[str, float]]],
    closes: dict[str, dict[str, float]],
    dates: list[str],
) -> dict:
    """The copy's growth on each of ``dates`` (from the first report's filing on), as a
    fraction of where it started, plus the lowest share of a report's value it could price.

    A holding with no price on its report's filing date (not yet listed, no ticker) is left
    out of that period and the rest are scaled up; one that stops trading is held at its last
    close until the next report."""
    if not points:
        return {"days": [], "growth": [], "covered": 0.0}
    sorted_days = {s: sorted(c) for s, c in closes.items()}
    days = [d for d in dates if d >= points[0][0]]
    growth: list[float] = []
    level, covered_low = 1.0, 1.0
    for i, (start, values) in enumerate(points):
        end = points[i + 1][0] if i + 1 < len(points) else None
        base = {}
        for symbol, value in values.items():
            price = _close_on(closes.get(symbol) or {}, sorted_days.get(symbol) or [], start)
            if price:
                base[symbol] = (value, price)
        total = sum(values.values())
        priced = sum(v for v, _ in base.values())
        covered_low = min(covered_low, priced / total if total else 0.0)

        def value_on(day: str, base=base, priced=priced) -> float:
            if not priced:
                return 1.0  # nothing priceable: the copy sits in cash for this period
            return (
                sum(
                    v * (_close_on(closes[s], sorted_days[s], day) or p) / p
                    for s, (v, p) in base.items()
                )
                / priced
            )

        for day in days:
            if day >= start and (end is None or day < end):
                growth.append(level * value_on(day))
        if end is not None:
            level *= value_on(end)
    return {"days": days[: len(growth)], "growth": growth, "covered": covered_low}

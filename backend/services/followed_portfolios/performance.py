"""How a followed portfolio's CURRENT top holdings have done, against the owner's benchmark.

This is not the fund's or the member's own return: nobody outside knows that. It is the price
return of the holdings in the newest filing, at their filed weights, over a window — "since the
filing" is what copying it on the filing date would have done, before costs. The longer windows
apply today's holdings to the past, so they flatter a portfolio that just bought its winners;
the screen says which is which.

``build_performance`` is pure. ``performance_for`` fetches daily closes (cached by
``prices.fetch_history``) and keeps its own short cache, since one answer costs a fetch per
holding.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from backend.core.user_time import user_today
from backend.services import prices
from backend.services.followed_portfolios.model import build_model, normalize_symbol
from backend.services.risk_metrics import BENCHMARK_ROSTER, preferred_benchmark

PERIODS = ("1M", "6M", "12M", "FILING", "ALL")
_FALLBACK_BENCHMARK = "SPY"
_CACHE_SECONDS = 30 * 60
_cache: dict[tuple, tuple[float, dict]] = {}


def _filled(closes: dict[str, float], dates: list[str]) -> list[float | None]:
    """The close on each date, carrying the last known one forward (a holiday on one venue)."""
    known = sorted(closes)
    out: list[float | None] = []
    i, last = 0, None
    for day in dates:
        while i < len(known) and known[i] <= day:
            last = closes[known[i]]
            i += 1
        out.append(last)
    return out


def _window(
    weights: dict[str, float],
    closes: dict[str, dict[str, float]],
    bench: dict[str, float],
    start: str,
) -> dict:
    dates = [d for d in sorted(bench) if d >= start]
    if len(dates) < 2:
        return {"status": "unavailable", "series": [], "holdings": {}, "missing": sorted(weights)}
    paths: dict[str, list[float]] = {}
    for symbol in weights:
        filled = _filled(closes.get(symbol) or {}, dates)
        if filled[0] and all(filled):
            paths[symbol] = [p / filled[0] - 1 for p in filled]  # type: ignore[operator]
    covered = sum(weights[s] for s in paths)
    if covered <= 0:
        return {"status": "unavailable", "series": [], "holdings": {}, "missing": sorted(weights)}
    base = bench[dates[0]]
    bench_path = [p / base - 1 for p in _filled(bench, dates)]  # type: ignore[operator]
    portfolio = [sum(weights[s] * paths[s][i] for s in paths) / covered for i in range(len(dates))]
    return {
        "status": "ok",
        "start": dates[0],
        "end": dates[-1],
        "return": portfolio[-1],
        "benchmark_return": bench_path[-1],
        "covered_weight": covered,
        "series": [
            {"date": d, "portfolio": round(portfolio[i], 6), "benchmark": round(bench_path[i], 6)}
            for i, d in enumerate(dates)
        ],
        "holdings": {s: paths[s][-1] for s in paths},
        "missing": sorted(set(weights) - set(paths)),
    }


def _trade_returns(activity: list[dict], closes: dict[str, dict[str, float]]) -> dict[str, float]:
    """``{"<date>|<symbol>": return since the trade became public}`` for counted trades the price
    history reaches back to. Measured from the FILING date, not the trade date: that is the
    first day a copier could have bought, so it is what copying would have returned."""
    out: dict[str, float] = {}
    for trade in activity:
        symbol = normalize_symbol(trade.get("symbol"))
        series = closes.get(symbol) or {}
        if not trade.get("counted") or not series:
            continue
        days = sorted(series)
        public = trade.get("filed_at") or trade["date"]
        at = [d for d in days if d <= public]
        # A stock with no price after the filing (delisted, renamed) has no return, not 0%.
        if at and days[0] <= public and days[-1] > at[-1]:
            out[f"{trade['date']}|{symbol}"] = series[days[-1]] / series[at[-1]] - 1
    return out


def build_performance(
    *,
    weights: dict[str, float],
    closes: dict[str, dict[str, float]],
    bench: dict[str, float],
    filed_at: str,
    today: date,
    activity: list[dict] | None = None,
    all_from: str | None = None,
) -> dict:
    """Every window in one answer, so the screen switches between them without a fetch.
    ``ALL`` starts where the portfolio's own history does (its oldest quarter on record)."""
    starts = {
        "ALL": all_from or (today - timedelta(days=731)).isoformat(),
        "1M": (today - timedelta(days=30)).isoformat(),
        "6M": (today - timedelta(days=183)).isoformat(),
        "12M": (today - timedelta(days=366)).isoformat(),
        "FILING": filed_at,
    }
    return {
        "periods": {key: _window(weights, closes, bench, starts[key]) for key in PERIODS},
        "trade_returns": _trade_returns(activity or [], closes),
    }


def closes(symbol: str) -> dict[str, float]:
    """``{day: close}``: weekly for the years the history reaches back, daily for the last one
    (a month's window still has every day; a two-year one is not 500 points)."""
    out: dict[str, float] = {}
    for timeframe in ("5Y", "1Y"):
        for p in prices.fetch_history(symbol, timeframe):
            if p.get("date") and p.get("price"):
                out[p["date"]] = float(p["price"])
    return dict(sorted(out.items()))


_closes = closes


def performance_for(follow: dict) -> dict:
    today = user_today()
    model = build_model(follow, today)
    key = preferred_benchmark()
    benchmark = BENCHMARK_ROSTER.get(key) if key != "BTC" else None
    benchmark = benchmark or _FALLBACK_BENCHMARK
    empty = {"benchmark": benchmark, "periods": {}, "trade_returns": {}}
    if not model or not model["holdings"]:
        return empty
    weights = {h["symbol"]: h["weight"] for h in model["holdings"]}
    cache_key = (follow["id"], (follow.get("current") or {}).get("filing_id"), benchmark, today)
    hit = _cache.get(cache_key)
    if hit and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    traded = {normalize_symbol(t.get("symbol")) for t in follow.get("activity") or []}
    symbols = sorted((set(weights) | traded) - {""})
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = dict(zip([benchmark, *symbols], pool.map(_closes, [benchmark, *symbols])))
    bench = fetched.pop(benchmark)
    if not bench:
        return empty
    out = {
        "benchmark": benchmark,
        **build_performance(
            weights=weights,
            closes=fetched,
            bench=bench,
            filed_at=str(model["filed_at"]),
            today=today,
            activity=follow.get("activity") or [],
            all_from=(follow.get("history") or [{}])[-1].get("as_of"),
        ),
    }
    _cache[cache_key] = (time.monotonic(), out)
    return out

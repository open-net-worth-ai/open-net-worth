"""What copying a followed portfolio would have returned, against the owner's benchmark.

This is not the fund's or the member's own return: nobody outside knows that. The line is a
copy that rebalanced to each report on the day it was filed (``copier``), so no window uses a
holding before it was public. Since the newest filing that copy IS today's holdings at their
filed weights. Each listed holding also carries its own price return over the window.

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
from backend.services.followed_portfolios import copier, share_classes
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
    listed: tuple[str, ...] = (),
    path: dict | None = None,
) -> dict:
    """The portfolio's path over the window; each holding's own return for every weighted
    symbol AND every ``listed`` one (the page lists every holding, the line weighs the top
    ones), so no listed row is left without its return. With ``path`` (the copy's growth,
    report by report) the line is that copy's, started at 0% on the window's first day."""
    if path is not None:
        return _copied_window(weights, closes, bench, start, listed, path)
    dates = [d for d in sorted(bench) if d >= start]
    if len(dates) < 2:
        return {"status": "unavailable", "series": [], "holdings": {}, "missing": sorted(weights)}
    paths: dict[str, list[float]] = {}
    for symbol in dict.fromkeys([*weights, *listed]):
        filled = _filled(closes.get(symbol) or {}, dates)
        if filled[0] and all(filled):
            paths[symbol] = [p / filled[0] - 1 for p in filled]  # type: ignore[operator]
    covered = sum(weights[s] for s in paths if s in weights)
    if covered <= 0:
        return {"status": "unavailable", "series": [], "holdings": {}, "missing": sorted(weights)}
    base = bench[dates[0]]
    bench_path = [p / base - 1 for p in _filled(bench, dates)]  # type: ignore[operator]
    portfolio = [
        sum(weights[s] * paths[s][i] for s in paths if s in weights) / covered
        for i in range(len(dates))
    ]
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


def _copied_window(
    weights: dict[str, float],
    closes: dict[str, dict[str, float]],
    bench: dict[str, float],
    start: str,
    listed: tuple[str, ...],
    path: dict,
) -> dict:
    pairs = [(d, g) for d, g in zip(path["days"], path["growth"]) if d >= start]
    if len(pairs) < 2:
        return {"status": "unavailable", "series": [], "holdings": {}, "missing": sorted(weights)}
    dates = [d for d, _ in pairs]
    own = _window(weights, closes, bench, dates[0], listed)
    base = pairs[0][1]
    portfolio = [g / base - 1 for _, g in pairs]
    bench_filled = _filled(bench, dates)
    bench_path = [p / bench_filled[0] - 1 for p in bench_filled]  # type: ignore[operator]
    return {
        "status": "ok",
        "start": dates[0],
        "end": dates[-1],
        "return": portfolio[-1],
        "benchmark_return": bench_path[-1],
        "covered_weight": path["covered"],
        "method": "copied",
        "series": [
            {"date": d, "portfolio": round(portfolio[i], 6), "benchmark": round(bench_path[i], 6)}
            for i, d in enumerate(dates)
        ],
        "holdings": own.get("holdings") or {},
        "missing": own.get("missing") or [],
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


def _history_start(follow: dict) -> str | None:
    """Where ``ALL`` starts: a fund's oldest quarter on record; for a member (no quarters) the
    quarter-end before their oldest counted trade, the same start Trading Activity's whole-range
    period uses, so the chart and the period it is shown for always cover the same days."""
    oldest = (follow.get("history") or [{}])[-1].get("as_of")
    if oldest:
        return str(oldest)
    days = [
        str(t["date"]) for t in follow.get("activity") or [] if t.get("counted") and t.get("date")
    ]
    if not days:
        return None
    first = date.fromisoformat(min(days))
    start_month = (first.month - 1) // 3 * 3 + 1
    return (date(first.year, start_month, 1) - timedelta(days=1)).isoformat()


def build_performance(
    *,
    weights: dict[str, float],
    closes: dict[str, dict[str, float]],
    bench: dict[str, float],
    filed_at: str,
    today: date,
    activity: list[dict] | None = None,
    all_from: str | None = None,
    listed: tuple[str, ...] = (),
    points: list[tuple[str, dict[str, float]]] | None = None,
) -> dict:
    """Every window in one answer, so the screen switches between them without a fetch.
    With ``points`` (each report's filing date and holdings) every window is cut from the one
    report-by-report copy and ``ALL`` starts at the first report's filing; without them, the
    newest holdings are applied over each window."""
    path = None
    if points:
        path = copier.copied_path(points, closes, sorted(bench))
        if len(path["days"]) < 2:
            path = None
        else:
            all_from = path["days"][0]
    starts = {
        "ALL": all_from or (today - timedelta(days=731)).isoformat(),
        "1M": (today - timedelta(days=30)).isoformat(),
        "6M": (today - timedelta(days=183)).isoformat(),
        "12M": (today - timedelta(days=366)).isoformat(),
        "FILING": filed_at,
    }
    return {
        "periods": {
            key: _window(weights, closes, bench, starts[key], listed, path) for key in PERIODS
        },
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


def listing_exchanges(symbols: list[str]) -> dict[str, str]:
    """``{symbol: exchange}`` from each ticker's OWN quote, never a name search (which once named
    Milan for a Nasdaq stock). A ticker whose quote says nothing is left out, not guessed."""
    wanted = sorted({s for s in symbols if s})
    try:
        quotes = prices.fetch_quote_snapshots(wanted)
    except Exception:  # an exchange label is never worth failing the page over
        return {}
    return {s: str(q["exchange"]) for s, q in quotes.items() if s in wanted and q.get("exchange")}


def performance_for(follow: dict) -> dict:
    follow = share_classes.grouped(follow)
    today = user_today()
    model = build_model(follow, today)
    key = preferred_benchmark()
    benchmark = BENCHMARK_ROSTER.get(key) if key != "BTC" else None
    benchmark = benchmark or _FALLBACK_BENCHMARK
    empty = {"benchmark": benchmark, "periods": {}, "trade_returns": {}}
    if not model or not model["holdings"]:
        return empty
    weights = {h["symbol"]: h["weight"] for h in model["holdings"]}
    # The reports the copy is built from are part of the answer: a refresh that reads further
    # back (or a member's new report) is a different answer under the same newest filing.
    reports = tuple(q.get("filed_at") for q in follow.get("history") or [])
    filed = tuple(sorted({str(t.get("filed_at")) for t in follow.get("activity") or []}))
    cache_key = (
        follow["id"],
        (follow.get("current") or {}).get("filing_id"),
        reports,
        filed,
        benchmark,
        today,
    )
    hit = _cache.get(cache_key)
    if hit and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    traded = {normalize_symbol(t.get("symbol")) for t in follow.get("activity") or []}
    points = (
        copier.member_points(follow.get("activity") or [])
        if follow.get("kind") == "house_member"
        else copier.fund_points(follow.get("history") or [])
    )
    # Every holding the page lists gets its own return, not only the top ones the line weighs.
    every = build_model(follow, today, None) or {}
    listed = tuple(h["symbol"] for h in every.get("holdings") or [])
    symbols = sorted((set(weights) | traded | set(listed) | copier.symbols(points)) - {""})
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
            all_from=_history_start(follow),
            listed=listed,
            points=points,
        ),
        "exchanges": listing_exchanges([*listed, *traded]),
    }
    _cache[cache_key] = (time.monotonic(), out)
    return out

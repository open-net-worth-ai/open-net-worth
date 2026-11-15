"""A paper portfolio's performance: its time-weighted return from the daily value snapshots
(deposits and withdrawals neutralised by ``services.returns``' own chain), beside the benchmark
and your real investments over the same days. Read-only — your real book's line comes from the
returns authority, and nothing here feeds back into it."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from backend.core.config import get_settings
from backend.core.user_time import user_today
from backend.services import returns
from backend.services.followed_portfolios import performance as followed_performance
from backend.services.paper_trading.book import Book

logger = logging.getLogger(__name__)

RANGES = {"1M": 31, "3M": 92, "1Y": 366, "ALL": None}


def _value_points(values: list[dict], value_now: float | None) -> list[dict]:
    pts = [
        {"date": v["date"], "value": float(v["value_home"])}
        for v in values
        if v.get("value_home") is not None
    ]
    today = user_today().isoformat()
    if value_now is not None:
        pts = [p for p in pts if p["date"] != today] + [{"date": today, "value": float(value_now)}]
    return pts


def _flows(row: dict, book: Book, rates: dict | None = None) -> list[tuple[str, float]]:
    """Pocket convention: money added is negative, money taken out positive. A foreign leg is
    valued at ``rates`` (price of one unit in home)."""
    from backend.services.paper_trading.valuation import flow_amount_home

    out = []
    for f in book.flows:
        amt = flow_amount_home(f, row["home_currency"], rates)
        if amt:
            out.append((str(f.get("at") or "")[:10], -amt))
    return out


def _path(points: list[dict], flows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    if len(points) < 2:
        return [(points[0]["date"], 0.0)] if points else []
    result = returns._twr_path(points, flows)
    return result[0] if result else []


def _benchmark_closes(symbol: str) -> dict[str, float]:
    if not symbol or get_settings().onw_qa_mode:
        return {}
    try:
        return followed_performance.closes(symbol)
    except Exception:
        logger.info("paper: benchmark %s unavailable", symbol, exc_info=True)
        return {}


def _relative(closes: dict[str, float], start: str, end: str) -> float | None:
    before = [d for d in closes if d <= start]
    upto = [d for d in closes if d <= end]
    if not before or not upto:
        return None
    a, b = closes[max(before)], closes[max(upto)]
    return round(b / a - 1.0, 6) if a > 0 else None


def _your_line(start: str) -> dict[str, float]:
    """Your real investments' cumulative return by day, rebased to ``start``."""
    if get_settings().onw_qa_mode:
        return {}
    try:
        series = returns.portfolio_returns_with_benchmark("ALL").get("series") or []
    except Exception:
        logger.info("paper: your returns unavailable", exc_info=True)
        return {}
    line = {p["date"]: p.get("portfolio") for p in series if p.get("portfolio") is not None}
    base_days = [d for d in line if d <= start]
    base = 1.0 + float(line[max(base_days)]) if base_days else None
    if not base:
        return {}
    return {d: round((1.0 + float(v)) / base - 1.0, 6) for d, v in line.items() if d >= start}


def summary(
    row: dict, book: Book, values: list[dict], value_now: float | None, *, with_yours: bool = False
) -> dict:
    points = _value_points(values, value_now)
    path = _path(points, _flows(row, book))
    ret = path[-1][1] if len(path) > 1 else (0.0 if path else None)
    start = str(row.get("started_at") or "")[:10] or user_today().isoformat()
    end = points[-1]["date"] if points else user_today().isoformat()
    bench = _relative(_benchmark_closes(str(row.get("benchmark") or "")), start, end)
    out = {"return_pct": ret, "benchmark_return_pct": bench}
    if with_yours:
        yours = _your_line(start)
        days = [d for d in yours if d <= end]
        out["your_return_pct"] = yours[max(days)] if days else None
    return out


def series(
    row: dict, book: Book, values: list[dict], value_now: float | None, rng: str = "ALL"
) -> dict:
    """The chart: the paper line, the benchmark and your investments, each a cumulative
    fraction from the range's first day."""
    points = _value_points(values, value_now)
    days = RANGES.get(rng.upper())
    if days is not None and points:
        cut = (user_today() - timedelta(days=days)).isoformat()
        points = [p for p in points if p["date"] >= cut] or points[-1:]
    path = dict(_path(points, _flows(row, book)))
    start = points[0]["date"] if points else user_today().isoformat()
    closes = _benchmark_closes(str(row.get("benchmark") or ""))
    yours = _your_line(start)
    out = []
    for p in points:
        out.append(
            {
                "date": p["date"],
                "value": p["value"],
                "paper": path.get(p["date"], 0.0),
                "benchmark": _relative(closes, start, p["date"]),
                "yours": yours.get(p["date"]),
            }
        )
    last = out[-1] if out else {}
    return {
        "series": out,
        "return_pct": last.get("paper"),
        "benchmark": {"symbol": row.get("benchmark"), "return_pct": last.get("benchmark")}
        if row.get("benchmark")
        else None,
        "yours_return_pct": last.get("yours"),
        "simulated": True,
    }


def trading_days(start: date, end: date) -> list[str]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out

"""A paper portfolio's performance: its time-weighted return from the daily value snapshots
(deposits and withdrawals neutralised by ``services.returns``' own chain), beside the benchmark
and your real investments over the same days. Read-only — your real book's line comes from the
returns authority, and nothing here feeds back into it."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from backend.core.config import get_settings
from backend.core.user_time import user_today
from backend.services import returns
from backend.services.followed_portfolios import performance as followed_performance
from backend.services.paper_trading.book import Book

logger = logging.getLogger(__name__)

RANGES = {"1W": 8, "1M": 31, "3M": 92, "1Y": 366, "ALL": None}


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


def _flows(
    row: dict, book: Book, rates: dict | None = None, *, from_start: bool = False
) -> list[tuple[str, float]]:
    """Pocket convention: money added is negative, money taken out positive. A foreign leg is
    valued at ``rates`` (price of one unit in home). ``from_start``: the run's starting money
    is the line's first point (`_start_point`), so it is not also a deposit into it."""
    from backend.services.paper_trading.valuation import flow_amount_home

    out = []
    for f in book.flows:
        if from_start and f.get("opening"):
            continue
        amt = flow_amount_home(f, row["home_currency"], rates)
        if amt:
            out.append((str(f.get("at") or "")[:10], -amt))
    return out


def _start_day(row: dict) -> str:
    return str(row.get("started_at") or "")[:10] or user_today().isoformat()


def _start_point(row: dict, book: Book) -> dict | None:
    """The run's starting money, dated the day before it started, so the line and the return
    begin when the portfolio was created — day one's move counted, a chart from day one."""
    home = row["home_currency"]
    legs = [
        (ccy, float(amt))
        for f in book.flows
        if f.get("opening")
        for ccy, amt in (f.get("cash_delta") or {}).items()
    ]
    # A foreign leg has no rate here: no start point beats a start valued too low.
    if not legs or any(ccy != home for ccy, _ in legs):
        return None
    opening = sum(amt for _, amt in legs)
    if opening <= 0:
        return None
    before = date.fromisoformat(_start_day(row)) - timedelta(days=1)
    return {"date": before.isoformat(), "value": round(opening, 2), "start": True}


def _points_from_start(
    row: dict, book: Book, values: list[dict], value_now: float | None
) -> list[dict]:
    points = _value_points(values, value_now)
    first = _start_point(row, book)
    return ([first] if first else []) + [
        p for p in points if not first or p["date"] > first["date"]
    ]


def _line(row: dict, book: Book, values: list[dict], value_now: float | None):
    """(points, flows) for the return: from the run's start when its starting money is known,
    else from the first daily value as before."""
    first = _start_point(row, book)
    points = _points_from_start(row, book, values, value_now)
    return points, _flows(row, book, from_start=first is not None)


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
    points, flows = _line(row, book, values, value_now)
    path = _path(points, flows)
    ret = path[-1][1] if len(path) > 1 else (0.0 if path else None)
    # The benchmark and your line start where the paper line does: the day before the run when
    # it starts from its starting money (that day's close is the last price before it).
    start = points[0]["date"] if points else _start_day(row)
    end = points[-1]["date"] if points else user_today().isoformat()
    bench = _relative(_benchmark_closes(str(row.get("benchmark") or "")), start, end)
    out = {"return_pct": ret, "benchmark_return_pct": bench}
    if with_yours:
        yours = _your_line(start)
        days = [d for d in yours if d <= end]
        out["your_return_pct"] = yours[max(days)] if days else None
    return out


# The intraday line covers the first week: the daily line is a handful of points until then, and
# the five-day intraday bars reach no further back.
INTRADAY_DAILY_POINTS = 6


def _intraday(row: dict, book: Book, events: list[dict] | None, points, flows, value_now):
    """The first week through the day, while the daily line is too short to be a chart; None
    when that does not apply or there are no intraday prices to draw it with."""
    from backend.services.paper_trading import intraday

    first = points[0] if points else None
    if (
        events is None
        or not first
        or not first.get("start")
        or len(points) > INTRADAY_DAILY_POINTS
        or flows
    ):
        return None
    closes = _benchmark_closes(str(row.get("benchmark") or ""))
    before = [d for d in closes if d <= first["date"]]
    try:
        out = intraday.line(
            row,
            events,
            opening=first["value"],
            start_day=_start_day(row),
            value_now=value_now,
            bench_base=closes[max(before)] if before else None,
            now=datetime.now(timezone.utc),
        )
    except Exception:
        logger.info("paper: intraday line unavailable", exc_info=True)
        return None
    return out or None


def series(
    row: dict,
    book: Book,
    values: list[dict],
    value_now: float | None,
    rng: str = "ALL",
    *,
    events: list[dict] | None = None,
) -> dict:
    """The chart: the paper line, the benchmark and your investments, each a cumulative
    fraction from the range's first day. In the first days, with ``events``, the line is
    drawn through the day instead (``intraday``)."""
    points, flows = _line(row, book, values, value_now)
    through_day = _intraday(row, book, events, points, flows, value_now)
    if through_day:
        last = through_day[-1]
        yours = _your_line(points[0]["date"])
        days = [d for d in yours if d <= points[-1]["date"]]
        return {
            "series": through_day,
            "intraday": True,
            "return_pct": last["paper"],
            "benchmark": {"symbol": row.get("benchmark"), "return_pct": last["benchmark"]}
            if row.get("benchmark")
            else None,
            "yours_return_pct": yours[max(days)] if days else None,
            "simulated": True,
        }
    days = RANGES.get(rng.upper())
    if days is not None and points:
        cut = (user_today() - timedelta(days=days)).isoformat()
        points = [p for p in points if p["date"] >= cut] or points[-1:]
    path = dict(_path(points, flows))
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
                # Your line is rebased to this day, so it is 0 here even on a day with no mark.
                "yours": 0.0 if p["date"] == start and yours else yours.get(p["date"]),
                "start": bool(p.get("start")),
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
        "intraday": False,
        "simulated": True,
    }


def trading_days(start: date, end: date) -> list[str]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out

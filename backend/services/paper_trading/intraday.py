"""A paper portfolio's first days, through the day: the book replayed at each intraday bar of
what it trades, beside the benchmark's own bars. Two daily points (the start and now) drew a
straight line; this draws the day as it went. Read-only, and only while the daily line is too
short to be a chart — after that the daily line is the chart.

Same anchors as the daily line, so the two never disagree: the paper line starts from the
starting money, the benchmark from its last close before the start day."""

from __future__ import annotations

import bisect
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from backend.core.config import get_settings
from backend.services import prices
from backend.services.paper_trading import assets, quotes
from backend.services.paper_trading.book import Book, apply
from backend.services.paper_trading.kinds import kind_for
from backend.services.paper_trading.model import PaperQuote

logger = logging.getLogger(__name__)

MAX_POINTS = 160
Bars = list[tuple[datetime, float]]
Fetch = Callable[[str, str], Bars]


def _ts(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def bars(symbol: str, price_source: str = "") -> Bars:
    """Intraday bars ``(time, price)``, oldest first; [] when the source has none (a fund, a
    price you set) or is unreachable. A daily-only series is no use here and is dropped."""
    if not symbol or get_settings().onw_qa_mode:
        return []
    for window in ("5D", "1D"):
        try:
            pts = prices.fetch_history(symbol, window, price_source or "yahoo")
        except Exception:
            logger.info("paper: intraday bars for %s unavailable", symbol, exc_info=True)
            return []
        out = [
            (t, float(p["price"]))
            for p in pts
            if "T" in str(p.get("date") or "") and p.get("price")
            for t in [_ts(p["date"])]
            if t
        ]
        if out:
            return sorted(out)
    return []


def _local_day(t: datetime) -> str:
    return t.isoformat()[:10]


def _price_at(line: Bars, times: list[datetime], t: datetime) -> float | None:
    i = bisect.bisect_right(times, t)
    return line[i - 1][1] if i else None


def _thin(grid: list[datetime]) -> list[datetime]:
    if len(grid) <= MAX_POINTS:
        return grid
    step = len(grid) / (MAX_POINTS - 1)
    picked = [grid[int(i * step)] for i in range(MAX_POINTS - 1)]
    return sorted(set(picked)) + [grid[-1]]


class _Prices:
    """Each traded asset's price at a moment: its newest bar or fill at or before it."""

    def __init__(self, events: list[dict], fetch: Fetch):
        self.lines: dict[str, Bars] = {}
        self.assets = {}
        for ev in events:
            key = ev.get("asset_key")
            if ev.get("kind") not in ("buy", "sell") or not key:
                continue
            if key not in self.assets:
                a = assets.resolve_key(key)
                self.assets[key] = a
                self.lines[key] = list(fetch(a.symbol, a.price_source))
            at = _ts(ev.get("at"))
            if at and ev.get("price"):
                self.lines[key].append((at, float(ev["price"])))
        self.times = {}
        for key, line in self.lines.items():
            line.sort()
            self.times[key] = [t for t, _ in line]

    def bar_times(self) -> list[datetime]:
        return [t for line in self.lines.values() for t, _ in line]

    def value(self, book: Book, t: datetime, rates: dict[str, float], home: str) -> float:
        total = sum(amt * (rates.get(ccy) or 0.0) for ccy, amt in book.cash.items())
        for key in book.held_keys():
            a = self.assets.get(key)
            price = _price_at(self.lines.get(key, []), self.times.get(key, []), t)
            if not a or price is None:
                continue
            q = PaperQuote(last=price, currency=a.currency, as_of=t, basis="quote", source="bar")
            native = kind_for(a).value(book.quantity(key), q) or 0.0
            total += native * (rates.get(a.currency) or 0.0)
        return total


def _bench_line(bench: Bars, base: float | None, start_day: str) -> tuple[Bars, float | None]:
    """The benchmark's bars from the start day on, and the price they are measured from: the
    daily close before the start day, else its last bar before it."""
    before = [p for t, p in bench if _local_day(t) < start_day]
    base = base or (before[-1] if before else None)
    return [(t, p) for t, p in bench if _local_day(t) >= start_day], base


def line(
    row: dict,
    events: list[dict],
    *,
    opening: float,
    start_day: str,
    value_now: float | None,
    bench_base: float | None,
    now: datetime,
    fetch: Fetch = bars,
) -> list[dict]:
    """``[{"date": ISO time, "value", "paper", "benchmark", "start"}]`` from the close before the
    start day to now; [] when there is nothing intraday to draw."""
    home = row["home_currency"]
    start_at = _ts(row.get("started_at"))
    if not start_at or opening <= 0:
        return []
    ordered = sorted(events, key=lambda e: str(e.get("at") or ""))
    px = _Prices(ordered, fetch)
    bench, base = _bench_line(
        fetch(str(row.get("benchmark") or ""), "yahoo"), bench_base, start_day
    )
    currencies = {a.currency for a in px.assets.values()} | {
        c for e in ordered for c in (e.get("cash_delta") or {})
    }
    rates = {home: 1.0} if currencies <= {home} else quotes.fx_rates(currencies, home)
    grid = sorted(
        {
            t
            for t in px.bar_times() + [t for t, _ in bench]
            if _local_day(t) >= start_day and t <= now
        }
    )
    if not grid:
        return []
    bench_times = [t for t, _ in bench]
    book, i = Book(), 0
    out = [_point(grid[0] - timedelta(minutes=1), opening, opening, 0.0 if base else None, True)]
    for t in _thin(grid):
        while i < len(ordered) and (_ts(ordered[i].get("at")) or now) <= t:
            apply(book, ordered[i])
            i += 1
        value = px.value(book, t, rates, home) if t >= start_at else opening
        b = _price_at(bench, bench_times, t)
        out.append(_point(t, value, opening, b / base - 1.0 if b and base else None))
    if value_now is not None and now > grid[-1]:
        b = bench[-1][1] if bench else None
        out.append(_point(now, value_now, opening, b / base - 1.0 if b and base else None))
    elif value_now is not None:
        out[-1] = _point(out[-1]["_t"], value_now, opening, out[-1]["benchmark"])
    for p in out:
        p.pop("_t")
    return out if len(out) >= 3 else []


def _point(t: datetime, value: float, opening: float, bench: float | None, start=False) -> dict:
    return {
        "_t": t,
        "date": t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "value": round(value, 2),
        "paper": round(value / opening - 1.0, 6),
        "benchmark": round(bench, 6) if bench is not None else None,
        "yours": None,
        "start": start,
    }

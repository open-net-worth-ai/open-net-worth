"""Your real investments through the day, for a paper portfolio's first-week chart: the same
reconstruction as the paper line, on your real book. Read-only.

At each chart point, the holdings you had that day (``portfolio_history.holdings_series``, from
your lots) are valued at each one's newest intraday bar at or before it (the paper line's bars),
or its last daily close before that day when it has none (a fund, a price you set). Days are
chained, so a buy or a sale moves the line only by what it then earns, never by its size. A trade
has a date but no time, so a day's trades count from that day's start."""

from __future__ import annotations

import bisect
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from backend.core.config import get_settings
from backend.core.fx import base_currency
from backend.core.user_time import user_now
from backend.db.instrument_store import InstrumentStore
from backend.db.price_history_store import PriceHistoryStore
from backend.services import portfolio_history
from backend.services.fx_cache import load_fx_cache
from backend.services.paper_trading import intraday

logger = logging.getLogger(__name__)

# One chart is drawn from many holdings' bars: kept briefly so the page's polling never refetches.
CACHE_S = 120
_FETCHERS = 8
_cache: dict[tuple, tuple[float, dict[str, float]]] = {}

Bars = intraday.Bars


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def _price_at(line: Bars, times: list[datetime], t: datetime) -> float | None:
    i = bisect.bisect_right(times, t)
    return line[i - 1][1] if i else None


class _Book:
    """Your holdings by day, and each one's price at a moment."""

    def __init__(self, first_day: str, last_day: str, fetch=intraday.bars):
        before = (date.fromisoformat(first_day) - timedelta(days=1)).isoformat()
        series = portfolio_history.holdings_series(before, last_day, "daily")
        self.by_day = {p["date"]: p["holdings"] for p in series}
        self.days = sorted(self.by_day)
        ids = {iid for h in self.by_day.values() for iid in h}
        insts = {i["id"]: i for i in InstrumentStore().all() if i.get("id") in ids}
        self.instruments = insts
        self.closes = PriceHistoryStore().as_of_maps(self.days) if self.days else {}
        self.base = base_currency()
        self.rates = load_fx_cache() or {}
        self.lines: dict[str, Bars] = {}
        self.times: dict[str, list[datetime]] = {}
        wanted = [i for i in insts.values() if i.get("price_source_id")]
        with ThreadPoolExecutor(max_workers=_FETCHERS) as pool:
            fetched = pool.map(
                lambda i: fetch(i["price_source_id"], i.get("price_source") or ""), wanted
            )
            for inst, line in zip(wanted, fetched):
                self.lines[inst["id"]] = line
                self.times[inst["id"]] = [t for t, _ in line]

    def holdings(self, day: str) -> dict[str, float]:
        i = bisect.bisect_right(self.days, day)
        return self.by_day[self.days[i - 1]] if i else {}

    def _close_before(self, day: str) -> dict[str, float]:
        i = bisect.bisect_left(self.days, day)
        return self.closes.get(self.days[i - 1], {}) if i else {}

    def value(self, held: dict[str, float], t: datetime, day: str) -> float:
        closes = self._close_before(day)
        total = 0.0
        for iid, qty in held.items():
            inst = self.instruments.get(iid)
            if not inst:
                continue
            pid = inst.get("price_source_id") or ""
            price = _price_at(self.lines.get(iid, []), self.times.get(iid, []), t)
            if price is None:
                price = closes.get(pid)
            prices = {pid: price} if price is not None else {}
            v = portfolio_history.instrument_value_base(inst, qty, prices, self.base, self.rates)
            total += v or 0.0
        return total


def line(stamps: list[str], *, fetch=intraday.bars) -> dict[str, float]:
    """``{stamp: return since the first stamp}`` for your real investments, one per chart point;
    {} when there is nothing to value (no holdings, or the QA market)."""
    if len(stamps) < 2 or get_settings().onw_qa_mode:
        return {}
    tz = user_now().tzinfo or timezone.utc
    moments = [(s, _ts(s)) for s in stamps]
    day_of = {s: t.astimezone(tz).date().isoformat() for s, t in moments}
    key = (stamps[0], stamps[-1], len(stamps), int(time.time() // CACHE_S))
    hit = _cache.get(key)
    if hit:
        return hit[1]
    try:
        book = _Book(day_of[stamps[0]], day_of[stamps[-1]], fetch)
        out = _chain(book, moments, day_of)
    except Exception:
        logger.info("paper: your intraday line unavailable", exc_info=True)
        return {}
    _cache.clear()
    _cache[key] = (time.time(), out)
    return out


def _chain(book: _Book, moments: list[tuple[str, datetime]], day_of: dict[str, str]):
    """Chain-linked through the points: each step is the move of the holdings held across it,
    and a change of holdings (a new day's trades) starts a new step from the same level."""
    first, t0 = moments[0]
    held = book.holdings(day_of[first])
    prev = book.value(held, t0, day_of[first])
    if prev <= 0:
        return {}
    level, out = 1.0, {first: 0.0}
    for stamp, t in moments[1:]:
        day = day_of[stamp]
        now_held = book.holdings(day)
        old = book.value(held, t, day)
        level *= old / prev if prev else 1.0
        if now_held is not held and now_held != held:
            held = now_held
            prev = book.value(held, t, day)
        else:
            prev = old
        out[stamp] = round(level - 1.0, 6)
        if prev <= 0:
            break
    return out

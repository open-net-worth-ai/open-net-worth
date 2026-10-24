"""prices_service — live price fetching, name/sector caching.

Extracted from api/prices.py so ai/tools/_shared and other services can import
the price subsystem without coupling to the HTTP layer (api → services → db → core).
api/prices.py now imports from here and provides thin route handlers.
"""

from __future__ import annotations

import bisect
import concurrent.futures
import json
import logging
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yfinance as yf

from backend.core.config import get_settings
from backend.core.errors import ValidationError
from backend.core.fx import base_currency as _base_currency
from backend.core.instrument_display import display_name
from backend.core.user_time import user_today
from backend.models.price import PriceQuote
from backend.paths import (
    NAMES_CACHE_JSON as _NAMES_CACHE_FILE,
)
from backend.paths import (
    SECTORS_CACHE_JSON as _SECTORS_CACHE_FILE,
)
from backend.services import coingecko_client
from backend.services import market_data_observability as observability
from backend.services.fx_cache import load_fx_cache, save_fx_cache

_log = logging.getLogger(__name__)


def _base() -> str:
    return _base_currency()


def save_names_cache(names: dict[str, str]) -> None:
    try:
        with open(_NAMES_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(names, f)
    except Exception:
        pass


def load_names_cache() -> dict[str, str]:
    try:
        if _NAMES_CACHE_FILE.exists():
            with open(_NAMES_CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_sectors_cache(sectors: dict[str, str]) -> None:
    try:
        with open(_SECTORS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(sectors, f)
    except Exception:
        pass


def load_sectors_cache() -> dict[str, str]:
    try:
        if _SECTORS_CACHE_FILE.exists():
            with open(_SECTORS_CACHE_FILE, encoding="utf-8") as f:
                return {k: v for k, v in json.load(f).items() if v}
    except Exception:
        pass
    return {}


_NAMES_CACHE: dict[str, str] = load_names_cache()
_EXCHANGE_CACHE: dict[str, str] = {}
# Listing/quote currency per symbol (Yahoo `info["currency"]`). In-memory only — mirrors
# _EXCHANGE_CACHE (repopulates from a live fetch; no separate persisted file). Lets a price
# display (e.g. the Peers card) label a cross-listed ticker's quote currency.
_CURRENCY_CACHE: dict[str, str] = {}
_SECTOR_CACHE: dict[str, str] = load_sectors_cache()
_DIV_RATE_CACHE: dict[str, float] = {}

# Live-price cache: keyed by (sorted tickers, sorted currencies, include_names).
# Avoids hitting Yahoo/CoinGecko on every page load; staleness is acceptable for UI.
_PRICE_CACHE_TTL = 60.0  # seconds
_price_cache: dict[str, tuple[float, dict]] = {}  # key → (fetched_at, result)


def _fetch_name(symbol: str) -> tuple[str, str, str]:
    """Fetch (symbol, name, exchange) — and opportunistically cache sector, div rate, currency."""
    # Gate on currency too so a warm name cache (seeded before currency was tracked) still
    # backfills the listing currency on its next touch instead of staying blank forever.
    if symbol in _NAMES_CACHE and symbol in _CURRENCY_CACHE:
        return symbol, _NAMES_CACHE[symbol], _EXCHANGE_CACHE.get(symbol, "")
    try:
        info = yf.Ticker(symbol).info
        name = info.get("longName") or info.get("shortName") or ""
        exchange = info.get("fullExchangeName") or ""
        sector = info.get("sector") or ""
        currency = (info.get("currency") or "").upper()
        div_rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate") or 0
        if name:
            _NAMES_CACHE[symbol] = name
        if exchange:
            _EXCHANGE_CACHE[symbol] = exchange
        if sector:
            _SECTOR_CACHE[symbol] = sector
        # Cache even an empty currency so a currency-less ticker doesn't re-hit .info every call.
        _CURRENCY_CACHE[symbol] = currency
        if div_rate:
            _DIV_RATE_CACHE[symbol] = float(div_rate)
        return symbol, name, exchange
    except Exception:
        return symbol, "", ""


def _fetch_sector(symbol: str) -> str:
    if _SECTOR_CACHE.get(symbol):
        return _SECTOR_CACHE[symbol]
    try:
        sector = yf.Ticker(symbol).info.get("sector") or ""
    except Exception:
        sector = ""
    if sector:
        _SECTOR_CACHE[symbol] = sector
    return sector


def _backfill_dividend_rates() -> None:
    """Persist freshly-fetched forward dividend rates onto matching instruments. Best-effort.

    Never overwrites a user-confirmed rate (dividend_rate_source=="manual") — one-way
    ratchet, same as classification_source. A row this DOES write is stamped
    dividend_rate_source="enriched" so the income forecast can tell "system-fetched" from
    "user-declared" (they used to share the field with no way to distinguish them)."""
    if not _DIV_RATE_CACHE:
        return
    try:
        from backend.db.instrument_store import InstrumentStore

        store = InstrumentStore()
        for inst in store.all():
            if (inst.get("price_source") or "").lower() != "yahoo":
                continue
            if inst.get("dividend_rate_source") == "manual":
                continue
            rate = _DIV_RATE_CACHE.get(inst.get("price_source_id", ""))
            if rate is None:
                continue
            if (
                inst.get("dividend_rate_annual") != rate
                or inst.get("dividend_rate_source") != "enriched"
            ):
                store.update_by_id(
                    inst["id"],
                    {"dividend_rate_annual": rate, "dividend_rate_source": "enriched"},
                )
    except Exception:
        _log.debug("dividend-rate backfill failed", exc_info=True)


def enrich_instrument_sectors() -> dict:
    """Backfill `sector` onto yahoo-priced instruments that don't have one yet."""
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    targets = [
        i
        for i in store.all()
        if (i.get("price_source") or "").lower() == "yahoo"
        and not i.get("sector")
        and i.get("price_source_id")
    ]
    if not targets:
        return {"checked": 0, "updated": 0}

    symbols = [i["price_source_id"] for i in targets]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(symbols), 14)) as ex:
        fetched = dict(zip(symbols, ex.map(_fetch_sector, symbols)))
    save_sectors_cache(_SECTOR_CACHE)

    updated = 0
    for inst in targets:
        sector = fetched.get(inst["price_source_id"], "")
        if sector:
            store.update_by_id(inst["id"], {"sector": sector})
            updated += 1
    _log.info("sector enrich: checked %d, updated %d", len(targets), updated)
    return {"checked": len(targets), "updated": updated}


# ── Dividend calendar metadata (ADR 0020) ──────────────────────────────────────────
# Cached onto the instrument by a daily scheduled job. The forward dates ride FREE on the
# `.info` call (Yahoo's quoteSummary), and `.dividends` adds one chart request per symbol.
# All read-time projection lives in services/dividend_calendar.py — here we only fetch + persist.

_FREQ_BY_DAYS = (  # (max avg gap in days, label) — first match wins, ascending
    (45, "monthly"),
    (115, "quarterly"),
    (250, "semi-annual"),
)


def _epoch_to_iso(v) -> str | None:
    """A Yahoo epoch-seconds timestamp → ISO ``YYYY-MM-DD`` (UTC), or None if unparseable."""
    if not v:
        return None
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _dividend_history_points(hist) -> list[tuple[str, float]]:
    """Coerce a yfinance ``Ticker.dividends`` Series (ex-date → per-share amount) into a
    plain, ascending ``[(iso_date, amount)]`` list. None / unparseable → []."""
    if hist is None:
        return []
    points: list[tuple[str, float]] = []
    try:
        for ts, amt in hist.items():
            iso = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
            try:
                points.append((iso, float(amt)))
            except (TypeError, ValueError):
                continue
    except Exception:
        return []
    return sorted(p for p in points if p[0])


def _summarize_dividend_history(
    points: list[tuple[str, float]],
) -> tuple[str | None, str | None, float | None]:
    """(last_ex_date, frequency, last_amount) from ascending dividend points. Frequency is
    inferred from the average spacing of the last ~5 ex-dates (pure; testable in isolation)."""
    if not points:
        return None, None, None
    last_ex, last_amt = points[-1]
    freq: str | None = None
    if len(points) >= 2:
        recent = points[-5:]
        gaps = [
            date.fromisoformat(recent[i + 1][0]).toordinal()
            - date.fromisoformat(recent[i][0]).toordinal()
            for i in range(len(recent) - 1)
        ]
        gaps = [g for g in gaps if g > 0]
        if gaps:
            avg = sum(gaps) / len(gaps)
            freq = next((label for cap, label in _FREQ_BY_DAYS if avg <= cap), "annual")
    return last_ex, freq, last_amt


def _fetch_dividend_meta(symbol: str) -> dict:
    """Fetch dividend calendar metadata for ONE yahoo symbol (best-effort, network).
    Returns ONLY positively-resolved instrument fields (omit-unknowns, like
    ``_backfill_dividend_rates``) so a transient empty response never nulls good data. A
    stale (now-past) ``next_ex_date`` is harmless — the calendar service ignores any
    ``next_ex_date`` that isn't in the future and falls back to projection."""
    out: dict = {}
    try:
        tk = yf.Ticker(symbol)
        info = tk.info or {}
    except Exception:
        return out
    rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate") or 0
    if rate:
        out["dividend_rate_annual"] = float(rate)
    today_iso = user_today().isoformat()
    ex = _epoch_to_iso(info.get("exDividendDate"))
    pay = _epoch_to_iso(info.get("dividendDate"))
    if ex and ex >= today_iso:  # store DECLARED dates only when genuinely upcoming
        out["next_ex_date"] = ex
    if pay and pay >= today_iso:
        out["next_pay_date"] = pay
    try:
        hist = tk.dividends
    except Exception:
        hist = None
    last_ex, freq, last_amt = _summarize_dividend_history(_dividend_history_points(hist))
    if last_ex:
        out["last_ex_date"] = last_ex
    if freq:
        out["dividend_frequency"] = freq
    amount = info.get("lastDividendValue") or last_amt
    if amount:
        out["last_dividend_amount"] = float(amount)
    return out


def enrich_dividend_meta() -> dict:
    """Refresh cached dividend calendar metadata onto every yahoo-priced instrument (ADR 0020).
    Daily scheduled job (`refresh_dividend_meta`). Best-effort; errors log type-name only. NOT on
    the page-load hot path — the calendar reads cached instrument fields + projects in-process.

    dividend_rate_annual is ratchet-guarded like `_backfill_dividend_rates`: a row already at
    dividend_rate_source=="manual" keeps its user-confirmed rate untouched (the calendar dates/
    frequency/last-dividend fields still refresh normally — those have no manual-override
    provenance to protect). A rate this DOES write is stamped dividend_rate_source="enriched"."""
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    targets = [
        i
        for i in store.all()
        if (i.get("price_source") or "").lower() == "yahoo" and i.get("price_source_id")
    ]
    if not targets:
        return {"checked": 0, "updated": 0}

    symbols = sorted({i["price_source_id"] for i in targets})
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(symbols), 14)) as ex:
        fetched = dict(zip(symbols, ex.map(_fetch_dividend_meta, symbols)))

    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    for inst in targets:
        meta = dict(fetched.get(inst.get("price_source_id", "")) or {})
        if not meta:
            continue
        if "dividend_rate_annual" in meta:
            if inst.get("dividend_rate_source") == "manual":
                del meta["dividend_rate_annual"]
            else:
                meta["dividend_rate_source"] = "enriched"
        store.update_by_id(inst["id"], {**meta, "dividend_meta_updated_at": now})
        updated += 1
    _log.info("dividend-meta enrich: checked %d, updated %d", len(targets), updated)
    return {"checked": len(targets), "updated": updated}


COINGECKO_IDS = {
    "BTC-USD": "bitcoin",
    "ETH-USD": "ethereum",
    "SHIB-USD": "shiba-inu",
    "DOGE-USD": "dogecoin",
    "PEPE-USD": "pepe",
    "TLM-USD": "alien-worlds",
}


def _round_price(p: float) -> float:
    """Round a price keeping ~6 significant figures, never fewer than 6 decimals.

    Fixed 6dp rounding collapses sub-cent crypto (e.g. PEPE ~3e-6) to a single value,
    so its chart flatlines and every tick reads 0.00. Significant-figure rounding keeps
    the intraday variation. Normal prices (>=1) are unaffected (still >=6 decimals)."""
    if not p or not math.isfinite(p):
        return p
    exp = math.floor(math.log10(abs(p)))
    return round(p, max(6, 5 - exp))


def _finite(x) -> float | None:
    """Coerce to a finite float, or None.

    yfinance ``fast_info`` returns NaN for sparse data (a fresh IPO/SPAC like SPCX, an
    illiquid or halted ticker). NaN is truthy, so it sails past ``if price:`` and would be
    stored as a live quote — then Starlette serializes the response with
    ``json.dumps(allow_nan=False)`` and the whole asset-detail endpoint 500s. Treating
    non-finite as missing lets the caller fall back to recorded history instead."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _fetch_one(symbol):
    try:
        fi = yf.Ticker(symbol).fast_info
        price = _finite(fi.get("lastPrice")) or _finite(fi.get("regularMarketPrice"))
        prev = _finite(fi.get("previousClose"))
        if prev is None:
            fallback_prev = _finite(fi.get("regularMarketPreviousClose"))
            # regularMarketPreviousClose can lag right at a session's open, momentarily
            # mirroring the CURRENT price instead of the real prior close, rather than
            # being absent outright (owner-reproduced live, 2026-09-21: KSTL.DE's "Prev
            # close" pill read 36.46 — identical to its own live price — instead of
            # Yahoo's own correct 36.61 the moment XETRA opened; previousClose itself
            # was live and correct seconds later, so this field specifically is what
            # lagged). A "previous close" that exactly equals the current price this
            # soon after an open is a red flag, not a coincidence — safer to report
            # unknown (None) than a confidently wrong number.
            if fallback_prev is not None and (price is None or fallback_prev != price):
                prev = fallback_prev
        if price:
            return (symbol, _round_price(price), _round_price(prev) if prev else None)
    except Exception:
        pass
    try:
        h = yf.Ticker(symbol).history(period="2d")
        if len(h) >= 2:
            last, prior = _finite(h["Close"].iloc[-1]), _finite(h["Close"].iloc[-2])
            if last is not None:
                return (
                    symbol,
                    _round_price(last),
                    _round_price(prior) if prior is not None else None,
                )
        if len(h) == 1:
            last = _finite(h["Close"].iloc[-1])
            if last is not None:
                return symbol, _round_price(last), None
    except Exception:
        pass
    return symbol, None, None


def _crypto_routing_with_currencies(
    tickers: list[str],
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Route crypto with the instrument's declared quote currency attached."""
    by_pid: dict[str, dict] = {}
    by_symbol: dict[str, dict] = {}
    try:
        from backend.db.instrument_store import InstrumentStore

        for i in InstrumentStore().all():
            if i.get("price_source_id"):
                by_pid[i["price_source_id"].lower()] = i
            # Index by market symbol too, so a holding/watchlist keyed by its symbol
            # (e.g. "BTC", which now reads off inst.symbol) still routes to its
            # CoinGecko id ("bitcoin"). price_source_id matches take precedence.
            if i.get("symbol"):
                by_symbol.setdefault(i["symbol"].upper(), i)
    except Exception:
        by_pid, by_symbol = {}, {}

    cg_pairs: list[tuple[str, str, str]] = []
    yf_tickers: list[str] = []
    for t in tickers:
        inst = by_pid.get(t.lower()) or by_symbol.get(t.upper())
        source = (inst.get("price_source") or "").lower() if inst else ""
        quote_currency = str((inst or {}).get("quote_currency") or "USD").upper()
        if source == "coingecko":
            cg_pairs.append((t, inst["price_source_id"], quote_currency))
        elif source == "manual":
            continue
        elif source == "yahoo":
            yf_tickers.append(t)
        elif t in COINGECKO_IDS:
            cg_pairs.append((t, COINGECKO_IDS[t], quote_currency))
        else:
            yf_tickers.append(t)
    return cg_pairs, yf_tickers


def _crypto_routing(tickers: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Compatibility view of crypto routing for callers that only need canonical ids."""
    pairs, yf_tickers = _crypto_routing_with_currencies(tickers)
    return [(request_key, canonical_id) for request_key, canonical_id, _ in pairs], yf_tickers


def _finite_positive(value: object) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _local_day(epoch: object, tz_name: str | None) -> str | None:
    """YYYY-MM-DD of a Yahoo epoch in the exchange's own timezone (UTC when unknown)."""
    if not isinstance(epoch, (int, float)):
        return None
    try:
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:
        tz = timezone.utc
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(tz).date().isoformat()


def last_session_post_print(q: dict, tz_name: str | None) -> dict | None:
    """The LAST COMPLETED session's after-hours print from a v7 quote, whatever the market
    state — Yahoo keeps `postMarketPrice`/`postMarketTime` from Friday's 8pm close through
    the whole weekend (verified 2026-09-20: ORCL post 147.55 @ 19:59 ET Fri, marketState
    CLOSED on Sunday; Perplexity shows exactly that figure as "After Hours" under Friday's
    row). Distinct from `_extended_hours_from_quote`, which is the LIVE extended print that
    feeds prices app-wide and is (rightly) gated on an open extended session; this one is
    display-only history, stamped with the `session_date` it belongs to so a timeline can
    attach it to the right day. None unless the print and the regular close fall on the
    same exchange-local day (otherwise it's some older session's echo)."""
    post = _extended_session_print(q, "post")
    if not post or not post.get("as_of"):
        return None
    post_day = _local_day(q.get("postMarketTime"), tz_name)
    reg_day = _local_day(q.get("regularMarketTime"), tz_name)
    if not post_day or post_day != reg_day:
        return None
    return {**post, "session_date": post_day}


def _as_of(value: object, *, max_age_hours: float = 6.0) -> str | None:
    """`max_age_hours` defaults to a tight 6h bar, kept for crypto's own freshness gate. An
    equity's regularMarketTime is legitimately hours (or, over a weekend/holiday, days) old —
    a frozen close while PRE/POST/CLOSED, and equally a delayed venue's first REGULAR minutes
    before today's first print lands. That is the correct "when did this price print" fact, so
    equity callers pass a looser bound (owner-reproduced live bugs: 2026-08-31, "At close" read
    the CURRENT wall-clock time instead of Friday's close; 2026-09-23, XETRA at 09:05 read
    "As of 9:05" over yesterday's closing price)."""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(timestamp) or timestamp <= 0:
        return None
    try:
        observed_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    now = datetime.now(timezone.utc)
    if observed_at > now + timedelta(minutes=5) or now - observed_at > timedelta(
        hours=max_age_hours
    ):
        return None
    return observed_at.isoformat()


def _fetch_coingecko_by_id(
    pairs: list[tuple[str, str]], currency: str | dict[str, str] = "USD"
) -> tuple[dict, dict, dict]:
    if not pairs:
        return {}, {}, {}
    ids = sorted({cid for _, cid in pairs})
    if isinstance(currency, dict):
        currencies_by_request = {
            request_key: str(currency.get(request_key) or "USD").lower() for request_key, _ in pairs
        }
    else:
        currencies_by_request = {
            request_key: str(currency or "USD").lower() for request_key, _ in pairs
        }
    vs_currencies = ",".join(sorted(set(currencies_by_request.values())))
    try:
        data = coingecko_client.get_json(
            "/simple/price",
            {
                "ids": ",".join(ids),
                "vs_currencies": vs_currencies,
                "include_24hr_change": "true",
                "include_last_updated_at": "true",
            },
            timeout=10,
        )
    except Exception:
        return {}, {}, {}
    if not isinstance(data, dict):
        return {}, {}, {}
    prices, prev_prices, as_of = {}, {}, {}
    for req_key, cg_id in pairs:
        vs_currency = currencies_by_request[req_key]
        d = data.get(cg_id)
        if not isinstance(d, dict) or vs_currency not in d:
            continue
        current = _finite_positive(d.get(vs_currency))
        if current is None:
            continue
        observed_at = _as_of(d.get("last_updated_at"))
        if observed_at is None:
            continue
        current = _round_price(current)
        previous = None
        prices[req_key] = current
        change_key = f"{vs_currency}_24h_change"
        if d.get(change_key) is not None:
            pct = _finite(d[change_key])
            if pct is None or pct == -100:
                prices.pop(req_key, None)
                continue
            previous = _finite_positive(current / (1 + pct / 100))
            if previous is None:
                prices.pop(req_key, None)
                continue
            prev_prices[req_key] = _round_price(previous)
        as_of[req_key] = observed_at
    return prices, prev_prices, as_of


# ── Batched Yahoo quote (v7) + market status ───────────────────────────────────
# Yahoo's v7 quote endpoint returns price + previous close + marketState + exchange
# timezone for MANY symbols in ONE request (vs one fast_info call per ticker). It is
# gated behind a crumb+cookie, so we reuse yfinance's managed session to obtain it.
# ANY failure (auth wall, yfinance API drift, parse error) returns {} and the caller
# falls back to the per-ticker path — so live prices can never regress.
_MARKET_CACHE_TTL = (
    600.0  # 10 min — open/closed transitions are slow; doubles as the off-hours probe cadence
)
_market_cache: dict[str, tuple[float, dict]] = {}  # symbol → (fetched_at_monotonic, status)

# ── Latest-resolved-price cache (extended-aware "last known", separate from price_history) ──
# `PriceHistoryStore` is a DAILY series — one point per (price_source_id, trading day), the
# day-P&L anchor `prior_to_map()` depends on meaning "that day's REGULAR close". Writing an
# extended (pre/post-market) print into that table would corrupt it: the LAST write for a day
# wins, so a POSTPOST-hours fetch would overwrite today's row with a thin-volume extended print,
# and tomorrow's `prior_to_map()` would then anchor day-change to THAT instead of the real close
# (considered and rejected — see docs/adr/2026-09-01-extended-price-consistency.md).
#
# This is a SEPARATE, non-day-keyed cache for the opposite need: `positions.hydration_context()`'s
# `last_known` (the fallback every ``hydrated()`` caller that doesn't fetch its own live price
# falls back to — net worth, income, watchlist, every AI tool that reads holdings, ~25 call
# sites) was resolving to the regular-session close ALWAYS, even minutes after a live extended
# print had been fetched elsewhere in the very same request cycle (owner-reproduced 2026-09-01:
# PLTR's position card valued at the regular close while the headline price already showed a
# live pre-market print — see the sibling fix in asset_detail.py). `fetch_live()` writes the
# EXTENDED-preferred resolved price here on every call (regardless of `record_history`, since
# this cache doesn't feed the day-keyed series `record_history` gates), and
# `hydration_context()` prefers it over the stored last-known whenever a fresh entry exists.
# In-memory only (resets on restart) — fine, since the scheduled price-check sweep
# (``monitoring.price_monitor.run_price_check``, ~60s while markets are open) keeps it warm for
# essentially every held ticker; a cold cache just means "no fresher-than-stored data yet",
# never a wrong one.
_LIVE_PRICE_CACHE_TTL = 300.0  # 5 min — comfortably longer than the ~60s sweep cadence
_live_price_cache: dict[
    str, tuple[float, float]
] = {}  # price_source_id → (resolved_price, fetched_at_monotonic)


def latest_live_prices() -> dict[str, float]:
    """{price_source_id: most-recently-resolved live price} for every entry fetched within
    the last `_LIVE_PRICE_CACHE_TTL` — extended-preferred (see the cache's own module note
    above). Read by `positions.hydration_context()` to keep `last_known` fresh without
    requiring every one of its ~25 callers to fetch and thread live prices individually."""
    now = time.monotonic()
    return {
        pid: price
        for pid, (price, fetched_at) in _live_price_cache.items()
        if now - fetched_at < _LIVE_PRICE_CACHE_TTL
    }


# Holiday-accurate next-open: Yahoo's chart meta carries the ACTUAL trading sessions
# (currentTradingPeriod, holidays + DST excluded). Used ONLY to refine the "reopens in …" countdown
# for a CLOSED symbol — NOT for the open/closed verdict itself (that comes from the batched v7 quote,
# one request for all symbols; per-symbol chart fetches must never sit on the gate's hot path). The
# raw payload is cached ~20 min; a closed symbol costs at most a few chart calls/day.
# symbol → (fetched_at_monotonic, chart_json|None).
_CHART_CACHE_TTL = 1200.0  # 20 min
# A FAILED fetch (payload None — a transient Yahoo timeout/hiccup, not "no data exists") gets a
# much shorter negative-cache TTL than a real success (owner-reproduced live bug, 2026-09-20:
# "why don't I see the dots anymore" — one bad Yahoo response cached `None` for the FULL 20 min,
# silently dropping session_bounds and blanking the 1D chart's pre/post-market dotted bands
# for that whole window with no retry). A short retry window lets the next call self-heal
# quickly instead of parking a real outage on a 20-minute clock.
_CHART_CACHE_FAILURE_TTL = 60.0  # 1 min
_chart_cache: dict[str, tuple[float, dict | None]] = {}

# Crypto trades 24/7 — never gated, never "closed".
_CRYPTO_MARKET = {
    "state": "REGULAR",
    "is_open": True,
    "is_regular": True,
    "exchange": "Crypto",
    "timezone": "UTC",
    "tz_short": "UTC",
    "gmt_offset": 0,
    "reopens_at": None,
    "always_open": True,
    "delayed_minutes": None,
}

# Best-effort regular-session open (local wall-clock HH, MM) by exchange timezone, used only
# to estimate "reopens in" when a market is CLOSED. Accurate except on exchange holidays
# (which would need Yahoo's per-symbol tradingPeriods); the chip labels it as approximate.
_EXCHANGE_OPEN_LOCAL: dict[str, tuple[int, int]] = {
    "America/New_York": (9, 30),
    "America/Chicago": (8, 30),
    "Europe/London": (8, 0),
    "Europe/Berlin": (9, 0),
    "Europe/Paris": (9, 0),
    "Europe/Amsterdam": (9, 0),
    "Asia/Singapore": (9, 0),
    "Asia/Hong_Kong": (9, 30),
    "Asia/Tokyo": (9, 0),
    "Asia/Shanghai": (9, 30),
    "Australia/Sydney": (10, 0),
}

# Session state → how we treat it. REGULAR + the REAL extended sessions (PRE/POST, where prices
# actually move) count as OPEN — worth polling, "Open"/"Pre-market"/"After hours" on the chip.
# Everything else is CLOSED — the gate idles and the chip reads "Market closed · reopens …".
# Yahoo's marketState keeps saying PRE/POST through dead windows where nothing trades (XETRA's
# phantom post window; the sticky overnight POST), so a PRE/POST is honoured ONLY when the quote
# actually updated within this window — otherwise the price is frozen at the close and it's CLOSED.
_OPEN_STATES = {"REGULAR", "PRE", "POST"}
_CLOSED_STATES = {"PREPRE", "POSTPOST", "CLOSED"}
_EXTENDED_STALE_SECS = 1800.0  # 30 min


def _response_json(response) -> dict:
    """Decode an eager HTTP response and release its socket on every exit path."""
    try:
        return response.json()
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()


def _yf_batch_quote(symbols: list[str]) -> dict[str, dict]:
    """Batched Yahoo v7 quote. Returns {symbol: raw_quote} for what resolved, or {} on ANY
    failure (so the caller falls back per-ticker). Reuses yfinance's managed crumb/cookie."""
    if not symbols:
        return {}
    try:
        from yfinance import data as _ydata

        yfd = _ydata.YfData()
        crumb = yfd._get_crumb_basic()
        if not crumb:
            return {}
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), 50):  # Yahoo caps symbols/request; chunk to be safe
            chunk = symbols[i : i + 50]
            url = (
                "https://query1.finance.yahoo.com/v7/finance/quote?symbols="
                + urllib.parse.quote(",".join(chunk))
                + "&crumb="
                + urllib.parse.quote(crumb)
            )
            data = _response_json(yfd.get(url))
            for q in data.get("quoteResponse", {}).get("result", []):
                sym = q.get("symbol")
                if sym:
                    out[sym] = q
        return out
    except Exception as exc:
        # Log the error CLASS only, never the exception text: on a network error it embeds the
        # request URL, which carries the session crumb, and the operator log is readable via the
        # get_recent_logs tool. The crumb must not reach logs.
        _log.info(
            "prices: v7 batch quote unavailable (%s) — falling back per-ticker",
            type(exc).__name__,
        )
        return {}


def _quote_to_price(q: dict) -> tuple[float | None, float | None]:
    """(price, prev_close) from a v7 quote — regular-session values, matching fast_info so the
    headline and position rows never disagree (extended-hours prices are NOT substituted).
    previousClose is preferred over regularMarketPreviousClose — the latter can lag right at a
    session's open, momentarily mirroring the CURRENT price instead of the real prior close
    (owner-reproduced live, 2026-09-21: KSTL.DE's "Prev close" pill read identical to its own
    live price — 36.46 both — the moment XETRA opened, instead of Yahoo's own correct 36.61;
    _fetch_one carries the matching fix for fast_info's equivalent fields). Only falls back to
    it when previousClose is genuinely absent, and rejects the fallback outright when it
    exactly mirrors the current price — a red flag, not a coincidence, this soon after an
    open."""
    price = _finite_positive(q.get("regularMarketPrice"))
    previous_raw = q.get("previousClose")
    if previous_raw is None:
        fallback_raw = q.get("regularMarketPreviousClose")
        fallback_prev = _finite_positive(fallback_raw) if fallback_raw is not None else None
        if fallback_prev is not None and (price is None or fallback_prev != price):
            previous_raw = fallback_raw
    prev = _finite_positive(previous_raw) if previous_raw is not None else None
    if price is None:
        return None, None
    if previous_raw is not None and prev is None:
        return None, None
    return _round_price(price), (_round_price(prev) if prev is not None else None)


def _extended_hours_from_quote(q: dict, gated_state: str | None) -> dict | None:
    """Pre/post-market price + change from a v7 quote — display-only (see `_quote_to_price`'s
    own note: extended-hours prices are NEVER substituted into the regular-session `price`/
    `prev`, so net worth / P&L / position value stay anchored to the last regular close, same
    as before this existed). `gated_state` is `_quote_to_market`'s own freshness-gated verdict
    (not the raw `marketState` string).

    PRE/PREPRE and POST/POSTPOST are both accepted — PREPRE/POSTPOST is Yahoo's OWN distinction
    for "the standard extended window has ended, deep pre-dawn/overnight now" (e.g. MSFT at
    10:55pm ET reports "POSTPOST", not "POST"), not "nothing real ever happened here". Yahoo's
    frozen postMarketPrice from the standard 8pm close is still real, honestly-timestamped
    information — Perplexity's own UI shows exactly this, unchanged, well into the evening
    (owner-reproduced 2026-09-01: our own after-hours figure vanished the moment marketState
    ticked from POST to POSTPOST, even though nothing about the underlying data changed).

    The real thing worth guarding against is a PHANTOM extended print — a venue (XETRA is the
    known case) whose "post" marketState persists for hours with NO real trade ever occurring,
    where the price is frozen at the regular close ITSELF, not a genuinely different extended
    trade. Checked directly (price must differ from `regularMarketPrice` by more than a
    rounding sliver) rather than by time elapsed — a real print, however many hours old, is
    still real; a copy of the regular close never was, no matter how fresh the timestamp.

    PREPRE covers TWO distinct moments Yahoo doesn't otherwise distinguish: before today's own
    pre-market has opened (nothing to show yet — `preMarketPrice` is genuinely absent), and deep
    overnight AFTER yesterday's post-market fully closed, once Yahoo's own metadata has already
    rolled currentTradingPeriod to tomorrow (see `market_status`'s session_bounds handling) while
    `postMarketPrice`/`postMarketTime` are still sitting there, real and unexpired, from the 8pm
    close (owner-reproduced 2026-09-01: verified directly against a live v7 quote — MSFT at
    12:09am ET, state PREPRE, `preMarketPrice: None` but `postMarketPrice: 507.73` still
    populated). So PREPRE tries pre first (the imminent-session case), and falls back to the
    still-real post print when there's nothing pre yet — never the reverse for POSTPOST, which
    is unambiguous."""
    if gated_state in ("PRE", "PREPRE"):
        ext = _extended_session_print(q, "pre")
        if ext is None and gated_state == "PREPRE":
            ext = _extended_session_print(q, "post")
        return ext
    if gated_state in ("POST", "POSTPOST"):
        return _extended_session_print(q, "post")
    return None


def _extended_session_print(q: dict, session: str) -> dict | None:
    """One session's (`"pre"` or `"post"`) price/change/timestamp fields from a v7 quote, or
    None if absent or a phantom copy of the regular close. Shared by both branches of
    `_extended_hours_from_quote` so PREPRE's pre→post fallback reuses the identical extraction
    and phantom-print guard rather than duplicating it per branch."""
    prefix = "preMarket" if session == "pre" else "postMarket"
    price = _finite_positive(q.get(f"{prefix}Price"))
    if price is None:
        return None
    regular = _finite_positive(q.get("regularMarketPrice"))
    if regular is not None and abs(price - regular) < 0.005:
        return None  # a phantom/frozen copy of the regular close, not a real extended trade
    change = q.get(f"{prefix}Change")
    change_pct = q.get(f"{prefix}ChangePercent")
    ts = q.get(f"{prefix}Time")
    return {
        "session": session,
        "price": _round_price(price),
        "change_abs": _round_price(change) if isinstance(change, (int, float)) else None,
        "change_pct": round(change_pct, 4) if isinstance(change_pct, (int, float)) else None,
        "as_of": (
            datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            if isinstance(ts, (int, float))
            else None
        ),
    }


def resolve_price_quote(
    pid: str,
    *,
    prices: dict | None = None,
    extended: dict | None = None,
    prev_prices: dict | None = None,
) -> PriceQuote | None:
    """The single read chokepoint for "what is this ticker's live price right now" —
    covers the live-quote tier only (fixed-income marks, manual overrides, and last-known
    fallbacks are POSITION concerns and stay in positions._resolve_price's own ladder,
    which calls this for its live tier). Every caller that used to pick between `prices`
    and `extended` by hand — positions.hydrated(), asset_detail._price_block(),
    market_ticker._build() — now calls this instead, so all three agree by construction.

    Prefers an extended-hours (pre/post) print over the regular-session price when one is
    live for `pid`. Safe without an extra time check: `extended` is only ever populated for
    a pid genuinely inside a live PRE/POST window (see _extended_hours_from_quote's own
    gating), so during regular hours this behaves exactly as the plain regular-price lookup
    always did.

    An EXTENDED quote's `change_abs`/`change_pct` compare against TODAY's regular close, not
    yesterday's prior close — verified directly against Yahoo's own UI (owner-reproduced
    2026-09-01: our after-hours change read "-6.09 (-1.19%)", i.e. price vs YESTERDAY's
    prior close, while Yahoo's own after-hours line for the same instant read "+0.26
    (+0.05%)", i.e. price vs TODAY's regular close — a materially different, and wrong,
    number, despite an earlier version of this docstring claiming to already match Yahoo's
    convention). `extended`'s own change_abs/change_pct (Yahoo's raw preMarketChange(Percent)
    / postMarketChange(Percent) — already computed against the regular close upstream, see
    _extended_hours_from_quote) are used directly when present; computed against
    `regular_price` only as a fallback when Yahoo didn't supply them. A REGULAR quote's
    change still anchors to `prev` (yesterday's close), which is the correct, unrelated
    convention for "today's regular-session change". Returns None when `pid` is falsy or
    neither map has anything for it."""
    if not pid:
        return None
    ext = (extended or {}).get(pid)
    regular = (prices or {}).get(pid)
    prev_raw = (prev_prices or {}).get(pid)
    prev = float(prev_raw) if isinstance(prev_raw, (int, float)) and prev_raw else None
    regular_f = float(regular) if regular is not None else None

    if ext and ext.get("price") is not None:
        price, source, is_extended, session, as_of = (
            float(ext["price"]),
            "extended",
            True,
            ext.get("session"),
            ext.get("as_of"),
        )
        ext_change_abs, ext_change_pct = ext.get("change_abs"), ext.get("change_pct")
        if ext_change_abs is not None or ext_change_pct is not None:
            change_abs = _round_price(float(ext_change_abs)) if ext_change_abs is not None else None
            change_pct = round(float(ext_change_pct), 4) if ext_change_pct is not None else None
        elif regular_f:
            change_abs = _round_price(price - regular_f)
            change_pct = round((price / regular_f - 1) * 100, 4)
        else:
            change_abs = change_pct = None
    elif regular is not None:
        price, source, is_extended, session, as_of = float(regular), "live", False, None, None
        change_abs = _round_price(price - prev) if prev else None
        change_pct = round((price / prev - 1) * 100, 4) if prev else None
    else:
        return None

    return PriceQuote(
        price=_round_price(price),
        source=source,
        is_extended=is_extended,
        extended_session=session,
        regular_price=_round_price(regular_f) if regular_f is not None else None,
        prev_close=prev,
        change_abs=change_abs,
        change_pct=change_pct,
        as_of=as_of,
    )


def _parse_chart_next_open(chart_json: dict, now_epoch: float) -> str | None:
    """Next FUTURE trading-session start (UTC ISO) from a chart payload's currentTradingPeriod —
    holiday- and DST-accurate. Prefers the REGULAR session open: that is "the market opens" on every
    exchange and matches the weekend fallback (_reopens_at). XETRA-style exchanges report a ~2h
    pre-trading auction window before the 09:00 open, and counting that pre start made the chip read
    "reopens in 18m" while the market actually opened ~2h later. Falls back to the earliest future of
    pre/post only when no future regular start is present. Returns None when every start is in the
    past (Yahoo hasn't rolled forward yet) → caller keeps the weekend heuristic. Pure (no I/O)."""
    try:
        meta = (chart_json.get("chart", {}).get("result") or [{}])[0].get("meta") or {}
    except Exception:
        return None
    ctp = meta.get("currentTradingPeriod") or {}

    def _future_start(seg: str) -> float | None:
        start = (ctp.get(seg) or {}).get("start")
        return float(start) if isinstance(start, (int, float)) and start > now_epoch else None

    chosen = _future_start("regular")
    if chosen is None:
        others = [s for s in (_future_start("pre"), _future_start("post")) if s is not None]
        if not others:
            return None
        chosen = min(others)
    return datetime.fromtimestamp(chosen, tz=timezone.utc).isoformat()


def _own_session_windows(trading_periods: object) -> dict | None:
    """{seg: {start, end}} for the most recent day in Yahoo's nested `meta.tradingPeriods`
    ({seg: [[{start, end}, …] per day]}); None for any other shape (absent, or the bare
    list Yahoo returns without includePrePost)."""
    if not isinstance(trading_periods, dict):
        return None
    out: dict[str, dict] = {}
    for seg, days in trading_periods.items():
        if not isinstance(days, list) or not days or not isinstance(days[-1], list):
            continue
        segments = days[-1]
        if segments and isinstance(segments[0], dict) and isinstance(segments[-1], dict):
            out[seg] = {"start": segments[0].get("start"), "end": segments[-1].get("end")}
    return out or None


def _parse_trading_periods(chart_json: dict) -> dict | None:
    """Real pre/regular/post session start+end (UTC ISO) of the session the chart payload's
    OWN bars belong to — `meta.tradingPeriods` (present on the intraday interval `_fetch_chart`
    asks for), falling back to `currentTradingPeriod` (the field `_parse_chart_next_open`
    reads) only for a symbol with no intraday data at all (a mutual fund). The two disagree
    past LOCAL MIDNIGHT: Yahoo rolls currentTradingPeriod to the next, not-yet-started session
    hours before it opens, while the 1D bars — and tradingPeriods — are still yesterday's
    (verified live 2026-09-15 against Tokyo/HK/ASX at ~04:55 local; owner-reproduced symptom:
    ORCL/RKLB at 2am ET drew yesterday's full session with no pre/post dots, because the
    bounds described tomorrow). Exact and holiday/DST-accurate straight from Yahoo (early-close
    days, unusual sessions, and all) — never a guessed 4:00 AM/9:30 AM/8:00 PM local convention
    baked into our own code (owner ask 2026-08-31). A zero-length window (start == end — what
    Tokyo, futures and FX report for pre/post) is no session at all and is dropped, so the
    chart never gets a `pre`/`post` key to draw a band for. None when nothing usable remains."""
    try:
        meta = (chart_json.get("chart", {}).get("result") or [{}])[0].get("meta") or {}
    except Exception:
        return None
    windows = _own_session_windows(meta.get("tradingPeriods")) or meta.get("currentTradingPeriod")
    out: dict[str, dict[str, str]] = {}
    for seg in ("pre", "regular", "post"):
        win = (windows or {}).get(seg) or {}
        start, end = win.get("start"), win.get("end")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            out[seg] = {
                "start": datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(end, tz=timezone.utc).isoformat(),
            }
    return out or None


def _next_session_has_begun(chart_json: dict | None, now_epoch: float) -> bool:
    """True once a session NEWER than the one the payload's own bars belong to has started:
    `currentTradingPeriod`'s first tick (pre.start, else regular.start — Yahoo rolls that field
    to the next session at local midnight, hours early) is later than the bars' own
    `tradingPeriods` regular start AND already in the past. From that instant the payload's
    bars and bounds describe yesterday while the 1-minute tape is today's. False whenever
    either field is missing (a mutual fund's currentTradingPeriod-only payload)."""
    try:
        meta = (chart_json or {}).get("chart", {}).get("result")[0].get("meta") or {}
    except Exception:
        return False
    own = (_own_session_windows(meta.get("tradingPeriods")) or {}).get("regular") or {}
    own_start = own.get("start")
    ctp = meta.get("currentTradingPeriod") or {}
    starts = [
        w.get("start")
        for w in (ctp.get("pre"), ctp.get("regular"))
        if isinstance(w, dict) and isinstance(w.get("start"), (int, float))
    ]
    if not isinstance(own_start, (int, float)) or not starts:
        return False
    first = min(starts)
    return first > own_start and now_epoch >= first


def _fetch_chart(symbol: str, now_epoch: float | None = None) -> dict | None:
    """Raw Yahoo v8 chart payload (no crumb needed), cached ~20 min on success. None on any
    failure → caller keeps the weekend heuristic, and that None is cached for only
    `_CHART_CACHE_FAILURE_TTL` (a real outage still degrades gracefully every call; a lone
    transient hiccup retries within a minute instead of sitting on the full success TTL — see
    that constant's own comment). The SAME short clock applies to a success whose session has
    since ROLLED (`_next_session_has_begun`): a payload cached at 03:5x ET still described
    Friday's session at 04:02 Monday while the 1-minute tape was already Monday's, so the 1D
    chart drew Friday's window stretched to Monday's first print — 66 hours of whitespace,
    "pre-market on the right side instead of left" (owner-reproduced live, 2026-09-21, MSFT);
    the short clock lets the bounds catch up within a minute of the open, and bounds Yahoo's
    own roll lag to a refetch per minute rather than per call. Short timeout so a hung Yahoo
    response can't pin the worker. `interval=1h` (not 1d) because only an intraday interval
    carries `meta.tradingPeriods` — the session the returned bars belong to, which
    `_parse_trading_periods` needs; the 1h payload is still ~2 KB, and `currentTradingPeriod`
    (the reopens countdown's field) is present either way."""
    now = time.monotonic()
    cached = _chart_cache.get(symbol)
    if cached:
        age = now - cached[0]
        wall = now_epoch if now_epoch is not None else time.time()
        fresh_success = cached[1] is not None and not _next_session_has_begun(cached[1], wall)
        ttl = _CHART_CACHE_TTL if fresh_success else _CHART_CACHE_FAILURE_TTL
        if age < ttl:
            return cached[1]
    payload: dict | None = None
    try:
        from yfinance import data as _ydata

        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            + urllib.parse.quote(symbol)
            + "?interval=1h&range=1d&includePrePost=true"
        )
        payload = _response_json(_ydata.YfData().get(url, timeout=10))
    except Exception as exc:
        _log.info("prices: chart unavailable (%s)", type(exc).__name__)
    _chart_cache[symbol] = (now, payload)
    return payload


def _chart_next_open(symbol: str) -> str | None:
    """Holiday-accurate next session open (UTC ISO) from Yahoo's chart, cached ~20 min. None on any
    failure → caller keeps the weekend heuristic. Refines the "reopens in …" chip label only."""
    payload = _fetch_chart(symbol)
    if not payload:
        return None
    return _parse_chart_next_open(payload, datetime.now(timezone.utc).timestamp())


def _reopens_at(state: str, timezone_name: str, gmt_offset_ms) -> str | None:
    """Weekend-only FALLBACK for the next regular open when closed (used when the chart can't
    supply a future session). Uses Yahoo's current GMT offset (so no tz database needed) + a
    per-exchange open time; skips weekends but NOT holidays. None when open / no offset."""
    if state not in _CLOSED_STATES or gmt_offset_ms is None:
        return None
    oh, om = _EXCHANGE_OPEN_LOCAL.get(timezone_name, (9, 30))
    offset = timedelta(milliseconds=gmt_offset_ms)
    local_now = datetime.now(timezone.utc) + offset  # value = exchange wall-clock (tag ignored)
    cand = local_now.replace(hour=oh, minute=om, second=0, microsecond=0)
    if cand <= local_now:
        cand += timedelta(days=1)
    while cand.weekday() >= 5:  # Sat=5, Sun=6
        cand += timedelta(days=1)
    return (cand - offset).isoformat()  # back to the true UTC instant


def _latest_quote_epoch(q: dict):
    """Most recent trade timestamp across regular/pre/post — the freshest evidence the symbol is
    actually trading. None when no usable timestamp is present."""
    times = [q.get(k) for k in ("postMarketTime", "preMarketTime", "regularMarketTime")]
    times = [t for t in times if isinstance(t, (int, float))]
    return max(times) if times else None


def _is_real_time_source(source) -> bool:
    return isinstance(source, str) and "real time" in source.lower()


def _quote_to_market(q: dict, now_epoch: float | None = None) -> dict:
    """Market status from a batched v7 quote — the single source for the open/closed verdict.

    Yahoo's marketState string keeps reading PRE/POST through windows where nothing actually trades:
    XETRA's phantom post window (no real extended session) and the sticky overnight POST (it never
    rolls to CLOSED). In both the price is frozen at the regular close. So a PRE/POST is honoured ONLY
    when the latest quote updated within _EXTENDED_STALE_SECS; otherwise the honest state is CLOSED.
    REGULAR is never downgraded (a thin regular session can legitimately go quiet)."""
    state = (q.get("marketState") or "").upper() or "UNKNOWN"
    if state in ("PRE", "POST"):
        now = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
        latest = _latest_quote_epoch(q)
        if latest is None or now - latest > _EXTENDED_STALE_SECS:
            state = "CLOSED"
    tz = q.get("exchangeTimezoneName") or ""
    gmt = q.get("gmtOffSetMilliseconds")
    qt = q.get("regularMarketTime")
    delayed_by = q.get("exchangeDataDelayedBy")
    return {
        "state": state,
        "is_open": state in _OPEN_STATES,
        "is_regular": state == "REGULAR",
        "exchange": q.get("fullExchangeName") or q.get("exchange") or "",
        "timezone": tz,
        "tz_short": q.get("exchangeTimezoneShortName") or "",
        "gmt_offset": gmt,
        "reopens_at": _reopens_at(state, tz, gmt),
        # Yahoo's own licensed-delay flag (0 for real-time venues like Nasdaq, 15+ for most
        # non-US exchanges under the free-tier feed — e.g. XETRA). Lets the chip read "Delayed
        # 15m" instead of a bare timestamp, so a routine licensing lag never reads as an outage
        # the way the stale/unavailable fallback banner does (owner report 2026-08-31: a XETRA
        # position briefly showed "Live providers are temporarily unavailable" at the open,
        # which was actually just this normal delay before the day's first tick landed).
        # ...but only when the price IS the delayed feed. For US listings Yahoo reports the
        # exchange's own licensing delay (NYSE 20, Nasdaq 15) while serving a real-time
        # consolidated price, and names it in quoteSourceName ("Nasdaq Real Time Price" vs
        # "Delayed Quote"). Found 2026-10-12: ORCL read "Delayed 20m" on a quote 16s old.
        "delayed_minutes": int(delayed_by)
        if isinstance(delayed_by, (int, float))
        and delayed_by > 0
        and not _is_real_time_source(q.get("quoteSourceName"))
        else None,
        # The displayed headline price is the regular-session price, so its real "as of" is
        # regularMarketTime — NOT wall-clock now. Lets the header read "As of 9:02 AM" when a
        # vendor feed is delayed instead of implying the stale price is current. None → caller
        # falls back to now (crypto / quotes without a timestamp are effectively real-time).
        "quote_time": (
            datetime.fromtimestamp(qt, tz=timezone.utc).isoformat()
            if isinstance(qt, (int, float))
            else None
        ),
    }


def _cache_market_from_quotes(batch: dict[str, dict]) -> None:
    now = time.monotonic()
    for sym, q in batch.items():
        if q.get("marketState"):
            _market_cache[sym] = (now, _quote_to_market(q))


def _trading_day_map(symbols: list[str]) -> dict[str, str]:
    """{symbol: today's date in ITS OWN exchange timezone} for every symbol with a cached
    timezone — populated by `_cache_market_from_quotes` from the SAME batched v7 quote a
    live fetch already made, so this costs no extra network call. A symbol with no cached
    timezone (crypto, an FX pair, a quote miss) is simply omitted; its caller then falls
    back to the price store's own UTC-anchored default day, which is correct for a 24/7
    market and for every exchange whose session doesn't straddle UTC midnight (the vast
    majority — this narrows that residual gap to exchanges like ASX during daylight saving,
    instead of every exchange doing business outside the server's own local hours)."""
    now = datetime.now(timezone.utc)
    out: dict[str, str] = {}
    for sym in symbols:
        cached = _market_cache.get(sym)
        tz_name = cached[1].get("timezone") if cached else None
        if not tz_name:
            continue
        try:
            out[sym] = now.astimezone(ZoneInfo(tz_name)).date().isoformat()
        except Exception:
            continue
    return out


# Minimum distinct 1-minute bars required inside a reported pre/post window before treating it
# as a REAL continuously-traded extended session, not a bare opening-auction print — see
# `_has_real_extended_bars`'s own docstring for why a delayed-quote flag alone isn't a valid
# proxy for this (owner correction, 2026-08-31: "even if it's not delayed, it doesn't mean that
# the session is extended"). 5 bars is a low bar deliberately — the goal is only to reject a
# window with just 1-3 bars clustered at its own open, not to require dense coverage.
_MIN_EXTENDED_SESSION_BARS = 5
# Fewer bars than that still prove a real session when they are SPREAD across the window —
# thin trading in a $9 stock (04:00 and 04:27) is not an opening-auction print. Below the bar
# count, a completed window is real when its bars span at least this long.
_MIN_EXTENDED_SESSION_SPAN_SECONDS = 15 * 60


def _has_real_extended_bars(
    symbol: str, window_start_iso: str, window_end_iso: str, *, now_epoch: float | None = None
) -> bool | None:
    """Ground truth for "does this venue's reported pre/post window carry genuine continuous
    trading" — counts REAL 1-minute bars (the SAME data the 1D chart itself fetches, reusing
    its cache rather than a fresh call) falling inside [start, end). Never inferred from an
    unrelated proxy: a real-time (non-delayed) venue does not automatically have a real
    extended session either, and a delayed venue occasionally does — this checks the actual
    fetched bars, not a flag correlated with them in the cases tested so far.

    Returns None — "not yet checkable" — when the window hasn't started yet as of `now_epoch`
    (a POST window during PRE/REGULAR genuinely has zero bars today because it hasn't happened
    yet, not because the venue lacks one — owner-reproduced live bug, 2026-08-31: RKLB's real
    post-market band vanished at 10am ET because there was nothing to count yet), and ALSO
    while the window is still IN PROGRESS with too few bars so far: only a completed window can
    prove absence (owner-reproduced live, 2026-09-22: ALOY's one pre-market bar at 04:27 ET read
    as "no extended session", the bounds shrank to the regular window, the 04:27 print fell
    before it, and the 1D chart was empty with no bands while Yahoo drew both). An in-progress
    window with enough bars already is True on its elapsed portion. A completed window with
    fewer than `_MIN_EXTENDED_SESSION_BARS` is still real when its bars are spread over at
    least `_MIN_EXTENDED_SESSION_SPAN_SECONDS` — thin trading, not an auction print."""
    try:
        now = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
        start = datetime.fromisoformat(window_start_iso).timestamp()
        window_end = datetime.fromisoformat(window_end_iso).timestamp()
        end = min(window_end, now)
        if end <= start:
            return None
        points = fetch_history_result(symbol, "1D", "yahoo", "usd").get("points") or []
        stamps = sorted(
            t
            for t in (datetime.fromisoformat(p["date"]).timestamp() for p in points)
            if start <= t < end
        )
        if len(stamps) >= _MIN_EXTENDED_SESSION_BARS:
            return True
        if window_end > now:
            return None  # still in progress: too few bars so far is not proof of absence
        return bool(stamps) and stamps[-1] - stamps[0] >= _MIN_EXTENDED_SESSION_SPAN_SECONDS
    except Exception:
        return False


def market_status(
    symbols: list[str],
    with_reopens: bool = True,
    *,
    with_session_bounds: bool = False,
    now_epoch: float | None = None,
) -> dict[str, dict]:
    """{symbol: {state, is_open, is_regular, exchange, timezone, tz_short, gmt_offset, reopens_at}}.

    Crypto (CoinGecko-routed) is always-open 24/7. Equities are classified from ONE batched Yahoo v7
    quote (marketState + freshness; see ``_quote_to_market``), cached ~10 min — never a per-symbol
    network call on the hot path. Symbols that can't be resolved are OMITTED, so callers fail OPEN
    (fetch anyway) rather than silently skip an alert.

    ``with_reopens`` (default True) refines a CLOSED symbol's "reopens in …" via Yahoo's
    holiday-accurate chart sessions (one cached chart call per closed symbol). The price-alert gate
    passes ``with_reopens=False`` — it only reads ``is_open``, so it must stay at one batched call.

    ``with_session_bounds`` (default False, opt-in): attaches the real pre/regular/post
    start+end times of the session the symbol's 1D bars belong to (one cached chart call per
    non-always-open symbol, open OR closed — see `_parse_trading_periods`) for a caller that
    needs the actual session window (the asset-detail page's 1D chart range + countdown, its
    dotted pre/post bands). Not gated on live market state — a closed symbol still gets its
    last completed session's bounds as long as they're real (see the "hasn't ended yet" guard
    below). Left off for every batch caller (ticker strip, price-alert gate) so they stay at
    one call.
    ``now_epoch`` (test-only): pins the "now" `_has_real_extended_bars` judges a window against;
    real callers never pass it (defaults to the real wall clock)."""
    if not symbols:
        return {}
    out: dict[str, dict] = {}
    crypto_pairs, _ = _crypto_routing(symbols)
    crypto_set = {c for c, _ in crypto_pairs}
    now = time.monotonic()
    need: list[str] = []
    for s in symbols:
        if s in crypto_set:
            out[s] = dict(_CRYPTO_MARKET)
        else:
            cached = _market_cache.get(s)
            if cached and now - cached[0] < _MARKET_CACHE_TTL:
                out[s] = cached[1]
            else:
                need.append(s)
    if need:
        _cache_market_from_quotes(_yf_batch_quote(need))
        for s in need:
            cached = _market_cache.get(s)
            if cached:
                out[s] = cached[1]
    if with_reopens:
        # Prefer Yahoo's holiday-accurate next session over the weekend heuristic for closed equities
        # (cached ~20 min). Copy before overriding so the shared cached dict isn't mutated.
        for s, m in list(out.items()):
            if m.get("is_open") or m.get("always_open"):
                continue
            accurate = _chart_next_open(s)
            if accurate:
                out[s] = {**m, "reopens_at": accurate}
    if with_session_bounds:
        for s, m in list(out.items()):
            if m.get("always_open"):
                continue
            # No live-state gate on purpose — the bands should show whenever the 1D bars the
            # chart is drawing genuinely HAVE pre/post data (owner request 2026-09-09), whatever
            # marketState reads. Every closed state attempts the (cached) chart fetch; the
            # bounds come from the session the payload's own bars belong to, and the "hasn't
            # ended yet" guard below is what protects against a future window.
            payload = _fetch_chart(s)
            bounds = _parse_trading_periods(payload) if payload else None
            if not bounds:
                continue
            # Safety net: a window that describes a session that hasn't happened yet. Rare now
            # that bounds are read from the bars' own tradingPeriods, but still the case for
            # the currentTradingPeriod fallback (no intraday data), which Yahoo rolls to the
            # NEXT session at local midnight — hours before it opens (owner-reproduced,
            # XETRA/KSTL.DE: PREPRE at 05:30 local reported tomorrow's 07:00-15:30 window, a
            # full session ahead of the real bars; the same rollover hid RKLB's dots at 2am
            # ET). A regular window that hasn't ENDED yet is always a not-yet-happened
            # session, never the one the chart is showing. An actually open market's own
            # regular.end is legitimately still in the future, so this only applies while
            # closed.
            if not m.get("is_open"):
                regular = bounds.get("regular")
                now_for_bounds = now_epoch if now_epoch is not None else time.time()
                if regular and datetime.fromisoformat(regular["end"]).timestamp() > now_for_bounds:
                    continue
            # Yahoo's chart-meta currentTradingPeriod reports a "pre"/"post" WINDOW for every
            # listed exchange, including ones that never get real extended-hours ticks (some
            # venues' own pre/post windows are just a pre-open auction + settlement period, not
            # continuously-traded sessions) — showing dotted pre/post bands for a window with
            # no real data behind it would fabricate an extended session. Checked directly
            # against the actual fetched bars (_has_real_extended_bars), never inferred from an
            # unrelated proxy like a delay flag (owner correction, 2026-08-31: a real-time venue
            # doesn't automatically have a real extended session either).
            #
            # Extended-hours support is a property of the VENUE, not of the specific window: a
            # window that hasn't happened yet today (POST, checked during PRE/REGULAR) has zero
            # bars to count regardless of whether the venue is capable — so ONE checkable
            # window's verdict (True or False) is trusted for BOTH, rather than judging each
            # independently and wrongly dropping a real post-market band that simply hasn't
            # started yet (owner-reproduced live bug, 2026-08-31).
            verdicts = [
                _has_real_extended_bars(
                    s, bounds[seg]["start"], bounds[seg]["end"], now_epoch=now_epoch
                )
                for seg in ("pre", "post")
                if seg in bounds
            ]
            known = [v for v in verdicts if v is not None]
            has_extended_capability = any(known) if known else True  # nothing checkable yet
            if not has_extended_capability:
                bounds = {"regular": bounds["regular"]} if "regular" in bounds else None
            if bounds:
                out[s] = {**m, "session_bounds": bounds}
    return out


# ── Historical series (real live source for the asset-detail chart) ────────────
# The chart used to read only the locally-accumulated PriceHistoryStore (~days of
# data). These pull REAL history from the live source instead, cached per (symbol, tf).
_HISTORY_PERIOD = {"1M": "1mo", "6M": "6mo", "YTD": "ytd", "1Y": "1y", "5Y": "5y", "MAX": "max"}
_HISTORY_CG_DAYS: dict[str, object] = {
    "1D": 1,
    "5D": 5,
    "1M": 30,
    "6M": 180,
    "YTD": 365,
    "1Y": 365,
    "5Y": 1825,
    "MAX": "max",
}
_HISTORY_TTL = 1800.0  # 30 min for multi-day windows
_HISTORY_TTL_1D = 60.0  # 1 min — keep the 1D intraday chart near-real-time on trading days
# CoinGecko intraday is ~30-minute candles (free tier) and every fetch now costs TWO calls
# (market_chart + /ohlc), so a 60s TTL re-buys unchanged data 30× over while burning the
# shared per-IP budget the price monitor also draws on — the measured cause of live 429s
# (2026-07-16: BTC 1D collapsed to the prev-close seed line while the log filled with 429s).
_HISTORY_TTL_CG_INTRADAY = 300.0
_HISTORY_FAIL_TTL = 30.0  # short back-off for an empty/failed fetch (e.g. a CoinGecko 429) so a
# rate-limited tape isn't re-hammered on every 60s poll / timeframe click; it retries soon after
_history_cache: dict[tuple[str, str, str], tuple[float, list]] = {}
# Last-good /ohlc candles per (coin, timeframe, vs) — reused through a TRANSIENT /ohlc
# failure so one 429 on the candle leg (while market_chart succeeded) doesn't ship a
# close-only series that greys out Candle/Bar for a whole cache TTL. Staleness bound
# matches CoinGecko's own intraday candle cadence (30-minute buckets).
_CG_OHLC_STALE_MAX = 1800.0
_cg_ohlc_cache: dict[tuple[str, str, str], tuple[float, list]] = {}

# ── OHLCV granularity (asset-chart-types) ──────────────────────────────────────
# Equity (yfinance) interval per timeframe, + a fallback interval retried when the primary
# comes back empty (a thin/foreign ticker, or a request landing outside the primary
# interval's max lookback — e.g. Yahoo's 1m cap is a trailing 7 days).
_YF_OHLCV_INTERVAL: dict[str, tuple[str, str | None]] = {
    "1D": ("1m", "5m"),
    "5D": ("15m", "30m"),
    "1M": ("1d", None),
    "6M": ("1d", None),
    "YTD": ("1d", None),
    "1Y": ("1d", None),
    "5Y": ("1wk", None),  # coarser for long lookbacks — mirrors Yahoo Finance's own charts
    "MAX": ("1mo", None),
}

# CoinGecko's /ohlc endpoint (free plan) only accepts these exact `days` values — it applies
# its OWN candle width per tier (30-min candles ≤2d, 4h for 3-30d, 4-day for 31-365d).
_CG_OHLC_SUPPORTED_DAYS: tuple[int, ...] = (1, 7, 14, 30, 90, 180, 365)


def _ohlcv_granularity_for_timeframe(timeframe: str, asset_class: str) -> dict:
    """OHLCV fetch granularity for a chart timeframe.

    `asset_class == "crypto"` -> the CoinGecko `/ohlc` `days` tier to request: the smallest
    value in `_CG_OHLC_SUPPORTED_DAYS` that covers the same window `_HISTORY_CG_DAYS` fetches
    for the close-only series, rounded UP (free-tier `/ohlc` doesn't take an arbitrary integer).
    `best_effort=True` marks a window the 365d cap can't reach (5Y/MAX) — the close-only series
    still covers the full window via `/market_chart`; only the candle overlay is capped, and
    `price_series` degrades to `ohlc_available=False` for those rather than a partial candle set.

    Everything else (equity) -> the yfinance `interval` to request, with an optional
    `fallback_interval` retried when the primary interval's fetch comes back empty.
    """
    if (asset_class or "").lower() == "crypto":
        needed = _HISTORY_CG_DAYS.get(timeframe, 365)
        if needed == "max":
            return {"cg_days": _CG_OHLC_SUPPORTED_DAYS[-1], "best_effort": True}
        needed = int(needed)
        for tier in _CG_OHLC_SUPPORTED_DAYS:
            if tier >= needed:
                return {"cg_days": tier, "best_effort": False}
        return {"cg_days": _CG_OHLC_SUPPORTED_DAYS[-1], "best_effort": True}
    interval, fallback = _YF_OHLCV_INTERVAL.get(timeframe, ("1d", None))
    return {"interval": interval, "fallback_interval": fallback}


# Every normal Yahoo symbol fits this shape: equities/ETFs, share classes (BRK.B), indices
# (^GSPC), FX pairs (EURUSD=X), Yahoo-priceable crypto pairs (BTC-USD). Guards the ONE
# unvalidated string every `yf.Ticker(symbol)` call below interpolates into yfinance's own
# URL construction — shared by `_yf_history` and `_yf_previous_session_bars` so neither can
# be reached with an unchecked value (security review 2026-09-08).
# `\Z`, not `$` — `$` matches just before a TRAILING NEWLINE too (a Python `re` gotcha), which
# would let "MSFT\n" slip through `.match()` unrejected.
_YAHOO_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-^=]{1,20}\Z")


def _assert_yahoo_symbol(symbol: str) -> None:
    if not _YAHOO_SYMBOL_RE.match(symbol or ""):
        raise ValidationError(f"not a valid Yahoo symbol: {symbol!r}")


def _yf_history(symbol: str, timeframe: str, *, ohlcv: bool = True) -> list[dict]:
    """Bars from yfinance: intraday bars for the short windows (1D/5D) so those charts read
    like a real intraday tape; daily bars for 1M/6M/YTD/1Y, weekly for 5Y, monthly for MAX
    (mirrors how Yahoo Finance itself coarsens long lookbacks — see
    `_ohlcv_granularity_for_timeframe`). `price` stays the Close alias; open/high/low/close/
    volume ride the SAME row from the SAME call, so (unlike crypto) there's no separate
    coverage/merge concern — either the fetch has full OHLCV or the row is dropped.

    `ohlcv=False` reproduces the PRE-OHLCV-feature behavior exactly (a stable interval per
    timeframe with NO 1D/5D fallback probe, close price only) — `backfill_history` passes
    this so `price_history.json`'s daily-close series (CLAUDE.md: computed net worth trusts
    it as daily closes) never silently drifts to weekly/monthly just because it happens to
    reuse the "5Y"/"MAX" lookback token the chart's OWN granularity table treats specially."""
    _assert_yahoo_symbol(symbol)
    if not ohlcv:
        if timeframe == "1D":
            h = yf.Ticker(symbol).history(period="1d", interval="5m")
            intraday = True
        elif timeframe == "5D":
            h = yf.Ticker(symbol).history(period="5d", interval="30m")
            intraday = True
        else:
            h = yf.Ticker(symbol).history(period=_HISTORY_PERIOD.get(timeframe, "1y"))
            intraday = False
        out: list[dict] = []
        for idx, row in h.iterrows():
            close = _finite(row.get("Close"))
            if close is None:
                continue
            out.append(
                {
                    "date": idx.isoformat() if intraday else idx.strftime("%Y-%m-%d"),
                    "price": _round_price(close),
                }
            )
        return out

    gran = _ohlcv_granularity_for_timeframe(timeframe, "equity")
    interval = gran["interval"]
    intraday = timeframe in ("1D", "5D")
    if timeframe == "1D":
        period = "1d"
    elif timeframe == "5D":
        period = "5d"
    else:
        period = _HISTORY_PERIOD.get(timeframe, "1y")

    # prepost=True on the INTRADAY windows only (1D/5D) — this is the chart-display path;
    # `ohlcv=False` above (net worth's own daily-close backfill) stays regular-session-only,
    # same as `_quote_to_price`'s own display-only rule for the live quote (owner ask
    # 2026-08-31: the 1D chart otherwise replays the LAST completed regular session's bars
    # while pre/post is active — e.g. Friday's tape shown as if it were "today" — instead of
    # the real, live pre/post-market ticks Yahoo already has).
    h = yf.Ticker(symbol).history(period=period, interval=interval, prepost=intraday)
    if h.empty and gran["fallback_interval"]:
        h = yf.Ticker(symbol).history(
            period=period, interval=gran["fallback_interval"], prepost=intraday
        )
    return _ohlcv_points_from_rows(h, intraday)


def _ohlcv_points_from_rows(h, intraday: bool) -> list[dict]:
    """Row -> point-dict conversion shared by `_yf_history`'s live fetch and
    `_yf_previous_session_bars`'s widened fallback fetch below — identical OHLCV shape
    either way, only the source frame differs."""
    out: list[dict] = []
    for idx, row in h.iterrows():
        close = _finite(row.get("Close"))
        if close is None:
            continue
        point: dict = {
            "date": idx.isoformat() if intraday else idx.strftime("%Y-%m-%d"),
            "price": _round_price(close),
        }
        o, hi, lo = _finite(row.get("Open")), _finite(row.get("High")), _finite(row.get("Low"))
        if o is not None and hi is not None and lo is not None:
            point["open"] = _round_price(o)
            point["high"] = _round_price(hi)
            point["low"] = _round_price(lo)
            point["close"] = point["price"]
        vol = _finite(row.get("Volume"))
        if vol is not None:
            point["volume"] = vol
        out.append(point)
    return out


def _yf_previous_session_bars(symbol: str) -> tuple[list[dict], str | None]:
    """1D-chart fallback for a non-extended-hours venue (`hasPrePostMarketData: false`, e.g.
    XETRA) viewed before its regular session opens or shortly after it closes: Yahoo's chart
    API returns ZERO bars for BOTH the 1m and 5m probes in that window — there's no live
    pre-market print to serve either, unlike a US ticker — while Yahoo's own web frontend
    quietly draws the prior session instead of erroring. Widens the request to a trailing
    week (same interval fallback chain, still `prepost=True`) and returns ONLY the single
    most recent COMPLETE session's bars — never today's own bucket, even a stray one-off
    after-hours print — so the caller can flag this result distinctly rather than present it
    as live. `([], None)` when even the widened fetch has no session older than today (a
    dead/delisted symbol). See ADR `2026-09-08-1d-chart-falls-back-to-the-previous-session`."""
    _assert_yahoo_symbol(symbol)
    gran = _ohlcv_granularity_for_timeframe("1D", "equity")
    for interval in (gran["interval"], gran["fallback_interval"]):
        if not interval:
            continue
        h = yf.Ticker(symbol).history(period="5d", interval=interval, prepost=True)
        if h.empty:
            continue
        tz = h.index.tz
        today = datetime.now(tz if tz else timezone.utc).date()
        session_dates = sorted({d for d in h.index.date if d < today})
        if not session_dates:
            continue
        session_date = session_dates[-1]
        points = _ohlcv_points_from_rows(h[h.index.date == session_date], intraday=True)
        if points:
            return points, session_date.isoformat()
    return [], None


def _cg_ohlc_candles(coin_id: str, timeframe: str, vs: str) -> list[dict]:
    """Raw candles from CoinGecko's dedicated /ohlc endpoint: own days-tier, own candle width
    (see `_ohlcv_granularity_for_timeframe`). Each candle keeps its epoch seconds for the
    nearest-timestamp merge in `_merge_cg_ohlc`; `_cg_history` pops it before returning."""
    days = _ohlcv_granularity_for_timeframe(timeframe, "crypto")["cg_days"]
    try:
        data = coingecko_client.get_json(
            f"/coins/{urllib.parse.quote(coin_id, safe='')}/ohlc",
            {"vs_currency": (vs or "usd").lower(), "days": days},
        )
    except Exception:
        raise RuntimeError("CoinGecko unavailable") from None
    intraday = timeframe == "1D"
    out: list[dict] = []
    for row in data or []:
        if len(row) < 5:
            continue
        ms, o, hi, lo, c = row[0], row[1], row[2], row[3], row[4]
        gm = time.gmtime(ms / 1000)
        key = time.strftime("%Y-%m-%dT%H:%M:%S", gm) if intraday else time.strftime("%Y-%m-%d", gm)
        out.append(
            {
                "date": key,
                "epoch": ms / 1000.0,
                "open": _round_price(float(o)),
                "high": _round_price(float(hi)),
                "low": _round_price(float(lo)),
                "close": _round_price(float(c)),
            }
        )
    return out


def _cg_ohlc_covers(base_points: list[dict], ohlc_points: list[dict]) -> bool:
    """Whether the OHLC candle series reaches back far enough to cover the close series' full
    requested window. CoinGecko's free `/ohlc` plan caps at 365 days, so a 5Y/MAX request
    degrades to a plain close(+volume) series (best-effort, `ohlc_available=False`) rather
    than a chart that's candled for its recent slice and line-only further back."""
    if not base_points or not ohlc_points:
        return False
    earliest_base = base_points[0]["_epoch"]
    earliest_ohlc = ohlc_points[0]["epoch"]
    latest_ohlc = ohlc_points[-1]["epoch"]
    # Tolerance = one candle's own spacing, so a first candle that lands slightly after the
    # base series' first sample (bucket-boundary rounding) doesn't spuriously fail coverage.
    span = max(latest_ohlc - earliest_ohlc, 1.0)
    tolerance = span / max(len(ohlc_points) - 1, 1)
    return earliest_ohlc <= earliest_base + tolerance


def _merge_cg_ohlc(base_points: list[dict], ohlc_points: list[dict]) -> None:
    """Attach each base (close/volume) point its nearest-timestamp OHLC candle, IN PLACE —
    `date`/`price`/`volume` are untouched, this only adds open/high/low/close. OHLC candles
    are often coarser than the close series (CoinGecko buckets by days-tier, not by
    `/market_chart`'s own sample rate), so several adjacent points may legitimately share one
    candle's O/H/L/close — that's expected, not a merge bug."""
    epochs = [c["epoch"] for c in ohlc_points]
    for p in base_points:
        idx = bisect.bisect_left(epochs, p["_epoch"])
        candidates = [i for i in (idx - 1, idx) if 0 <= i < len(ohlc_points)]
        if not candidates:
            continue
        nearest = min(candidates, key=lambda i: abs(ohlc_points[i]["epoch"] - p["_epoch"]))
        c = ohlc_points[nearest]
        p["open"] = c["open"]
        p["high"] = c["high"]
        p["low"] = c["low"]
        p["close"] = c["close"]


def _cg_history(coin_id: str, timeframe: str, vs: str = "usd", *, ohlcv: bool = True) -> list[dict]:
    """Historical OHLCV from CoinGecko. Close(+volume) comes from `/market_chart` — the
    SAME call as before this feature, so `price`/`date` stay byte-identical to the pre-OHLCV
    series; `total_volumes` rides the same response (parallel array, same cadence) so volume
    is free. Open/high/low come from the dedicated `/ohlc` endpoint, overlaid by
    nearest-timestamp merge ONLY when it covers the full requested window (`_cg_ohlc_covers`)
    — otherwise the OHLC fetch/merge is skipped entirely and the caller gets a plain
    close(+volume) series (never raises; any OHLC-side failure degrades gracefully).

    `ohlcv=False` (used by `backfill_history`) skips the `/ohlc` fetch AND the volume merge
    entirely, returning a plain `{date, price}` list — reproduces the PRE-OHLCV-feature
    behavior exactly, since `record_backfill_series` only ever reads `date`/`price` anyway
    and `price_history.json` must not gain an extra CoinGecko round-trip (and its rate-limit
    exposure) it has no use for."""
    days = _HISTORY_CG_DAYS.get(timeframe, 365)
    try:
        data = coingecko_client.get_json(
            f"/coins/{urllib.parse.quote(coin_id, safe='')}/market_chart",
            {"vs_currency": (vs or "usd").lower(), "days": days},
        )
    except Exception:
        raise RuntimeError("CoinGecko unavailable") from None
    intraday = timeframe == "1D"
    volumes = data.get("total_volumes") or []
    out: list[dict] = []
    seen: dict[str, int] = {}
    for i, entry in enumerate(data.get("prices", []) or []):
        ms, price = entry
        gm = time.gmtime(ms / 1000)
        key = time.strftime("%Y-%m-%dT%H:%M:%S", gm) if intraday else time.strftime("%Y-%m-%d", gm)
        point: dict = {"date": key, "price": _round_price(float(price)), "_epoch": ms / 1000.0}
        if i < len(volumes):
            vol = _finite(volumes[i][1])
            if vol is not None:
                point["volume"] = vol
        if not intraday and key in seen:
            out[seen[key]] = point  # last price of the day wins
            continue
        seen[key] = len(out)
        out.append(point)

    if not ohlcv:
        return [{"date": p["date"], "price": p["price"]} for p in out]

    ohlc_key = (coin_id, timeframe, (vs or "usd").lower())
    try:
        ohlc = _cg_ohlc_candles(coin_id, timeframe, vs)
    except Exception:
        _log.warning("prices: coingecko ohlc fetch failed %s/%s", coin_id, timeframe, exc_info=True)
        ohlc = []
    if ohlc:
        _cg_ohlc_cache[ohlc_key] = (time.monotonic(), ohlc)
    else:
        # Transient /ohlc failure (a 429 during a rate-limit burst — the market_chart leg
        # above succeeded): reuse the last-good candles rather than shipping a close-only
        # series that greys out Candle/Bar for the whole cache TTL. Bounded staleness —
        # CoinGecko's own intraday candles are 30-minute buckets, so candles up to 30 min
        # old match the source's native update cadence; past that, degrade for real.
        hit = _cg_ohlc_cache.get(ohlc_key)
        if hit and (time.monotonic() - hit[0]) < _CG_OHLC_STALE_MAX:
            ohlc = hit[1]
    if ohlc and _cg_ohlc_covers(out, ohlc):
        _merge_cg_ohlc(out, ohlc)

    for p in out:
        p.pop("_epoch", None)
    return out


def _parse_coin_facts(data: dict) -> dict:
    """Shape a CoinGecko /coins/{id} payload into the crypto Key-Facts block. Pure —
    network/caching live in fetch_coin_facts. All money fields are USD (CG default)."""
    md = data.get("market_data") or {}

    def _usd(d: object) -> object:
        return d.get("usd") if isinstance(d, dict) else None

    cats = [c for c in (data.get("categories") or []) if c]
    homepages = [h for h in ((data.get("links") or {}).get("homepage") or []) if h]
    return {
        "category": cats[0] if cats else None,
        # asset_platform_id is the host chain for tokens (e.g. "ethereum" for PEPE);
        # null for native L1 coins (BTC/ETH) — the frontend hides the row then.
        "network": data.get("asset_platform_id") or None,
        "market_cap": _usd(md.get("market_cap")),
        "market_cap_rank": data.get("market_cap_rank") or md.get("market_cap_rank"),
        "circulating_supply": md.get("circulating_supply"),
        "max_supply": md.get("max_supply"),
        "ath": _usd(md.get("ath")),
        "ath_date": _usd(md.get("ath_date")),
        "genesis_date": data.get("genesis_date"),
        "homepage": homepages[0] if homepages else None,
    }


_COIN_FACTS_TTL = 1800.0  # 30 min — supply/rank/ath/category move slowly
# Raw /coins/{id} payload cache, shared by fetch_coin_facts (Key Facts) and
# fetch_crypto_quote_stats (the stats strip) — both widgets load on the same asset-detail
# view, so sharing the payload means ONE CoinGecko call per coin per TTL, not two
# (rate-limit pressure is a live concern — see _HISTORY_TTL_CG_INTRADAY's incident note).
_coin_payload_cache: dict[str, tuple[float, dict]] = {}


def _fetch_coin_payload(coin_id: str) -> dict:
    """Raw CoinGecko /coins/{id} JSON (market_data included), cached 30 min.
    Returns {} on failure so callers render what they have."""
    if not coin_id:
        return {}
    hit = _coin_payload_cache.get(coin_id)
    if hit and (time.monotonic() - hit[0]) < _COIN_FACTS_TTL:
        return hit[1]
    try:
        if get_settings().onw_qa_mode:
            from backend.services import market_data_qa

            data = market_data_qa.metadata("coingecko", coin_id)
        else:
            data = coingecko_client.get_json(
                f"/coins/{urllib.parse.quote(coin_id, safe='')}",
                {
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "true",
                    "community_data": "false",
                    "developer_data": "false",
                    "sparkline": "false",
                },
                timeout=10,
            )
        if not isinstance(data, dict):
            raise ValueError("_fetch_coin_payload: non-object response")
    except Exception:
        _log.warning("prices: coin payload fetch failed (%s)", type(coin_id).__name__)
        return {}
    _coin_payload_cache[coin_id] = (time.monotonic(), data)
    return data


def fetch_coin_facts(coin_id: str) -> dict:
    """Real crypto metadata (market cap, supply, ATH, category, launch) from CoinGecko
    /coins/{id}, cached. Returns {} on failure so the caller renders what it has."""
    data = _fetch_coin_payload(coin_id)
    return _parse_coin_facts(data) if data else {}


def _parse_crypto_stats(data: dict, vs: str) -> dict:
    """Shape a CoinGecko /coins/{id} payload into the crypto quote-stats strip (the crypto
    counterpart of Yahoo's fetch_quote_stats shape). Money fields are extracted in `vs`
    (the asset's display currency — see asset_detail._display_currency) so the strip agrees
    with the headline/chart; supplies are coin units, currency-free. Pure — network/caching
    live in fetch_crypto_quote_stats. Missing fields are absent (caller renders '—')."""
    md = data.get("market_data") or {}
    cur = (vs or "usd").lower()

    def _in_vs(field: str) -> object:
        d = md.get(field)
        return d.get(cur) if isinstance(d, dict) else None

    raw = {
        "market_cap": _in_vs("market_cap"),
        "fdv": _in_vs("fully_diluted_valuation"),
        "volume_24h": _in_vs("total_volume"),
        "day_low": _in_vs("low_24h"),
        "day_high": _in_vs("high_24h"),
        "ath": _in_vs("ath"),
        "atl": _in_vs("atl"),
        "circulating_supply": md.get("circulating_supply"),
        "total_supply": md.get("total_supply"),
        "max_supply": md.get("max_supply"),
        "genesis_date": data.get("genesis_date"),
    }
    stats = {k: v for k, v in raw.items() if v is not None}
    # Vol/Market cap (24h) — the liquidity ratio Yahoo/CG surface; derived, never stored.
    mc, vol = stats.get("market_cap"), stats.get("volume_24h")
    if isinstance(mc, (int, float)) and isinstance(vol, (int, float)) and mc > 0:
        stats["vol_to_mcap"] = vol / mc
    return stats


def fetch_crypto_quote_stats(coin_id: str, vs: str) -> dict:
    """Crypto per-quote stats (market cap, FDV, 24h volume/range, ATH/ATL, supplies,
    launch date) from the shared /coins/{id} payload, extracted in `vs`. Falls back to
    USD when CoinGecko doesn't serve that fiat. Returns {"currency": ..., "stats": {...}}
    with empty stats on failure so the caller hides the strip."""
    data = _fetch_coin_payload(coin_id)
    if not data:
        return {"currency": (vs or "USD").upper(), "stats": {}}
    stats = _parse_crypto_stats(data, vs)
    currency = (vs or "USD").upper()
    if not any(k in stats for k in ("market_cap", "volume_24h", "day_low")):
        usd_stats = _parse_crypto_stats(data, "usd")
        if usd_stats:
            return {"currency": "USD", "stats": usd_stats}
    return {"currency": currency, "stats": stats}


def fetch_history(
    price_source_id: str, timeframe: str, price_source: str = "yahoo", vs: str = "usd"
) -> list[dict]:
    """Real historical close series for the asset-detail chart, from the live source
    (Yahoo via yfinance, or CoinGecko market_chart for crypto). Cached per (symbol, tf, vs)
    — the vs-currency is part of the key so a USD fetch and an SGD fetch for the same coin
    don't collide. Returns [] on failure so the caller can fall back to the local store."""
    return fetch_history_result(price_source_id, timeframe, price_source, vs)["points"]


def fetch_history_result(
    price_source_id: str, timeframe: str, price_source: str = "yahoo", vs: str = "usd"
) -> dict:
    """Return an honest chart result while preserving ``fetch_history``'s points-only API."""
    if not price_source_id:
        return {
            "provider": None,
            "state": "unavailable",
            "as_of": None,
            "degraded_reason": "no_data",
            "points": [],
        }
    key = (price_source_id, timeframe, (vs or "usd").lower())
    is_cg = (price_source or "").lower() == "coingecko"
    if is_cg:
        from backend.services.crypto_market_data import fetch_crypto_history

        def yahoo_fetch(alias: str, window: str) -> list[dict]:
            if get_settings().onw_qa_mode:
                from backend.services import market_data_qa

                return market_data_qa.history(
                    "yahoo", price_source_id, window, vs, provider_id=alias
                )
            return _yf_history(alias, window)

        def coingecko_fetch(canonical_id: str, window: str, currency: str) -> list[dict]:
            if get_settings().onw_qa_mode:
                from backend.services import market_data_qa

                return market_data_qa.history("coingecko", canonical_id, window, currency)
            return _cg_history(canonical_id, window, currency)

        return fetch_crypto_history(
            price_source_id,
            timeframe,
            vs,
            yahoo_fetch=yahoo_fetch,
            coingecko_fetch=coingecko_fetch,
        )
    # 1D and 5D are intraday tapes — keep them near-real-time on trading days. CoinGecko
    # intraday is coarse (~30-min candles) and rate-limit-sensitive, so it refreshes on a
    # slower clock than Yahoo's per-minute bars (see _HISTORY_TTL_CG_INTRADAY).
    if timeframe in ("1D", "5D"):
        ttl = _HISTORY_TTL_CG_INTRADAY if is_cg else _HISTORY_TTL_1D
    else:
        ttl = _HISTORY_TTL
    hit = _history_cache.get(key)
    if hit:
        # A cached EMPTY result (a failed/rate-limited fetch) backs off for a short window so we
        # don't re-hammer CoinGecko on every poll/click; a real series keeps the normal TTL.
        eff_ttl = ttl if hit[1] else _HISTORY_FAIL_TTL
        if (time.monotonic() - hit[0]) < eff_ttl:
            if not hit[1] and timeframe == "1D" and not is_cg:
                # `allow_fetch=False`: this branch's entire purpose is a back-off window on
                # a cached-empty live result — issuing a fresh widened fetch here would
                # defeat that back-off (security review 2026-09-08: a request that used to
                # cost 0 outbound calls was costing up to 2). Only an ALREADY-cached
                # previous-session answer is served; the widened fetch happens exclusively
                # from the post-live-fetch path below.
                fallback = _previous_session_response(price_source_id, allow_fetch=False)
                if fallback:
                    return fallback
            return {
                "provider": "yahoo" if hit[1] else None,
                "state": "live" if hit[1] else "unavailable",
                "as_of": hit[1][-1].get("date") if hit[1] else None,
                "degraded_reason": None if hit[1] else "upstream_unavailable",
                "points": hit[1],
            }
    try:
        if is_cg:
            points = _cg_history(price_source_id, timeframe, vs)
        else:
            points = _yf_history(price_source_id, timeframe)
    except Exception:
        _log.warning(
            "prices: history fetch failed %s/%s", price_source_id, timeframe, exc_info=True
        )
        points = []
    if not points and hit and hit[1] and timeframe == "1D" and not _tape_is_today(hit[1]):
        # A 1D tape cached during an EARLIER session (the process stayed up overnight) plus an
        # empty refresh before today's open is the routine pre-open shape on a venue with no
        # pre-market data (e.g. XETRA) — not an outage. Serve it through the silent
        # previous-session path instead of the "live providers unavailable" stale banner.
        fallback = _previous_session_response(price_source_id)
        if fallback:
            _history_cache[key] = (time.monotonic(), points)
            return fallback
    if not points and hit and hit[1]:
        # Failed refresh of a series we HAVE: serve the stale-but-real tape rather than
        # replacing good data with an empty one (which the chart renders as a bogus
        # 2-point prev-close seed line). Re-stamp so the next retry waits out the
        # failure back-off window, then falls through to a real fetch again.
        _history_cache[key] = (time.monotonic() - ttl + _HISTORY_FAIL_TTL, hit[1])
        return {
            "provider": "yahoo",
            "state": "stale",
            "as_of": hit[1][-1].get("date"),
            "degraded_reason": "upstream_unavailable",
            "points": hit[1],
        }
    # Cache the LIVE fetch's own outcome (success or failure) exactly as before — the
    # previous-session fallback below is a SEPARATE, distinctly-flagged result and must
    # never be written into THIS cache as if it were live/stale-live data, or a later
    # transient live failure would serve it back mislabeled as "upstream_unavailable"
    # (losing the "this is yesterday's session, not today's" disclosure). `_previous_session_
    # response` has its own (longer-lived) memoization, so re-checking it on every call here —
    # including from the early-return branch above — never re-issues the widened fetch.
    _history_cache[key] = (time.monotonic(), points)
    if not points and timeframe == "1D" and not is_cg:
        fallback = _previous_session_response(price_source_id)
        if fallback:
            return fallback
    return {
        "provider": "yahoo" if points else None,
        "state": "live" if points else "unavailable",
        "as_of": points[-1].get("date") if points else None,
        "degraded_reason": None if points else "upstream_unavailable",
        "points": points,
    }


# A completed prior session's bars don't change again — cache them for the same window as any
# other multi-day series (_HISTORY_TTL), well past the 1D live tape's own 60s TTL, so the
# (comparatively expensive) 5-day widened fetch above isn't re-issued on every 60s poll while
# the venue is still closed. Separate dict from `_history_cache`: see the comment above this
# fallback's call site for why the two must never share a slot.
#
# BOTH outcomes are cached — a positive `(ts, points, session_date)` for _HISTORY_TTL, AND a
# negative `(ts, [], None)` for the shorter _HISTORY_FAIL_TTL (security review 2026-09-08: an
# uncached negative result meant every poll re-issued the widened fetch, and under a Yahoo 429
# the response to being rate-limited was to TRIPLE the request rate — the empty result never
# stopped retrying). `session_date is None` is exactly what marks an entry negative; a genuine
# session date is never falsy.
#
# Keyed by a bare symbol string reachable from an LLM tool (`yahoo_get_price_history`, no cap
# of its own) — bounded to `_PREV_SESSION_CACHE_MAX` entries, pruning expired rows first, so an
# unbounded set of distinct tickers can't grow this dict without limit.
_prev_session_cache: dict[str, tuple[float, list[dict], str | None]] = {}
_PREV_SESSION_CACHE_MAX = 300


def _prev_session_cache_set(symbol: str, points: list[dict], session_date: str | None) -> None:
    _prev_session_cache[symbol] = (time.monotonic(), points, session_date)
    if len(_prev_session_cache) <= _PREV_SESSION_CACHE_MAX:
        return
    now = time.monotonic()
    for sym, (ts, _pts, sd) in list(_prev_session_cache.items()):
        if now - ts >= (_HISTORY_TTL if sd else _HISTORY_FAIL_TTL):
            del _prev_session_cache[sym]
    if len(_prev_session_cache) > _PREV_SESSION_CACHE_MAX:
        oldest = sorted(_prev_session_cache.items(), key=lambda kv: kv[1][0])
        for sym, _entry in oldest[: len(_prev_session_cache) - _PREV_SESSION_CACHE_MAX]:
            del _prev_session_cache[sym]


def _tape_is_today(points: list[dict]) -> bool:
    """Whether an intraday tape's last bar falls on today in the bar's own (exchange) tz.
    Unparseable -> True, keeping the caller's conservative stale-but-real behavior."""
    try:
        last = datetime.fromisoformat(str(points[-1].get("date")))
    except (ValueError, TypeError):
        return True
    tz = last.tzinfo or timezone.utc
    return last.astimezone(tz).date() >= datetime.now(tz).date()


def _previous_session_response(price_source_id: str, *, allow_fetch: bool = True) -> dict | None:
    """The `fetch_history_result` previous-session-fallback response, or None when even the
    widened fetch can't find a session that isn't today's.

    `allow_fetch=False` consults ONLY an already-populated `_prev_session_cache` entry and
    never itself calls `_yf_previous_session_bars` — used from the live-fetch back-off
    early-return in `fetch_history_result`, so that window's whole purpose (bounding how
    often Yahoo gets hit while a result is cached-empty) can't be defeated by this fallback
    re-fetching on every call through it. The widened fetch fires exclusively from the
    default `allow_fetch=True` call, in the post-live-fetch path."""
    hit = _prev_session_cache.get(price_source_id)
    if hit:
        eff_ttl = _HISTORY_TTL if hit[2] else _HISTORY_FAIL_TTL
        if (time.monotonic() - hit[0]) < eff_ttl:
            points, session_date = hit[1], hit[2]
            return _previous_session_dict(points, session_date) if session_date else None
    if not allow_fetch:
        return None
    try:
        points, session_date = _yf_previous_session_bars(price_source_id)
    except Exception:
        _log.warning("prices: previous-session fetch failed %s", price_source_id, exc_info=True)
        points, session_date = [], None
    _prev_session_cache_set(price_source_id, points, session_date)
    return _previous_session_dict(points, session_date) if session_date else None


def _previous_session_dict(points: list[dict], session_date: str) -> dict:
    return {
        "provider": "yahoo",
        "state": "stale",
        "as_of": points[-1].get("date"),
        "degraded_reason": "previous_session",
        "session_date": session_date,
        "points": points,
    }


# ── Price-history backfill (rebuildable cache of real daily closes) ──────────
# Adds historical depth for any held instrument so the returns engine and
# portfolio_history can reconstruct deep value series. Only real upstream closes
# are stored; derived values (TWR/MWR/value series) are never persisted.
# ADR 0019 records the no-backfill-pillar bend.
_BACKFILL_TIMEFRAME = "5Y"  # maps to "5y" Yahoo / 1825 CG days — covers the standard windows


def _settled_only(points: list[dict], before: str | None) -> list[dict]:
    """Drop bars dated on or after ``before`` — a session still trading returns a PARTIAL bar,
    and the store never overwrites a date, so it would stand as that day's close for good."""
    if not before:
        return points
    return [p for p in points if str(p.get("date", ""))[:10] < before]


def backfill_history(
    price_source_id: str, *, lookback: str = _BACKFILL_TIMEFRAME, before: str | None = None
) -> int:
    """Fetch and cache historical daily closes for one instrument (by price_source_id).

    Resolves the fetch source from the instrument master:
      * price_source=="yahoo"      → _yf_history(price_source_id, lookback)
      * price_source=="coingecko"  → _cg_history(price_source_id, lookback, vs="usd")
      * price_source=="manual"/unknown/not found → return 0 (no upstream to fetch from)

    Existing points for a date are NEVER overwritten (idempotent upsert-skip) so a
    re-run is a no-op and live/prior-backfill points are preserved. Returns the count
    of points newly written; returns 0 on any network/parse error (fail-safe)."""
    if not price_source_id:
        return 0
    from backend.db.instrument_store import InstrumentStore
    from backend.db.price_history_store import PriceHistoryStore

    try:
        # Single InstrumentStore instance; try both sources with one object.
        store = InstrumentStore()
        inst = store.find_by_source("yahoo", price_source_id) or store.find_by_source(
            "coingecko", price_source_id
        )
    except Exception as exc:
        _log.warning("backfill_history: instrument lookup failed (%s)", type(exc).__name__)
        return 0

    # If instrument not found or is manual, there is no upstream to fetch from.
    if inst is None:
        return 0
    price_source = (inst.get("price_source") or "").lower()
    if price_source == "manual" or price_source not in ("yahoo", "coingecko"):
        return 0

    try:
        if price_source == "coingecko":
            points = _cg_history(price_source_id, lookback, "usd", ohlcv=False)
        else:
            points = _yf_history(price_source_id, lookback, ohlcv=False)
        points = _settled_only(points, before)
    except Exception as exc:
        _log.warning(
            "backfill_history: fetch failed for %s (%s)", price_source_id, type(exc).__name__
        )
        return 0

    if not points:
        return 0

    try:
        added = PriceHistoryStore().record_backfill_series(price_source_id, points)
    except Exception as exc:
        _log.warning(
            "backfill_history: store write failed for %s (%s)", price_source_id, type(exc).__name__
        )
        return 0

    _log.info("backfill_history: %s → %d new point(s)", price_source_id, added)
    return added


# Pacing: sleep this many seconds between instruments in the startup sweep so the
# bulk backfill doesn't burst the external APIs or starve the live poller.
_BACKFILL_SWEEP_PACE_SECS = 0.5


def backfill_held_instruments(*, lookback: str = _BACKFILL_TIMEFRAME) -> dict[str, int]:
    """Backfill historical closes for every currently-held instrument with a price_source_id.

    "Held" = any position or lot referencing the instrument. Best-effort: individual
    instrument failures are logged and skipped so one bad ticker can't abort the sweep.
    A small per-instrument delay (_BACKFILL_SWEEP_PACE_SECS) keeps the startup sweep
    from bursting external APIs. Returns {price_source_id: newly_written_count}."""
    import time as _time

    from backend.db.instrument_store import InstrumentStore
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore

    try:
        # Collect instrument_ids referenced by any position or lot; use public .all()
        held_ids: set[str] = set()
        for pos in PositionStore().all():
            iid = pos.get("instrument_id")
            if iid:
                held_ids.add(iid)
        for lot in LotStore().all():
            iid = lot.get("instrument_id")
            if iid:
                held_ids.add(iid)
    except Exception as exc:
        _log.warning("backfill_held_instruments: store read failed (%s)", type(exc).__name__)
        return {}

    insts = InstrumentStore()
    results: dict[str, int] = {}
    for iid in held_ids:
        try:
            inst = insts.find_by_id(iid)
        except Exception:
            continue
        if not inst:
            continue
        pid = inst.get("price_source_id") or ""
        if not pid or (inst.get("price_source") or "").lower() == "manual":
            continue
        try:
            results[pid] = backfill_history(pid, lookback=lookback)
        except Exception as exc:
            _log.warning("backfill_held_instruments: %s failed (%s)", pid, type(exc).__name__)
            results[pid] = 0
        _time.sleep(_BACKFILL_SWEEP_PACE_SECS)
    return results


def backfill_symbol_history(
    symbol: str, *, lookback: str = _BACKFILL_TIMEFRAME, before: str | None = None
) -> int:
    """Daily closes for a Yahoo symbol with NO instrument record (a ticker watched by typing its
    symbol), stored under the symbol itself — the key ``watch_context.history_for`` reads for
    such an item. Idempotent like ``backfill_history``; 0 on any failure."""
    from backend.db.price_history_store import PriceHistoryStore

    if not symbol:
        return 0
    try:
        points = _settled_only(_yf_history(symbol, lookback, ohlcv=False), before)
        return PriceHistoryStore().record_backfill_series(symbol, points) if points else 0
    except Exception as exc:
        _log.warning("backfill_symbol_history: %s failed (%s)", symbol, type(exc).__name__)
        return 0


# Symbols Yahoo had no sector for (funds, coins, unmapped listings) → when we last asked. A miss
# is re-asked only after _SECTOR_MISS_TTL, not on every pulse top-up and 12-hourly sweep.
_SECTOR_MISSES: dict[str, float] = {}
_SECTOR_MISS_TTL = 7 * 86400


def ensure_sector(symbol: str) -> str:
    """The symbol's Yahoo sector, fetched once and kept in the sectors cache ("" if none)."""
    if not symbol:
        return ""
    known = _SECTOR_CACHE.get(symbol)
    if known:
        return known
    if time.time() - _SECTOR_MISSES.get(symbol, float("-inf")) < _SECTOR_MISS_TTL:
        return ""
    sector = _fetch_sector(symbol)
    if sector:
        save_sectors_cache(_SECTOR_CACHE)
    else:
        _SECTOR_MISSES[symbol] = time.time()
    return sector


def backfill_watched_instruments(*, pace: float = _BACKFILL_SWEEP_PACE_SECS) -> dict[str, int]:
    """The 12-hourly ``watch_history`` job: top up every watched ticker's daily closes (by
    instrument, or by symbol when it has none), then the benchmarks and sector funds the pulse's
    trend line compares against, and fill in missing sectors. See ``services.watch_history``.
    Never under QA mode."""
    if get_settings().onw_qa_mode:
        return {}
    from backend.services import watch_history
    from backend.services.watchlist import read_watchlist

    return watch_history.sweep(read_watchlist().get("items") or [], pace=pace)


_SNAPSHOT_TTL = (
    180.0  # 3 min — the watch engine polls on a ~10 min cadence; this dedupes page loads
)
_snapshot_cache: dict[str, tuple[float, dict]] = {}


def _epoch_to_iso_utc(value: object) -> str | None:
    try:
        n = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n) or n <= 0:
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc).isoformat()


def _next_earnings(q: dict, now: float | None = None) -> str | None:
    """The next earnings date. ``earningsTimestamp`` is often the LAST report once it's passed,
    while ``earningsTimestampStart`` already names the next one — the soonest still-future
    timestamp wins, else the newest one Yahoo gave."""
    now = time.time() if now is None else now
    stamps: list[float] = []
    for key in ("earningsTimestamp", "earningsTimestampStart"):
        try:
            n = float(q.get(key))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if math.isfinite(n) and n > 0:
            stamps.append(n)
    if not stamps:
        return None
    future = [n for n in stamps if n >= now - 86400]
    return _epoch_to_iso_utc(min(future) if future else max(stamps))


def _snapshot_from_quote(q: dict) -> dict | None:
    """Compact watch-engine view of ONE raw v7 quote, or None when it carries no usable price.
    Every optional field is simply absent when Yahoo omitted it — never defaulted to zero."""
    price, prev = _quote_to_price(q)
    if price is None:
        return None
    raw = {
        "price": price,
        "prev_close": prev,
        "week52_high": _finite_positive(q.get("fiftyTwoWeekHigh")),
        "week52_low": _finite_positive(q.get("fiftyTwoWeekLow")),
        "avg_50d": _finite_positive(q.get("fiftyDayAverage")),
        "avg_200d": _finite_positive(q.get("twoHundredDayAverage")),
        "volume": _finite_positive(q.get("regularMarketVolume")),
        "avg_volume": _finite_positive(
            q.get("averageDailyVolume3Month") or q.get("averageDailyVolume10Day")
        ),
        "earnings_at": _next_earnings(q),
        # Session + size context for the Investments Watchlist view.
        "open": _finite_positive(q.get("regularMarketOpen")),
        "day_high": _finite_positive(q.get("regularMarketDayHigh")),
        "day_low": _finite_positive(q.get("regularMarketDayLow")),
        "market_cap": _finite_positive(q.get("marketCap")),
        "exchange": q.get("fullExchangeName") or q.get("exchange") or None,
        "currency": q.get("currency") or None,
        "name": q.get("shortName") or q.get("longName") or None,
        "market_state": q.get("marketState") or None,
        "analyst_rating": q.get("averageAnalystRating") or None,
        # For "volume on pace" (session hours by exchange) and valuation context on the pulse.
        "exchange_tz": q.get("exchangeTimezoneName") or None,
        # The exchange-local day this quote's session belongs to: history dated on or after it
        # is today's (partial) bar, never a settled close (`watch_context.settled`).
        "session_date": _local_day(q.get("regularMarketTime"), q.get("exchangeTimezoneName")),
        "trailing_pe": _finite_positive(q.get("trailingPE")),
        "forward_pe": _finite_positive(q.get("forwardPE")),
    }
    return {k: v for k, v in raw.items() if v is not None}


def cached_quote_snapshot(symbol: str) -> dict | None:
    """The last snapshot `fetch_quote_snapshots` saw for `symbol` (any age), or None. Never
    touches the network — for a reader (the asset page) that must not add a Yahoo round trip."""
    hit = _snapshot_cache.get((symbol or "").strip())
    return hit[1] if hit else None


def fetch_quote_snapshots(symbols: list[str]) -> dict[str, dict]:
    """{symbol: snapshot} for the symbols one batched Yahoo v7 quote resolved — price, prior close,
    52-week range, 50/200-day averages, volume vs its 3-month average, and the next earnings
    timestamp. ONE request per 50 symbols (the same call the price path already makes), cached 3 min.
    {} under QA mode or on any failure: the watch engine treats "no snapshot" as "say nothing"."""
    wanted = list(dict.fromkeys(s.strip() for s in symbols if s and s.strip()))
    if not wanted or get_settings().onw_qa_mode:
        return {}
    now = time.monotonic()
    out: dict[str, dict] = {}
    missing: list[str] = []
    for sym in wanted:
        hit = _snapshot_cache.get(sym)
        if hit and (now - hit[0]) < _SNAPSHOT_TTL:
            out[sym] = hit[1]
        else:
            missing.append(sym)
    if missing:
        for sym, q in _yf_batch_quote(missing).items():
            snap = _snapshot_from_quote(q)
            if snap is not None:
                _snapshot_cache[sym] = (now, snap)
                out[sym] = snap
    return out


_STATS_TTL = 600.0  # 10 min
_stats_cache: dict[str, tuple[float, dict]] = {}


def _dividend_yield_ratio(info: dict) -> float | None:
    """Yahoo dividend yield as a ratio in (0, 1], or None when Yahoo has none.

    ``trailingAnnualDividendYield`` is a fraction (0.0025 → 0.25%).
    ``dividendYield`` is a *percent* (0.26 → 0.26%, 4.19 → 4.19%) — treating it as a
    ratio shows 26%/419% (owner: GOOG Yield column). Prefer trailing; fall back to
    dividendYield/100, then to ``yield`` (a ratio — the field Yahoo fills for ETFs).
    Zero is Yahoo's "no data" (an LSE ETF returned 0 beside a real distribution), so it
    reads as absent and the screen shows "—", never a 0.00% it doesn't know.
    """
    trailing = _finite_positive(info.get("trailingAnnualDividendYield"))
    if trailing is not None:
        return (trailing / 100.0) if trailing > 1.0 else trailing
    pct = _finite_positive(info.get("dividendYield"))
    if pct is not None:
        return pct / 100.0
    return _finite_positive(info.get("yield"))


def _first_positive(info: dict, *keys: str) -> float | None:
    """The first of ``keys`` Yahoo filled with a real (finite, > 0) number. Yahoo reports 0 for
    a price field it has no data for (an LSE ETF: Open 0, Day range 0 to 0), and a 0 that
    reaches the screen reads as a price of nothing."""
    for key in keys:
        number = _finite_positive(info.get(key))
        if number is not None:
            return number
    return None


def fetch_quote_stats(symbol: str) -> dict:
    """Real per-quote stats (prev close, open, day/52w range, market cap, P/E, EPS, div
    yield, volume) from Yahoo (yfinance .info — the same source _fetch_name already uses).
    Cached 10 min. Missing fields are simply absent (caller renders '—'); {} on failure."""
    if not symbol:
        return {}
    hit = _stats_cache.get(symbol)
    if hit and (time.monotonic() - hit[0]) < _STATS_TTL:
        return hit[1]
    stats: dict = {}
    try:
        info = yf.Ticker(symbol).info
        raw = {
            "prev_close": _first_positive(info, "previousClose", "regularMarketPreviousClose"),
            "open": _first_positive(info, "open", "regularMarketOpen"),
            "day_low": _first_positive(info, "dayLow", "regularMarketDayLow"),
            "day_high": _first_positive(info, "dayHigh", "regularMarketDayHigh"),
            "week52_low": _first_positive(info, "fiftyTwoWeekLow"),
            "week52_high": _first_positive(info, "fiftyTwoWeekHigh"),
            "market_cap": _first_positive(info, "marketCap"),
            "volume": _first_positive(info, "volume", "regularMarketVolume"),
            "pe_ratio": _finite_positive(info.get("trailingPE")),
            "forward_pe": _finite_positive(info.get("forwardPE")),
            "peg_ratio": _finite(info.get("pegRatio")),
            "price_to_book": _finite(info.get("priceToBook")),
            "ev_to_ebitda": _finite(info.get("enterpriseToEbitda")),
            "eps": _finite(info.get("trailingEps")),
            "eps_growth": _finite(
                info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")
            ),
            "revenue_growth": _finite(info.get("revenueGrowth")),
            "profit_margin": _finite(info.get("profitMargins")),
            "return_on_equity": _finite(info.get("returnOnEquity")),
            "debt_to_equity": _finite(info.get("debtToEquity")),
            "dividend_yield": _dividend_yield_ratio(info),
        }
        stats = {k: v for k, v in raw.items() if v is not None}
    except Exception:
        _log.warning("prices: quote stats fetch failed %s", symbol, exc_info=True)
        stats = {}
    if stats:
        _stats_cache[symbol] = (time.monotonic(), stats)
    return stats


_ANALYST_TTL = 21600.0  # 6h — analyst consensus / targets move slowly
_analyst_cache: dict[str, tuple[float, dict]] = {}
_ratings_cache: dict[str, tuple[float, list]] = {}

# Yahoo recommendationKey → display label. Unknown keys are title-cased as a fallback.
_RATING_LABELS = {
    "strong_buy": "Strong Buy",
    "buy": "Buy",
    "hold": "Hold",
    "underperform": "Underperform",
    "sell": "Sell",
    "strong_sell": "Strong Sell",
}


def _analyst_distribution(tk) -> dict | None:
    """Latest-period strongBuy/buy/hold/sell/strongSell counts from yfinance ``.recommendations``
    (a small DataFrame; row 0 is the current month). Returns None if unavailable / all-zero."""
    try:
        rec = tk.recommendations
        if rec is None or len(rec) == 0:
            return None
        row = rec.iloc[0]
        dist = {
            "strong_buy": int(row.get("strongBuy", 0) or 0),
            "buy": int(row.get("buy", 0) or 0),
            "hold": int(row.get("hold", 0) or 0),
            "sell": int(row.get("sell", 0) or 0),
            "strong_sell": int(row.get("strongSell", 0) or 0),
        }
        return dist if sum(dist.values()) > 0 else None
    except Exception:
        return None


def fetch_analyst(symbol: str) -> dict:
    """Analyst consensus + 52-week price targets from Yahoo (yfinance ``.info`` +
    ``.recommendations`` — the same free source as ``fetch_quote_stats``, no LLM tokens).

    Returns ``{}`` when the symbol has no analyst coverage (crypto, most commodities, thinly
    covered listings) so the caller hides the widget. Cached 6h since these figures move slowly.
    """
    if not symbol:
        return {}
    hit = _analyst_cache.get(symbol)
    if hit and (time.monotonic() - hit[0]) < _ANALYST_TTL:
        return hit[1]
    out: dict = {}
    try:
        tk = yf.Ticker(symbol)
        info = tk.info
        mean = info.get("targetMeanPrice")
        count = info.get("numberOfAnalystOpinions")
        key = info.get("recommendationKey")
        # "Covered" = at least one analyst opinion AND either a price target or a rating.
        if count and (mean or key):
            current = info.get("currentPrice") or info.get("regularMarketPrice")
            raw: dict = {
                "rating_key": key,
                "rating_label": _RATING_LABELS.get(key or "")
                or ((key or "").replace("_", " ").title() or None),
                "rating_mean": info.get("recommendationMean"),
                "analyst_count": count,
                "target_low": info.get("targetLowPrice"),
                "target_mean": mean,
                "target_high": info.get("targetHighPrice"),
                "current": current,
            }
            if mean and current:
                raw["upside_pct"] = round((mean / current - 1) * 100, 2)
            dist = _analyst_distribution(tk)
            if dist:
                raw["distribution"] = dist
            out = {k: v for k, v in raw.items() if v is not None}
    except Exception:
        _log.warning("prices: analyst fetch failed %s", symbol, exc_info=True)
        out = {}
    if out:
        _analyst_cache[symbol] = (time.monotonic(), out)
    return out


def _pos_num(v) -> float | None:
    """A positive float, or None for missing / NaN / non-positive — filters Yahoo's 0/NaN
    price-target cells (e.g. a fresh 'Initiated' rating has no prior target)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if (f == f and f > 0) else None  # f == f is False for NaN


def fetch_analyst_ratings(symbol: str, limit: int = 40, max_age_days: int = 730) -> list[dict]:
    """Recent per-firm rating actions + price targets from Yahoo (yfinance
    ``.upgrades_downgrades``), most-recent first. Each row:
    ``{date, firm, rating, action, target, prior_target}``.

    Rows older than ``max_age_days`` (~2y) are dropped: Yahoo's firm feed is sparse for many
    non-US listings (e.g. ONWAI.DE has a single 2021 entry), and a lone stale rating reads as
    misleadingly current — better to show nothing and let the table hide. Yahoo exposes the
    FIRM but NOT the individual analyst's name or a source link (those need a paid provider).
    Returns ``[]`` when unavailable (no coverage / crypto). Cached 6h alongside the consensus."""
    if not symbol:
        return []
    hit = _ratings_cache.get(symbol)
    if hit and (time.monotonic() - hit[0]) < _ANALYST_TTL:
        return hit[1]
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=max_age_days)).isoformat()
    rows: list[dict] = []
    try:
        ud = yf.Ticker(symbol).upgrades_downgrades
        if ud is not None and len(ud):
            for ts, r in ud.sort_index(ascending=False).head(limit).iterrows():
                firm = str(r.get("Firm") or "").strip()
                if not firm:
                    continue
                dt = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
                if dt < cutoff:
                    continue
                rows.append(
                    {
                        "date": dt,
                        "firm": firm,
                        "rating": str(r.get("ToGrade") or "").strip() or None,
                        "action": str(r.get("priceTargetAction") or r.get("Action") or "").strip()
                        or None,
                        "target": _pos_num(r.get("currentPriceTarget")),
                        "prior_target": _pos_num(r.get("priorPriceTarget")),
                    }
                )
    except Exception:
        _log.warning("prices: analyst ratings fetch failed %s", symbol, exc_info=True)
        rows = []
    if rows:
        _ratings_cache[symbol] = (time.monotonic(), rows)
    return rows


_symbol_search_cache: dict[str, dict] = {}


def search_symbol(query: str) -> dict:
    """Resolve a company/peer NAME to its Yahoo ticker via Yahoo's search endpoint.

    Returns {symbol, name, exchange} for the best EQUITY/ETF match, or {}. Cached per query.
    Lets us price peers the LLM can name but can't reliably ticker (e.g. 'DoorDash' -> DASH,
    'Just Eat Takeaway' -> TKWY.AS)."""
    q = (query or "").strip()
    if not q:
        return {}
    if q in _symbol_search_cache:
        return _symbol_search_cache[q]
    result: dict = {}
    try:
        url = (
            "https://query2.finance.yahoo.com/v1/finance/search?q="
            + urllib.parse.quote(q)
            + "&quotesCount=6&newsCount=0"
        )
        from yfinance import data as _ydata

        # yfinance's managed session (certifi bundle + browser TLS fingerprint), the same
        # transport as every other Yahoo call here — a bare urllib request dies with
        # CERTIFICATE_VERIFY_FAILED on a Python with an empty trust store and is the first
        # thing Yahoo rate-limits by fingerprint.
        data = _response_json(_ydata.YfData().get(url, timeout=10))
        quotes = [x for x in (data.get("quotes") or []) if x.get("symbol")]
        # Only a genuinely tradeable equity/ETF is a valid peer. Yahoo search also returns
        # private "Company Level" placeholders (quoteType=MUTUALFUND, e.g. "Axiom Space" ->
        # AXISZZX), plus indices/futures/currencies — none are real peers and AXISZZX has no
        # quote, so it priced as NaN. A loose quotes[0] fallback used to accept that junk; now
        # no EQUITY/ETF match -> {} -> the caller drops the peer (matches the docstring).
        pick = next((x for x in quotes if x.get("quoteType") in ("EQUITY", "ETF")), None)
        if pick:
            result = {
                "symbol": pick["symbol"],
                "name": pick.get("shortname") or pick.get("longname") or "",
                "exchange": pick.get("exchDisp") or pick.get("exchange") or "",
            }
    except Exception as exc:
        _log.warning("prices: symbol search failed for %r: %s: %s", q, type(exc).__name__, exc)
        result = {}
    _symbol_search_cache[q] = result
    return result


_coin_search_cache: dict[str, dict] = {}


def search_coin(query: str) -> dict:
    """Resolve a crypto NAME/symbol to a CoinGecko coin via /search.

    Returns {id, symbol, name} for the best match, or {}. Cached per query. Used to price
    crypto PEERS — Yahoo's equity search mis-resolves coin names to ETPs/trusts (e.g.
    'Ethereum' -> Grayscale ETHE on NYSEArca, 'Ripple' -> a Valour XRP ETP in SEK), so a
    crypto asset must resolve its peers through CoinGecko, not Yahoo."""
    q = (query or "").strip()
    if not q:
        return {}
    if q in _coin_search_cache:
        return _coin_search_cache[q]
    result: dict = {}
    try:
        data = coingecko_client.get_json("/search", {"query": q}, timeout=10)
        coins = data.get("coins") or []
        # Prefer a ranked (established) coin; CoinGecko already sorts by relevance + market cap.
        pick = next((c for c in coins if c.get("market_cap_rank")), None) or (
            coins[0] if coins else None
        )
        if pick and pick.get("id"):
            result = {
                "id": pick["id"],
                "symbol": (pick.get("symbol") or "").upper(),
                "name": pick.get("name") or "",
            }
    except Exception:
        _log.debug("prices: coin search failed for %r", q, exc_info=True)
        result = {}
    _coin_search_cache[q] = result
    return result


# ── Market symbol search (ranked multi-provider list) ────────────────────────

_MARKET_SEARCH_TTL = 60.0  # seconds
_market_search_cache: dict[tuple, tuple[float, list]] = {}  # (q, limit) → (fetched_at, results)


def _yahoo_equity_candidates(q: str, n: int) -> list[dict]:
    """Return up to n EQUITY/ETF candidates from Yahoo search for query q.

    Never raises — returns [] on any error.
    """
    try:
        url = (
            "https://query2.finance.yahoo.com/v1/finance/search?q="
            + urllib.parse.quote(q)
            + "&quotesCount="
            + str(max(n, 6))
            + "&newsCount=0"
        )
        from yfinance import data as _ydata

        # Same transport as search_symbol above — see the note there.
        data = _response_json(_ydata.YfData().get(url, timeout=10))
        quotes = data.get("quotes") or []
        results = []
        for x in quotes:
            if x.get("quoteType") not in ("EQUITY", "ETF"):
                continue
            sym = (x.get("symbol") or "").upper()
            if not sym:
                continue
            results.append(
                {
                    "symbol": sym,
                    "name": x.get("shortname") or x.get("longname") or sym,
                    "asset_class": "equity",
                    "exchange": x.get("exchDisp") or x.get("exchange") or "",
                    "price_source": "yahoo",
                    "price_source_id": sym,
                }
            )
        return results
    except Exception as exc:
        _log.warning(
            "prices: yahoo equity candidate search failed for %r: %s: %s",
            q,
            type(exc).__name__,
            exc,
        )
        return []


def _coingecko_candidates(q: str, n: int) -> list[dict]:
    """Return up to n crypto candidates from CoinGecko /search, sorted by market cap rank.

    Never raises — returns [] on any error.
    """
    try:
        data = coingecko_client.get_json("/search", {"query": q}, timeout=10)
        coins = data.get("coins") or []
        coins.sort(
            key=lambda c: c.get("market_cap_rank") if c.get("market_cap_rank") is not None else 1e9
        )
        results = []
        for c in coins[:n]:
            coin_id = c.get("id") or ""
            if not coin_id:
                continue
            results.append(
                {
                    "symbol": (c.get("symbol") or "").upper(),
                    "name": c.get("name") or "",
                    "asset_class": "crypto",
                    "exchange": "",
                    "price_source": "coingecko",
                    "price_source_id": coin_id,
                }
            )
        return results
    except Exception:
        _log.debug("prices: coingecko candidate search failed for %r", q, exc_info=True)
        return []


def _metal_candidates(q: str) -> list[dict]:
    """Match query against metal keys/names; return commodity candidates (no HTTP call).

    Deduplicates by the Yahoo futures symbol so aliases like gold/xau yield one entry.
    """
    from backend.services.instruments import _METAL_SYMBOLS

    q_low = (q or "").strip().lower()
    if not q_low:
        return []
    seen: set[str] = set()
    results: list[dict] = []
    for key, (futures_sym, display_name_str) in _METAL_SYMBOLS.items():
        if futures_sym in seen:
            continue
        # Match key itself or the display name (lowercased, partial)
        if q_low not in key and q_low not in display_name_str.lower():
            continue
        seen.add(futures_sym)
        friendly_sym = display_name_str.split()[0].upper()  # e.g. "Gold (spot)" → "GOLD"
        results.append(
            {
                "symbol": friendly_sym,
                "name": display_name_str,
                "asset_class": "commodity",
                "exchange": "Metal",
                "price_source": "yahoo",
                "price_source_id": futures_sym,
            }
        )
    return results


def _match_rank(q: str, candidate: dict) -> int:
    """Lower = better match. Stable secondary ordering by provider (caller preserves list order)."""
    q_low = q.lower()
    sym = (candidate.get("symbol") or "").lower()
    name = (candidate.get("name") or "").lower()
    if sym == q_low or name == q_low:
        return 0
    if sym.startswith(q_low):
        return 1
    if name.startswith(q_low):
        return 2
    if q_low in sym or q_low in name:
        return 3
    return 4


def search_market(query: str, limit: int = 8) -> list[dict]:
    """Return a ranked list of market symbol candidates across equities, crypto, and metals.

    Each entry has: symbol, name, asset_class, exchange, price_source, price_source_id.
    Never raises. Returns [] for empty query or no matches. TTL-cached 60s.
    """
    q = (query or "").strip()
    if not q:
        return []
    cache_key = (q.lower(), limit)
    now = time.monotonic()
    cached = _market_search_cache.get(cache_key)
    if cached and now - cached[0] < _MARKET_SEARCH_TTL:
        return list(cached[1])

    try:
        eq = _yahoo_equity_candidates(q, limit)
    except Exception:
        eq = []
    try:
        crypto = _coingecko_candidates(q, limit)
    except Exception:
        crypto = []
    try:
        metal = _metal_candidates(q)
    except Exception:
        metal = []

    merged: list[dict] = []
    seen_keys: set[tuple] = set()
    for candidate in eq + crypto + metal:
        key = (candidate["price_source"], candidate["price_source_id"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append(candidate)

    ranked = sorted(merged, key=lambda c: _match_rank(q, c))
    result = ranked[:limit]
    _market_search_cache[cache_key] = (now, result)
    return list(result)


# ── Yahoo predefined screeners (free structured movers) ───────────────────────────
# These are server-side lists (no user query), so they never touch our SSRF surface —
# we reuse yfinance's managed crumb/cookie session (same as _yf_batch_quote) to hit
# Yahoo's screener endpoint. Any error returns {} / [] — never raises.
_MOVERS_TTL = 300.0  # 5 min — top-movers refresh once per bar; no need to hammer
_movers_cache: dict[str, tuple[float, list]] = {}  # scr_id → (fetched_at, rows)

_SCREENER_IDS = frozenset({"day_gainers", "day_losers", "most_actives"})


def _parse_screener_row(q: dict) -> dict | None:
    """Shape one Yahoo screener quote into {symbol, name, change_pct, price}. None if unusable."""
    sym = (q.get("symbol") or "").strip()
    if not sym:
        return None
    price = _finite(q.get("regularMarketPrice"))
    change_pct = _finite(q.get("regularMarketChangePercent"))
    if price is None:
        return None
    return {
        "symbol": sym,
        "name": q.get("shortName") or q.get("longName") or sym,
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "price": _round_price(price),
    }


def fetch_movers(
    scr_ids: tuple[str, ...] = ("day_gainers", "day_losers", "most_actives"),
    count: int = 10,
) -> dict[str, list[dict]]:
    """Fetch Yahoo predefined screener results (free structured movers).

    Returns {scr_id: [{symbol, name, change_pct, price}, ...]} for each requested screener.
    Unknown screener ids are silently skipped. Any HTTP/parse error returns [] for that
    screener — never raises. Cached 5 min per screener.
    """
    out: dict[str, list[dict]] = {}
    now = time.monotonic()
    for scr in scr_ids:
        if scr not in _SCREENER_IDS:
            continue
        cached = _movers_cache.get(scr)
        if cached and now - cached[0] < _MOVERS_TTL:
            out[scr] = cached[1]
            continue
        rows: list[dict] = []
        try:
            from yfinance import data as _ydata

            yfd = _ydata.YfData()
            crumb = yfd._get_crumb_basic()
            if not crumb:
                out[scr] = rows
                continue
            url = (
                "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
                "?formatted=false&scrIds="
                + urllib.parse.quote(scr)
                + "&count="
                + str(max(1, count))
                + "&crumb="
                + urllib.parse.quote(crumb)
            )
            data = _response_json(yfd.get(url))
            quotes = (data.get("finance") or {}).get("result", [{}])[0].get("quotes", [])
            for q in quotes[:count]:
                row = _parse_screener_row(q)
                if row:
                    rows.append(row)
        except Exception as exc:
            _log.info("prices: screener fetch failed (%s: %s)", scr, type(exc).__name__)
        _movers_cache[scr] = (now, rows)
        out[scr] = rows
    return out


def resolve_watchlist_symbol(query: str) -> dict | None:
    """Validate + canonicalise a user-typed ticker/name for the watchlist.

    Tries an equity/ETF match first (Yahoo search — also fixes loose input like
    'ADOBE' -> ADBE, 'DoorDash' -> DASH), then a crypto match (CoinGecko). Returns
    {value, label, asset_class} with the canonical symbol, or None when nothing
    real matches (so the caller can reject the add with an error).
    """
    q = (query or "").strip()
    if not q:
        return None
    eq = search_symbol(q)
    if eq.get("symbol"):
        return {
            "value": eq["symbol"].upper(),
            "label": eq.get("name") or eq["symbol"],
            "asset_class": "equity",
        }
    coin = search_coin(q)
    if coin.get("symbol"):
        return {
            "value": coin["symbol"].upper(),
            "label": coin.get("name") or coin["symbol"],
            "asset_class": "crypto",
        }
    return None


# An FX quote and its reciprocal must multiply to 1. Tolerance is generous (±2%) because the
# two legs are separate quotes with their own bid/ask and timestamps — this is a scale check,
# not a precision check. Anything outside it is a magnitude error, not market drift.
_FX_RECIPROCAL_TOLERANCE = 0.02


def _reconcile_fx_rate(
    currency: str, direct: float | None, inverse: float | None, previous: float | None = None
) -> float | None:
    """Validate a fetched FX rate against its own reciprocal, and prefer the trustworthy leg.

    Observed data-quality defect (2026-07-27). Yahoo's ``VNDSGD=X`` returned **0.048984** —
    implying 1 SGD = 20.4 VND, when the true rate is ~20,369. The reciprocal quote
    ``SGDVND=X`` was correct at 20,369, so the pair multiplied to 997.7 instead of 1.0: Yahoo
    was quoting the inverse leg scaled by 1000 (as if VND were priced per 1,000 units). Stored
    verbatim, that inflates every VND-denominated figure in the app by ~1000x — a S$5.0M
    phantom net-worth swing on a representative test book, and a commitment reporting a S$20M
    shortfall. ``IDRSGD=X`` was observed wrong the same way (~100x), so this is not
    VND-specific: it is a systematic defect in Yahoo's low-value-currency inverse pairs.

    The check is a mathematical identity, not a heuristic — ``direct × inverse == 1`` holds for
    every correct currency pair, so it cannot produce a false positive on a real quote. When it
    fails we take ``1 / inverse``: the conventional quote direction (USD/VND, SGD/VND) is the
    liquid, correctly-scaled one, and it is the leg that was right in every observed case.

    Returns None to leave the cached rate untouched rather than write a number we don't trust —
    a stale rate is materially better than a 1000x-wrong one.
    """
    if direct is not None and direct > 0 and inverse is not None and inverse > 0:
        product = direct * inverse
        if abs(product - 1.0) <= _FX_RECIPROCAL_TOLERANCE:
            return direct
        corrected = 1.0 / inverse
        _log.error(
            "prices: FX sanity check FAILED for %s — %s=%.10g and its reciprocal=%.10g multiply "
            "to %.4g (expected 1.0). Using 1/reciprocal=%.10g instead of the quoted %.10g "
            "(off by ~%.0fx).",
            currency,
            currency,
            direct,
            inverse,
            product,
            corrected,
            direct,
            product,
        )
        return corrected
    if direct is not None and direct > 0:
        # No reciprocal to check against. Accept it, but refuse a silent order-of-magnitude
        # jump away from the rate we already trust — that is the shape this incident took.
        if previous and previous > 0:
            ratio = direct / previous
            if ratio > 10 or ratio < 0.1:
                _log.error(
                    "prices: REFUSING FX update for %s — quoted %.10g is %.1fx the cached %.10g; "
                    "keeping the cached rate. No reciprocal quote was available to arbitrate.",
                    currency,
                    direct,
                    ratio,
                    previous,
                )
                return None
        return direct
    if inverse is not None and inverse > 0:
        return 1.0 / inverse  # direct leg missing entirely — the reciprocal is all we have
    return None


# Single-flight: one in-flight network fetch per cache key. Without this, a tab that fires
# portfolio + analytics + brief + returns at once had each independently call fetch_live and
# hit Yahoo/CoinGecko simultaneously — N redundant multi-second round-trips (+ provider rate
# limits) for ONE user, which read as "the app is slow" but is pure I/O, not computation. Now
# the first caller fetches and the rest wait on its lock, then reuse the just-cached result.
_fetch_locks: dict[str, threading.Lock] = {}
_fetch_locks_guard = threading.Lock()


def _fetch_lock_for(cache_key: str) -> threading.Lock:
    with _fetch_locks_guard:
        lock = _fetch_locks.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _fetch_locks[cache_key] = lock
        return lock


def _fresh_cached(cache_key: str) -> dict | None:
    cached_at, cached_result = _price_cache.get(cache_key, (0.0, {}))
    if cached_result and (time.monotonic() - cached_at) < _PRICE_CACHE_TTL:
        _log.info("prices: cache hit (%.0fs old)", time.monotonic() - cached_at)
        return cached_result
    return None


# When a provider throttles (usage-based: fine at noon, 429s/timeouts at night), one TTL-lapsed
# fetch held its lock through a 35s round-trip and EVERY money-bearing request queued behind it
# (measured 2026-08-12 00:09: a 35.3s fetch = a 35.7s /api/portfolio + 28-30s stalls on
# unrelated endpoints). Interactive callers must therefore never wait on a provider: they pass
# ``allow_stale=True``, get the last-known result immediately, and the refresh runs in ONE
# background thread per cache key. Background consumers (scheduler tick, price monitor, the AI
# tool) keep blocking-fresh semantics.
_STALE_HARD_LIMIT = 900.0  # seconds — beyond this, even interactive callers block for a fetch


def _refresh_in_background(
    cache_key: str,
    tickers: list[str],
    currencies: list[str],
    include_names: bool,
    *,
    record_history: bool = True,
) -> None:
    """`record_history` (M4 fix, security-review 2026-08-29) is threaded straight through from
    the `fetch_live` call that triggered this refresh — it used to be hardcoded True here on the
    (once-true) assumption that "the caller that accepted stale data is a portfolio/asset view,
    never a chat question". That assumption broke the moment a genuinely read-only caller
    (`get_asset_context`'s `record_price_history=False` -> `read_asset_detail` ->
    `fetch_live(..., allow_stale=True, record_history=False)`) started using `allow_stale=True`
    too: on any cache entry aged between the fast TTL and `_STALE_HARD_LIMIT` — the common case
    a minute or two after any page load — this thread ran with the hardcoded default and wrote a
    price_history row anyway, silently defeating the caller's own `record_history=False`."""
    lock = _fetch_lock_for(cache_key)
    if not lock.acquire(blocking=False):
        return  # a refresh for this key is already in flight

    def _run():
        try:
            result = _fetch_live_uncached(
                tickers, currencies, include_names, record_history=record_history
            )
            _price_cache[cache_key] = (time.monotonic(), result)
        except Exception:
            _log.warning("prices: background refresh failed for %s", cache_key, exc_info=True)
        finally:
            lock.release()

    threading.Thread(target=_run, name="prices-bg-refresh", daemon=True).start()


def fetch_live(
    tickers: list[str],
    currencies: list[str],
    include_names: bool = False,
    *,
    allow_stale: bool = False,
    record_history: bool = True,
) -> dict:
    """Live prices + FX for a holding set, with a 60s cache AND single-flight. Concurrent
    callers for the same set share ONE network fetch (see `_fetch_locks`), so a tab-load no
    longer stampedes the provider with N identical round-trips.

    ``record_history=False`` for a caller that is only ASKING what a price is (today: the
    ``get_live_prices`` AI tool). The series is the portfolio's record of ITSELF, accumulated
    by the paths that value holdings; a chat question is not an event in it.

    It is deliberately NOT part of the cache key. A recording caller and a non-recording one
    share the 60s entry, so a chat question can consume the one fetch a portfolio load would
    otherwise have made and that cycle records nothing. That is harmless because the series
    is one point per TRADING DAY (``record_many(..., days=trading_days)``), not per minute —
    any later valuing fetch the same day fills it. Keying the cache on the flag instead would
    double the provider round-trips to protect a point that arrives anyway.

    ``allow_stale=True`` (interactive request paths ONLY): a TTL-lapsed entry is returned
    immediately and refreshed in the background — a user's tab never waits on Yahoo/CoinGecko.
    Falls back to a blocking fetch when nothing cached yet or the entry is >15 min old. That
    background refresh honors THIS call's own ``record_history`` (M4 fix, security-review
    2026-08-29 — see ``_refresh_in_background``'s docstring): a non-recording caller taking the
    stale-then-background-refresh path never leaves a price_history row behind, matching the
    blocking-fetch path below exactly."""
    cache_key = (
        ",".join(sorted(tickers)) + "|" + ",".join(sorted(currencies)) + "|" + str(include_names)
    )
    fresh = _fresh_cached(cache_key)
    if fresh is not None:
        return fresh
    if allow_stale:
        stale_at, stale = _price_cache.get(cache_key, (0.0, {}))
        age = time.monotonic() - stale_at
        if stale and age < _STALE_HARD_LIMIT:
            _log.info("prices: serving %.0fs-old prices; refreshing in background", age)
            _refresh_in_background(
                cache_key, tickers, currencies, include_names, record_history=record_history
            )
            return stale
    with _fetch_lock_for(cache_key):
        # A concurrent leader may have filled the cache while we waited for the lock.
        fresh = _fresh_cached(cache_key)
        if fresh is not None:
            return fresh
        result = _fetch_live_uncached(
            tickers, currencies, include_names, record_history=record_history
        )
        _price_cache[cache_key] = (time.monotonic(), result)
        return result


def _fetch_live_uncached(
    tickers: list[str],
    currencies: list[str],
    include_names: bool = False,
    *,
    record_history: bool = True,
) -> dict:
    # One telemetry write per fetch, not one per event (see `observability.batched`).
    with observability.batched():
        return _fetch_live_provider_round(
            tickers, currencies, include_names, record_history=record_history
        )


def _fetch_live_provider_round(
    tickers: list[str],
    currencies: list[str],
    include_names: bool = False,
    *,
    record_history: bool = True,
) -> dict:
    crypto_routes, yf_tickers = _crypto_routing_with_currencies(tickers)
    crypto_pairs = [(request_key, canonical_id) for request_key, canonical_id, _ in crypto_routes]
    canonical_by_request = dict(crypto_pairs)
    crypto_currency = {
        request_key: quote_currency for request_key, _, quote_currency in crypto_routes
    }
    from backend.db.market_data_config_store import MarketDataConfigStore
    from backend.services.crypto_market_data import (
        record_crypto_quote,
        split_major_crypto_pairs,
        stale_crypto_quote,
    )
    from backend.services.market_data_settings import (
        acquire_yahoo_quote,
        record_yahoo_failure,
        record_yahoo_success,
    )

    yahoo_primary = MarketDataConfigStore().runtime()["yahoo_primary_for_major_crypto"]
    usd_pairs = [
        (request_key, canonical_id)
        for request_key, canonical_id, quote_currency in crypto_routes
        if quote_currency == "USD"
    ]
    non_usd_pairs = [
        (request_key, canonical_id)
        for request_key, canonical_id, quote_currency in crypto_routes
        if quote_currency != "USD"
    ]
    major_pairs, usd_coin_gecko_only = split_major_crypto_pairs(usd_pairs)
    coin_gecko_only = usd_coin_gecko_only + non_usd_pairs
    qa_enabled = get_settings().onw_qa_mode

    base = _base()
    fx_map = {c: f"{c}{base}=X" for c in currencies if c and c != base}
    # Fetch the RECIPROCAL pair too, purely to validate — see `_reconcile_fx_rate`.
    fx_inverse_map = {c: f"{base}{c}=X" for c in fx_map}
    yahoo_reservation = acquire_yahoo_quote() if major_pairs and yahoo_primary else None
    yahoo_quote_allowed = bool(yahoo_reservation and yahoo_reservation["allowed"])
    yahoo_primary_pairs = major_pairs if yahoo_primary and yahoo_quote_allowed else []
    yahoo_crypto_attempted = bool(yahoo_primary_pairs)
    yf_symbols = list(
        dict.fromkeys(
            [t for t in yf_tickers if t]
            + [alias for _, alias in yahoo_primary_pairs]
            + list(fx_map.values())
            + list(fx_inverse_map.values())
        )
    )

    _log.info(
        "prices: fetching %d yfinance + %d crypto + %d FX",
        len(yf_symbols),
        len(crypto_pairs),
        len(fx_map),
    )
    t0 = time.monotonic()
    initial_yahoo_started: float | None = None
    if yahoo_crypto_attempted:
        initial_yahoo_started = time.monotonic()
        observability.emit("provider_request", provider="yahoo", timeframe="QUOTE")

    # One batched v7 quote covers all yfinance symbols + FX in a single request and also
    # yields market state for free (cached for market_status()). Whatever it misses — or
    # everything, if the batch fails — falls back to the per-ticker fast_info path below,
    # so a batch outage degrades to today's behaviour rather than dropping prices.
    batch = {} if qa_enabled or not yf_symbols else _yf_batch_quote(yf_symbols)
    if batch:
        _cache_market_from_quotes(batch)
    raw_yf: dict[str, tuple[str, float, float | None, str | None]] = {}
    extended: dict[str, dict] = {}
    last_post: dict[str, dict] = {}
    yahoo_crypto_aliases = {alias for _, alias in yahoo_primary_pairs}
    for sym in yf_symbols:
        q = batch.get(sym)
        if q is not None:
            pr, pv = _quote_to_price(q)
            is_crypto_alias = sym in yahoo_crypto_aliases
            gated_state = None
            if not is_crypto_alias:
                cached_market = _market_cache.get(sym)
                gated_state = cached_market[1].get("state") if cached_market else None
            # Crypto's own freshness bar is untouched (that `observed_at`-required check
            # below is an already-tested, unrelated gate). An equity's regularMarketTime IS
            # when its price last printed, whatever the state — including REGULAR: in a
            # delayed venue's first minutes (XETRA, 15m) the state is already REGULAR while
            # the price is still yesterday's close, and stamping it "now" read as if today's
            # open had printed (owner-reproduced 2026-09-23, KSTL.DE at 09:05 CEST).
            max_age = 6.0 if is_crypto_alias else 24 * 7.0
            observed_at = _as_of(q.get("regularMarketTime"), max_age_hours=max_age)
            if pr is not None and (not is_crypto_alias or observed_at):
                raw_yf[sym] = (sym, pr, pv, observed_at)
            if not is_crypto_alias:
                ext = _extended_hours_from_quote(q, gated_state)
                if ext:
                    extended[sym] = ext
                tz_name = cached_market[1].get("timezone") if cached_market else None
                post = last_session_post_print(q, tz_name)
                if post:
                    last_post[sym] = post
    missing = [s for s in yf_symbols if s not in raw_yf]

    if qa_enabled and yahoo_primary_pairs:
        from backend.services import market_data_qa

        for request_key, alias in yahoo_primary_pairs:
            canonical_id = next(cid for key, cid in crypto_pairs if key == request_key)
            quote = market_data_qa.quote("yahoo", canonical_id)
            price = _finite_positive(quote.get("price")) if quote else None
            previous_raw = quote.get("previous_close") if quote else None
            previous = _finite_positive(previous_raw) if previous_raw is not None else None
            observed_at = quote.get("as_of") if quote else None
            if (
                price is not None
                and (previous_raw is None or previous is not None)
                and observed_at is not None
            ):
                raw_yf[alias] = (alias, _round_price(price), previous, observed_at)
        missing = [
            s
            for s in yf_symbols
            if s not in raw_yf and s not in {alias for _, alias in yahoo_primary_pairs}
        ]

    yahoo_crypto_prices: dict[str, tuple[str, float, float | None, str | None]] = {}
    for request_key, alias in yahoo_primary_pairs:
        _, price, previous, observed_at = raw_yf.get(alias, (alias, None, None, None))
        if price is not None and observed_at is not None:
            yahoo_crypto_prices[request_key] = (alias, price, previous, observed_at)

    if initial_yahoo_started is not None:
        yahoo_latency = (time.monotonic() - initial_yahoo_started) * 1000
        for request_key, _alias in major_pairs:
            canonical_id = canonical_by_request[request_key]
            if request_key in yahoo_crypto_prices:
                observability.emit(
                    "provider_success",
                    provider="yahoo",
                    canonical_id=canonical_id,
                    timeframe="QUOTE",
                    latency_ms=yahoo_latency,
                )
            else:
                observability.emit(
                    "validation_rejected",
                    provider="yahoo",
                    canonical_id=canonical_id,
                    timeframe="QUOTE",
                    reason="empty_or_invalid_quote",
                )
                observability.emit(
                    "provider_failure",
                    provider="yahoo",
                    canonical_id=canonical_id,
                    timeframe="QUOTE",
                    reason="empty_or_invalid_quote",
                    latency_ms=yahoo_latency,
                )

    crypto_misses = (
        coin_gecko_only
        + [
            (request_key, canonical_id)
            for request_key, canonical_id in crypto_pairs
            if request_key not in yahoo_crypto_prices
            and (request_key, canonical_id) not in coin_gecko_only
        ]
        if yahoo_primary
        else list(crypto_pairs)
    )
    crypto_prices: dict = {}
    crypto_prev: dict = {}
    crypto_as_of: dict[str, str | None] = {}
    if yahoo_primary:
        for request_key, _alias in major_pairs:
            canonical_id = canonical_by_request[request_key]
            if request_key not in yahoo_crypto_prices:
                observability.emit(
                    "fallback",
                    provider="coingecko",
                    canonical_id=canonical_id,
                    timeframe="QUOTE",
                )
    if crypto_misses:
        if qa_enabled:
            from backend.services import market_data_qa

            for request_key, canonical_id in crypto_misses:
                quote = market_data_qa.quote(
                    "coingecko", canonical_id, crypto_currency[request_key]
                )
                price = _finite_positive(quote.get("price")) if quote else None
                previous_raw = quote.get("previous_close") if quote else None
                previous = _finite_positive(previous_raw) if previous_raw is not None else None
                observed_at = quote.get("as_of") if quote else None
                if (
                    price is not None
                    and (previous_raw is None or previous is not None)
                    and observed_at is not None
                ):
                    crypto_prices[request_key] = _round_price(price)
                    if previous is not None:
                        crypto_prev[request_key] = _round_price(previous)
                    crypto_as_of[request_key] = observed_at
        else:
            requested_currencies = {crypto_currency[key] for key, _ in crypto_misses}
            fetched = (
                _fetch_coingecko_by_id(crypto_misses)
                if requested_currencies == {"USD"}
                else _fetch_coingecko_by_id(crypto_misses, crypto_currency)
            )
            crypto_prices.update(fetched[0])
            crypto_prev.update(fetched[1])
            crypto_as_of.update(fetched[2])

        # A price without a fresh provider timestamp is not a live crypto observation.
        for request_key in set(crypto_prices) - set(crypto_as_of):
            crypto_prices.pop(request_key, None)
            crypto_prev.pop(request_key, None)

    if not yahoo_primary:
        yahoo_fallback = [
            (request_key, alias)
            for request_key, alias in major_pairs
            if request_key not in crypto_prices
        ]
        if yahoo_fallback:
            fallback_reservation = yahoo_reservation or acquire_yahoo_quote()
            if not fallback_reservation["allowed"]:
                yahoo_fallback = []
            yahoo_crypto_attempted = bool(yahoo_fallback)
        if yahoo_fallback:
            for request_key, _alias in yahoo_fallback:
                canonical_id = next(cid for key, cid in crypto_pairs if key == request_key)
                observability.emit(
                    "fallback",
                    provider="yahoo",
                    canonical_id=canonical_id,
                    timeframe="QUOTE",
                )
            fallback_yahoo_started = time.monotonic()
            observability.emit("provider_request", provider="yahoo", timeframe="QUOTE")
            if qa_enabled:
                from backend.services import market_data_qa

                for request_key, alias in yahoo_fallback:
                    canonical_id = next(cid for key, cid in crypto_pairs if key == request_key)
                    quote = market_data_qa.quote("yahoo", canonical_id)
                    price = _finite_positive(quote.get("price")) if quote else None
                    previous_raw = quote.get("previous_close") if quote else None
                    previous = _finite_positive(previous_raw) if previous_raw is not None else None
                    observed_at = quote.get("as_of") if quote else None
                    if (
                        price is not None
                        and (previous_raw is None or previous is not None)
                        and observed_at is not None
                    ):
                        yahoo_crypto_prices[request_key] = (
                            alias,
                            _round_price(price),
                            _round_price(previous) if previous is not None else None,
                            observed_at,
                        )
            else:
                fallback_batch = _yf_batch_quote([alias for _, alias in yahoo_fallback])
                if fallback_batch:
                    _cache_market_from_quotes(fallback_batch)
                for request_key, alias in yahoo_fallback:
                    quote = fallback_batch.get(alias)
                    if quote is None:
                        continue
                    price, previous = _quote_to_price(quote)
                    observed_at = _as_of(quote.get("regularMarketTime"))
                    if price is not None and observed_at is not None:
                        yahoo_crypto_prices[request_key] = (
                            alias,
                            price,
                            previous,
                            observed_at,
                        )
            fallback_latency = (time.monotonic() - fallback_yahoo_started) * 1000
            for request_key, _alias in yahoo_fallback:
                canonical_id = next(cid for key, cid in crypto_pairs if key == request_key)
                if request_key in yahoo_crypto_prices:
                    observability.emit(
                        "provider_success",
                        provider="yahoo",
                        canonical_id=canonical_id,
                        timeframe="QUOTE",
                        latency_ms=fallback_latency,
                    )
                else:
                    observability.emit(
                        "validation_rejected",
                        provider="yahoo",
                        canonical_id=canonical_id,
                        timeframe="QUOTE",
                        reason="empty_or_invalid_quote",
                    )
                    observability.emit(
                        "provider_failure",
                        provider="yahoo",
                        canonical_id=canonical_id,
                        timeframe="QUOTE",
                        reason="empty_or_invalid_quote",
                        latency_ms=fallback_latency,
                    )

    if yahoo_crypto_attempted:
        if yahoo_crypto_prices:
            record_yahoo_success(
                generation=(yahoo_reservation or fallback_reservation)["generation"]
            )
        else:
            record_yahoo_failure(
                generation=(yahoo_reservation or fallback_reservation)["generation"]
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=14) as ex:
        yf_futures = {
            sym: ex.submit(_fetch_one, sym)
            for sym in missing
            if not qa_enabled and sym not in {alias for _, alias in yahoo_primary_pairs}
        }
    for sym, fut in yf_futures.items():
        symbol, price, previous = fut.result()
        if price is not None and price > 0 and (previous is None or previous > 0):
            raw_yf[sym] = (symbol, price, previous, None)

    prices, prev_prices = {}, {}
    providers: dict[str, str | None] = {}
    states: dict[str, str] = {}
    as_of: dict[str, str | None] = {}
    degraded_reasons: dict[str, str] = {}
    provider_as_of: dict[str, str | None] = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    failed = []
    for t in yf_tickers:
        _, price, prev, observed_at = raw_yf.get(t, (t, None, None, None))
        if price is not None:
            prices[t] = price
            providers[t] = "yahoo"
            states[t] = "live"
            as_of[t] = observed_at or now_iso
        else:
            failed.append(t)
            providers[t] = None
            states[t] = "unavailable"
            as_of[t] = None
        if prev is not None:
            prev_prices[t] = prev

    for request_key, (_, price, previous, observed_at) in yahoo_crypto_prices.items():
        prices[request_key] = price
        if previous is not None:
            prev_prices[request_key] = previous
        providers[request_key] = "yahoo"
        states[request_key] = "live"
        as_of[request_key] = observed_at or now_iso
        provider_as_of[request_key] = observed_at
        canonical_id = next(cid for key, cid in crypto_pairs if key == request_key)
        observability.emit(
            "provider_selected",
            provider="yahoo",
            canonical_id=canonical_id,
            timeframe="QUOTE",
        )
    for request_key, _ in crypto_pairs:
        if request_key in crypto_prices:
            prices[request_key] = crypto_prices[request_key]
            if request_key in crypto_prev:
                prev_prices[request_key] = crypto_prev[request_key]
            providers[request_key] = "coingecko"
            states[request_key] = "live"
            as_of[request_key] = crypto_as_of.get(request_key) or now_iso
            provider_as_of[request_key] = crypto_as_of.get(request_key)
            canonical_id = next(cid for key, cid in crypto_pairs if key == request_key)
            observability.emit(
                "provider_selected",
                provider="coingecko",
                canonical_id=canonical_id,
                timeframe="QUOTE",
            )
        elif request_key not in providers:
            providers[request_key] = None
            states[request_key] = "unavailable"
            as_of[request_key] = None

    for request_key, canonical_id in crypto_pairs:
        quote_currency = crypto_currency[request_key]
        if states.get(request_key) == "live":
            record_crypto_quote(
                canonical_id,
                quote_currency,
                providers[request_key] or "unknown",
                prices[request_key],
                prev_prices.get(request_key),
                provider_as_of.get(request_key),
            )
            continue
        stale = stale_crypto_quote(canonical_id, quote_currency)
        if stale:
            prices[request_key] = stale["price"]
            if stale["previous_close"] is not None:
                prev_prices[request_key] = stale["previous_close"]
            providers[request_key] = stale["provider"]
            states[request_key] = stale["state"]
            as_of[request_key] = stale["as_of"]
            degraded_reasons[request_key] = stale["degraded_reason"]
            stale_provider = stale.get("provider")
            observability.emit(
                "stale_serve",
                provider=stale_provider if stale_provider in {"coingecko", "yahoo"} else None,
                canonical_id=canonical_id,
                timeframe="QUOTE",
                reason="upstream_unavailable",
            )
        else:
            observability.emit(
                "unavailable",
                canonical_id=canonical_id,
                timeframe="QUOTE",
                reason="upstream_unavailable",
            )

    # Each ticker's OWN exchange trading-day date, from the timezone the batch quote above
    # already gave us for free — shared by the prior-close lookup and the record below so
    # both anchor to the same boundary. A ticker with no cached timezone (crypto, a quote
    # miss) is simply absent, and falls back to the store's own UTC-anchored default.
    trading_days = _trading_day_map(yf_tickers)

    try:
        from backend.db.price_history_store import PriceHistoryStore

        # Only while the regular session is ACTIVELY open and ticking (state REGULAR) — the
        # PLTR incident this guards (2026-08-11: a still-open NYSE session's live tick split
        # across two UTC calendar-date rows, so prior_to_map anchored to an intraday snapshot
        # from earlier THAT SAME session instead of yesterday's real close; see
        # test_fetch_live_anchors_prior_close_to_each_tickers_own_exchange_day).
        #
        # When the regular session hasn't opened yet (PRE) or has already closed (POST/CLOSED),
        # regularMarketPrice is simply the frozen close of the LAST completed session — Yahoo's
        # own regularMarketPreviousClose (already in prev_prices via _quote_to_price) is exactly
        # the right comparator then: that session's own close-vs-prior-close move, matching
        # Yahoo's own "At close: <date>" framing (owner report 2026-08-31, comparing against
        # Yahoo's own reference page for a held stock). Applying the trading-day anchor
        # unconditionally collapsed a pre-market Monday's headline to a false 0% (Friday's
        # close compared against itself) instead of Friday's real move vs Thursday.
        prior_map = PriceHistoryStore().prior_to_map(days=trading_days)
        for t in list(prices):
            cached_market = _market_cache.get(t)
            is_regular = bool(cached_market and cached_market[1].get("is_regular"))
            if is_regular and prior_map.get(t) is not None:
                prev_prices[t] = prior_map[t]
    except Exception:
        pass

    fx = dict(load_fx_cache())
    fx[base] = 1.0
    for c, sym in fx_map.items():
        _, rate, _, _ = raw_yf.get(sym, (sym, None, None, None))
        _, inverse, _, _ = raw_yf.get(fx_inverse_map[c], (None, None, None, None))
        rate = _reconcile_fx_rate(c, rate, inverse, previous=fx.get(c))
        if rate is not None:
            fx[c] = rate

    ms = (time.monotonic() - t0) * 1000
    _log.info(
        "prices: got %d prices, %d FX in %.0fms%s",
        len(prices),
        len(fx) - 1,
        ms,
        f" | failed: {failed}" if failed else "",
    )

    if fx:
        save_fx_cache(fx)

    # Extended-preferred "latest known" cache (see its own module docstring above) —
    # unconditional, unlike the day-keyed write below: this doesn't feed the day-P&L series
    # `record_history` gates against, so even a read-only chat question's fresh data is worth
    # sharing app-wide (a genuinely live price is a genuinely live price, whatever asked for it).
    # `resolved` (keyed like `prices`/`extended` — the requested ticker, not a provider id)
    # rides in the return value below: a caller that returned the raw `prices`/`extended` split
    # straight to an LLM (`get_live_prices`) or over the wire (`GET /api/prices`) otherwise has
    # no single field telling it which one is actually current — the same shape of gap that
    # caused the `asset_detail.py` bug this whole pass started from, just facing a different
    # caller (owner ask 2026-09-01: fix this consistently, not just the one call site found).
    now_mono = time.monotonic()
    resolved: dict[str, float] = {}
    for t in yf_tickers:
        if t in prices:
            ext = extended.get(t)
            r = ext["price"] if ext and ext.get("price") is not None else prices[t]
            resolved[t] = r
            _live_price_cache[t] = (r, now_mono)
    for req_key, cg_id in crypto_pairs:
        if req_key in prices and states.get(req_key) == "live":
            resolved[req_key] = prices[req_key]
            _live_price_cache[cg_id] = (prices[req_key], now_mono)

    # ``record_history=False`` for a caller that is only ASKING what a price is — today the
    # ``get_live_prices`` AI tool. A price series is the portfolio's own record of itself,
    # accumulated as the app values holdings; a question about a ticker is not an event in
    # that series, and letting a chat message append to it made a read-shaped tool a writer
    # of checkpoint-included data (owner, 2026-08-23). The direct single-provider tools
    # already worked this way for a related reason — see the module note below.
    if record_history:
        try:
            from backend.db.price_history_store import PriceHistoryStore

            hist_points: dict[str, float] = {}
            for req_key, cg_id in crypto_pairs:
                if req_key in prices and states.get(req_key) == "live":
                    hist_points[cg_id] = prices[req_key]
            for t in yf_tickers:
                if t in prices:
                    hist_points[t] = prices[t]
            if hist_points:
                # Stamp each ticker under ITS OWN exchange trading day (see the prior_to_map
                # call above) — otherwise a still-open session's ticks split across two
                # `date` rows the moment the boundary is crossed mid-session, corrupting the
                # very "one point per trading day" invariant `prior_to_map` relies on.
                PriceHistoryStore().record_many(hist_points, source="live", days=trading_days)
        except Exception:
            pass

    names: dict[str, str] = {}
    exchanges: dict[str, str] = {}
    currencies: dict[str, str] = {}
    if include_names:
        all_name_tickers = [t for t in yf_tickers if t]
        # Refetch when EITHER name or currency is missing — a name-only warm cache must still
        # backfill currency (gated identically in _fetch_name) so the Peers card can label it.
        uncached = [
            t for t in all_name_tickers if t not in _NAMES_CACHE or t not in _CURRENCY_CACHE
        ]
        if uncached:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(uncached), 14)) as ex:
                for sym, name, exchange in ex.map(_fetch_name, uncached):
                    if name:
                        _NAMES_CACHE[sym] = name
                    if exchange:
                        _EXCHANGE_CACHE[sym] = exchange
        for t in all_name_tickers:
            if t in _NAMES_CACHE:
                names[t] = display_name(_NAMES_CACHE[t])
            if t in _EXCHANGE_CACHE:
                exchanges[t] = _EXCHANGE_CACHE[t]
            if _CURRENCY_CACHE.get(t):
                currencies[t] = _CURRENCY_CACHE[t]
        save_names_cache(_NAMES_CACHE)
        save_sectors_cache(_SECTOR_CACHE)
        _backfill_dividend_rates()

    result: dict = {
        "prices": prices,
        "prev_prices": prev_prices,
        "fx": fx,
        "base_currency": base,
        "providers": providers,
        "states": states,
        "as_of": as_of,
        "degraded_reasons": degraded_reasons,
        "extended": extended,
        # The last completed session's after-hours print per symbol, whatever the market state
        # (see `last_session_post_print`) — display history for the movement timeline, never
        # a price input.
        "last_post": last_post,
        # The single "what is it right now" answer per ticker — extended-preferred, the same
        # resolution `resolve_price_quote` applies everywhere a price is shown or valued. Use
        # THIS, not `prices` directly, unless you specifically want the regular-session value.
        "resolved": resolved,
    }
    if include_names:
        result["names"] = names
        result["exchanges"] = exchanges
        result["currencies"] = currencies
    return result  # caching + single-flight are handled by the fetch_live wrapper


# ── Direct single-provider quotes (yahoo_get_ticker_prices / coingecko_get_crypto_prices) ──
#
# `fetch_live` above is the general cross-provider path: it CACHES (60s TTL), records every
# fetched point into price_history (the portfolio's canonical per-instrument series), and
# for crypto silently falls back Yahoo -> CoinGecko (or CoinGecko -> Yahoo, per
# `yahoo_primary_for_major_crypto`). These two are a deliberately narrower surface for "what
# does THIS ONE provider say" — the two direct-provider AI tools below, and any future
# caller with the same need. NEITHER caches NOR records into price_history: an ad-hoc direct
# lookup may use a different provider than the instrument's own configured `price_source`,
# and writing it into that instrument's series would silently splice a foreign provider's
# tick into what is supposed to be one provider's continuous history.


def fetch_yahoo_quotes(tickers: list[str]) -> dict:
    """Direct Yahoo Finance quotes for arbitrary symbols — equities, ETFs, indices, FX pairs
    (e.g. 'EURUSD=X'), and any Yahoo-priceable crypto pair. NO CoinGecko fallback: a symbol
    Yahoo can't price comes back state="unavailable", never silently substituted. See the
    module note above for why this neither caches nor records into price_history."""
    symbols = list(dict.fromkeys(t.strip() for t in tickers if t and t.strip()))
    prices: dict[str, float] = {}
    prev_prices: dict[str, float] = {}
    providers: dict[str, str | None] = {}
    states: dict[str, str] = {}
    as_of: dict[str, str | None] = {}
    if not symbols:
        return {
            "prices": prices,
            "prev_prices": prev_prices,
            "providers": providers,
            "states": states,
            "as_of": as_of,
            "extended": {},
        }

    batch = _yf_batch_quote(symbols)
    if batch:
        _cache_market_from_quotes(batch)
    raw: dict[str, tuple[float, float | None, str | None]] = {}
    extended: dict[str, dict] = {}
    for sym in symbols:
        q = batch.get(sym)
        if q is not None:
            price, prev = _quote_to_price(q)
            cached_market = _market_cache.get(sym)
            gated_state = cached_market[1].get("state") if cached_market else None
            # See fetch_live's same bound — regularMarketTime is when this price printed,
            # in every state (a delayed venue's first REGULAR minutes still carry yesterday's).
            max_age = 24 * 7.0
            if price is not None:
                raw[sym] = (price, prev, _as_of(q.get("regularMarketTime"), max_age_hours=max_age))
            ext = _extended_hours_from_quote(q, gated_state)
            if ext:
                extended[sym] = ext

    missing = [s for s in symbols if s not in raw]
    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(missing), 14)) as ex:
            futures = {sym: ex.submit(_fetch_one, sym) for sym in missing}
        for sym, fut in futures.items():
            _, price, prev = fut.result()
            if price is not None and price > 0 and (prev is None or prev > 0):
                raw[sym] = (price, prev, None)

    now_iso = datetime.now(timezone.utc).isoformat()
    for sym in symbols:
        hit = raw.get(sym)
        if hit is None:
            providers[sym] = None
            states[sym] = "unavailable"
            as_of[sym] = None
            continue
        price, prev, observed_at = hit
        prices[sym] = price
        if prev is not None:
            prev_prices[sym] = prev
        providers[sym] = "yahoo"
        states[sym] = "live"
        as_of[sym] = observed_at or now_iso

    return {
        "prices": prices,
        "prev_prices": prev_prices,
        "providers": providers,
        "states": states,
        "as_of": as_of,
        "extended": extended,
    }


def _resolve_coingecko_coin(raw: str) -> tuple[str | None, str]:
    """Resolve one requested crypto identifier to a canonical CoinGecko coin id.

    Checked in order: (1) the small known-ticker map `COINGECKO_IDS` also used by portfolio
    crypto routing (e.g. 'BTC-USD' -> 'bitcoin'); (2) CoinGecko's own /search (`search_coin`),
    which resolves a coin NAME, a market SYMBOL, or — since a well-known coin's id and name
    usually coincide ('bitcoin', 'solana') — the id itself. Returns (coingecko_id, display)
    or (None, raw) when nothing matches."""
    key = (raw or "").strip()
    if not key:
        return None, raw
    upper = key.upper()
    if upper in COINGECKO_IDS:
        display = upper[:-4] if upper.endswith("-USD") else upper
        return COINGECKO_IDS[upper], display
    hit = search_coin(key)
    if hit.get("id"):
        return hit["id"], hit.get("symbol") or upper
    return None, upper


def fetch_coingecko_quotes(
    coins: list[str], vs_currency: str = "usd", *, include_facts: bool = False
) -> dict:
    """Direct CoinGecko quotes for crypto assets — by ticker ('BTC-USD'), CoinGecko coin id
    ('bitcoin'), or a name/symbol resolved via CoinGecko's own search ('solana'). NO Yahoo
    routing: even a "major" pair `fetch_live` would normally check Yahoo for first is always
    answered by CoinGecko here. `include_facts=True` adds market cap/rank/supply/ATH per coin
    (from the SAME cached CoinGecko payload `fetch_coin_facts` already maintains — no extra
    round-trip per fact). See the module note above for why this neither caches nor records
    into price_history."""
    requested = list(dict.fromkeys(c.strip() for c in coins if c and c.strip()))
    prices: dict[str, float] = {}
    prev_prices: dict[str, float] = {}
    providers: dict[str, str | None] = {}
    states: dict[str, str] = {}
    as_of: dict[str, str | None] = {}
    resolved_ids: dict[str, str] = {}
    if not requested:
        return {
            "prices": prices,
            "prev_prices": prev_prices,
            "providers": providers,
            "states": states,
            "as_of": as_of,
            "resolved_ids": resolved_ids,
        }

    unresolved: list[str] = []
    for raw in requested:
        coin_id, _display = _resolve_coingecko_coin(raw)
        if coin_id:
            resolved_ids[raw] = coin_id
        else:
            unresolved.append(raw)

    if resolved_ids:
        pairs = list(resolved_ids.items())
        fetched_prices, fetched_prev, fetched_as_of = _fetch_coingecko_by_id(pairs, vs_currency)
        for req in resolved_ids:
            if req in fetched_prices and req in fetched_as_of:
                prices[req] = fetched_prices[req]
                if req in fetched_prev:
                    prev_prices[req] = fetched_prev[req]
                providers[req] = "coingecko"
                states[req] = "live"
                as_of[req] = fetched_as_of[req]
            else:
                providers[req] = None
                states[req] = "unavailable"
                as_of[req] = None

    for raw in unresolved:
        providers[raw] = None
        states[raw] = "unavailable"
        as_of[raw] = None

    result: dict = {
        "prices": prices,
        "prev_prices": prev_prices,
        "providers": providers,
        "states": states,
        "as_of": as_of,
        "resolved_ids": resolved_ids,
    }
    if include_facts:
        facts: dict[str, dict] = {}
        for req, coin_id in resolved_ids.items():
            if states.get(req) != "live":
                continue
            f = fetch_coin_facts(coin_id)
            if f:
                facts[req] = f
        if facts:
            result["facts"] = facts
    return result

"""Sector peer aggregation for the Analysis FA panel.

Resolves a GICS sector → Yahoo screener peer universe → per-metric low/mean/high.
Tier-1 metrics come from screener quote fields; tier-2 fall back to
``fetch_quote_stats`` on the largest peers (cached 10 min per symbol).
Sector aggregates are cached 6h.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from typing import Any

_log = logging.getLogger(__name__)

_SECTOR_TTL = 21600.0  # 6h — sector stats move slowly
_MIN_PEERS = 5
_SCREENER_SIZE = 100
_TIER2_PEER_CAP = 40
_MIN_MARKET_CAP = 1_000_000_000  # $1B — drop micro-caps

# Cache: sector → (fetched_at, {metric_id: {low, mean, high, count}})
_sector_cache: dict[str, tuple[float, dict[str, dict]]] = {}

# Metric id → screener quote field (tier 1 — free with the screener response).
_SCREENER_FIELDS: dict[str, str] = {
    "pe_ratio": "trailingPE",
    "forward_pe": "forwardPE",
    "price_to_book": "priceToBook",
    "dividend_yield": "dividendYield",
    "eps": "epsTrailingTwelveMonths",
}

# Metric id → fetch_quote_stats key (tier 2 — needs per-peer .info).
_TIER2_KEYS: tuple[str, ...] = (
    "peg_ratio",
    "ev_to_ebitda",
    "eps_growth",
    "revenue_growth",
    "profit_margin",
    "return_on_equity",
    "debt_to_equity",
)

# Outlier bounds per metric (inclusive). None = no bound on that side.
# Negative P/E / PEG / EV/EBITDA / P/B are dropped; growth/margins clamped to ±1
# (Yahoo returns ratios; dividend yield may be percent OR ratio — normalize later).
_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "pe_ratio": (0.0, 500.0),
    "forward_pe": (0.0, 500.0),
    "peg_ratio": (0.0, 50.0),
    "price_to_book": (0.0, 100.0),
    "ev_to_ebitda": (0.0, 200.0),
    "dividend_yield": (0.0, 0.25),  # after normalizing to ratio
    "eps": (None, None),
    "eps_growth": (-1.0, 5.0),
    "revenue_growth": (-1.0, 5.0),
    "profit_margin": (-1.0, 1.0),
    "return_on_equity": (-1.0, 2.0),
    "debt_to_equity": (0.0, 1000.0),
}


def aggregate(values: list[float]) -> dict[str, float | int] | None:
    """Simple min / mean / max. Returns None when fewer than ``_MIN_PEERS`` values."""
    if len(values) < _MIN_PEERS:
        return None
    lo = min(values)
    hi = max(values)
    mean = sum(values) / len(values)
    return {
        "low": round(lo, 6),
        "mean": round(mean, 6),
        "high": round(hi, 6),
        "count": len(values),
    }


def position_pct(ticker: float, low: float, high: float) -> float | None:
    """Marker position on a low→high track. None when the range collapses."""
    if high <= low:
        return None
    pct = (ticker - low) / (high - low) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


def _finite(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):  # noqa: PLR0124 — NaN check
        return None
    return n


def _normalize_div_yield(n: float, *, from_screener: bool = False) -> float:
    """Normalize yield to a ratio in [0, 1].

    Screener ``dividendYield`` is percent (0.47 → 0.47%). Tier-2 values from
    ``fetch_quote_stats`` are already ratios — only scale when clearly percent (>1).
    """
    if from_screener:
        return n / 100.0
    return n / 100.0 if n > 1.0 else n


def _in_bounds(metric_id: str, n: float) -> bool:
    lo, hi = _BOUNDS.get(metric_id, (None, None))
    if lo is not None and n < lo:
        return False
    if hi is not None and n > hi:
        return False
    return True


def _clean(metric_id: str, raw: Any, *, from_screener: bool = False) -> float | None:
    n = _finite(raw)
    if n is None:
        return None
    if metric_id == "dividend_yield":
        n = _normalize_div_yield(n, from_screener=from_screener)
    if not _in_bounds(metric_id, n):
        return None
    return n


def fetch_sector_peers(sector: str, size: int = _SCREENER_SIZE) -> list[dict]:
    """Yahoo EquityQuery screener for a GICS sector. Returns quote dicts (may be empty)."""
    if not sector:
        return []
    try:
        import yfinance as yf
        from yfinance import EquityQuery

        q = EquityQuery(
            "and",
            [
                EquityQuery("eq", ["sector", sector]),
                EquityQuery("is-in", ["exchange", "NMS", "NYQ"]),
                EquityQuery("gte", ["intradaymarketcap", _MIN_MARKET_CAP]),
            ],
        )
        result = yf.screen(
            q,
            size=min(size, 250),
            sortField="intradaymarketcap",
            sortAsc=False,
        )
        quotes = result.get("quotes") if isinstance(result, dict) else None
        return list(quotes or [])
    except Exception:
        _log.info("sector_fundamentals: screener failed for %s", sector, exc_info=True)
        return []


def _tier1_from_quotes(quotes: list[dict]) -> dict[str, list[float]]:
    buckets: dict[str, list[float]] = {mid: [] for mid in _SCREENER_FIELDS}
    for q in quotes:
        for mid, field in _SCREENER_FIELDS.items():
            n = _clean(mid, q.get(field), from_screener=True)
            if n is not None:
                buckets[mid].append(n)
    return buckets


def _tier2_from_peers(symbols: list[str]) -> dict[str, list[float]]:
    """Fetch quote stats for the largest peers; collect tier-2 metric values."""
    buckets: dict[str, list[float]] = {mid: [] for mid in _TIER2_KEYS}
    if not symbols:
        return buckets
    from backend.services.prices import fetch_quote_stats

    capped = symbols[:_TIER2_PEER_CAP]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(capped), 8)) as ex:
        results = list(ex.map(fetch_quote_stats, capped))
    for stats in results:
        if not stats:
            continue
        for mid in _TIER2_KEYS:
            n = _clean(mid, stats.get(mid))
            if n is not None:
                buckets[mid].append(n)
    return buckets


def _aggregate_all(buckets: dict[str, list[float]]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for mid, vals in buckets.items():
        agg = aggregate(vals)
        if agg is not None:
            out[mid] = agg
    return out


def sector_fundamentals_for(sector: str) -> dict[str, dict]:
    """Per-metric ``{low, mean, high, count}`` for a GICS sector. Cached 6h.

    Returns ``{}`` when the sector is unknown / screener fails / no metric clears
    the peer floor.
    """
    key = (sector or "").strip()
    if not key:
        return {}
    hit = _sector_cache.get(key)
    if hit and (time.monotonic() - hit[0]) < _SECTOR_TTL:
        return hit[1]

    quotes = fetch_sector_peers(key)
    if not quotes:
        _sector_cache[key] = (time.monotonic(), {})
        return {}

    buckets = _tier1_from_quotes(quotes)
    symbols = [q.get("symbol") for q in quotes if q.get("symbol")]
    symbols = [s for s in symbols if isinstance(s, str) and s]
    # Merge tier-2 into the same buckets (tier-1 keys stay as-is; tier-2 fill new ones).
    for mid, vals in _tier2_from_peers(symbols).items():
        buckets[mid] = vals

    # Dividend yield may also appear on tier-2 .info — but we already have screener
    # coverage. PEG / growth etc. only live in tier 2.

    result = _aggregate_all(buckets)
    _sector_cache[key] = (time.monotonic(), result)
    return result


def resolve_sector(instrument: dict | None, symbol: str | None = None) -> str | None:
    """Instrument.sector first; fall back to a live Yahoo .info lookup."""
    if instrument:
        s = (instrument.get("sector") or "").strip()
        if s:
            return s
    sym = symbol or (instrument or {}).get("price_source_id") or ""
    if not sym:
        return None
    try:
        from backend.services.prices import _fetch_sector

        return _fetch_sector(sym) or None
    except Exception:
        return None


def clear_sector_cache() -> None:
    """Test seam — drop the in-process sector aggregate cache."""
    _sector_cache.clear()


# ── Row builders for fundamentals_snapshot ────────────────────────────────────

VALUATION_SPECS: tuple[tuple[str, str, str], ...] = (
    ("pe_ratio", "P/E", "Trailing price-to-earnings vs sector peers."),
    ("forward_pe", "Fwd P/E", "Forward price-to-earnings vs sector peers."),
    ("peg_ratio", "PEG", "P/E divided by expected earnings growth vs sector peers."),
    ("price_to_book", "P/B", "Price-to-book vs sector peers."),
    ("ev_to_ebitda", "EV/EBITDA", "Enterprise value to EBITDA vs sector peers."),
    ("dividend_yield", "Yield", "Dividend yield vs sector peers."),
)
GROWTH_SPECS: tuple[tuple[str, str, str], ...] = (
    ("eps", "EPS (ttm)", "Trailing twelve-month earnings per share vs sector peers."),
    ("eps_growth", "EPS growth", "Year-over-year earnings growth vs sector peers."),
    ("revenue_growth", "Rev growth", "Year-over-year revenue growth vs sector peers."),
    ("profit_margin", "Profit mgn", "Net profit margin vs sector peers."),
    ("return_on_equity", "ROE", "Return on equity vs sector peers."),
    ("debt_to_equity", "Debt/Equity", "Total debt to equity vs sector peers."),
)

# Metrics where a LOWER reading is the favorable end of the sector range (cheap /
# less leverage). The track still runs low→high left-to-right; the gradient flips
# so green sits on the low end for these.
_LOWER_IS_BETTER = frozenset(
    {
        "pe_ratio",
        "forward_pe",
        "peg_ratio",
        "price_to_book",
        "ev_to_ebitda",
        "debt_to_equity",
    }
)

# Ratios by the time they reach format_metric (1.06 is 106%) — a growth or ROE figure
# above 1 is real, so its size says nothing about its unit.
_RATIO_PCT_METRICS = frozenset(
    {"dividend_yield", "eps_growth", "revenue_growth", "profit_margin", "return_on_equity"}
)
# Yahoo's debtToEquity is already a percent (84.3 is debt at 0.84x equity).
_PERCENT_METRICS = frozenset({"debt_to_equity"})
_MONEY_METRICS = frozenset({"eps"})


def higher_is_better(metric_id: str) -> bool:
    """True when a higher reading is the favorable end of the sector range."""
    return metric_id not in _LOWER_IS_BETTER


def format_metric(metric_id: str, n: float, currency: str = "USD") -> str:
    """Server-side display string so the frontend does not re-derive format rules."""
    from backend.services.portfolio_brief import _CURRENCY_SYMBOL

    if metric_id in _RATIO_PCT_METRICS:
        pct = n * 100.0
        return f"{pct:.1f}%" if metric_id != "dividend_yield" else f"{pct:.2f}%"
    if metric_id in _PERCENT_METRICS:
        return f"{n:.1f}%"
    if metric_id in _MONEY_METRICS:
        sym = _CURRENCY_SYMBOL.get((currency or "USD").upper()) or (currency or "USD")
        sign = "-" if n < 0 else ""
        return f"{sign}{sym}{abs(n):,.2f}"
    return f"{n:,.2f}"


def build_fund_row(
    metric_id: str,
    label: str,
    help_text: str,
    ticker: float,
    currency: str,
    sector_stats: dict[str, dict] | None,
) -> dict:
    """One FA row: ticker value + optional sector low/mean/high + marker position."""
    row: dict = {
        "id": metric_id,
        "label": label,
        "help": help_text,
        "ticker": ticker,
        "ticker_display": format_metric(metric_id, ticker, currency),
        "higher_is_better": higher_is_better(metric_id),
        "sector_low": None,
        "sector_mean": None,
        "sector_high": None,
        "sector_low_display": None,
        "sector_mean_display": None,
        "sector_high_display": None,
        "position_pct": None,
        "peer_count": None,
    }
    agg = (sector_stats or {}).get(metric_id)
    if not agg:
        return row
    lo, mean, hi = agg.get("low"), agg.get("mean"), agg.get("high")
    if lo is None or mean is None or hi is None:
        return row
    row["sector_low"] = lo
    row["sector_mean"] = mean
    row["sector_high"] = hi
    row["sector_low_display"] = format_metric(metric_id, float(lo), currency)
    row["sector_mean_display"] = format_metric(metric_id, float(mean), currency)
    row["sector_high_display"] = format_metric(metric_id, float(hi), currency)
    row["peer_count"] = agg.get("count")
    row["position_pct"] = position_pct(float(ticker), float(lo), float(hi))
    return row


def build_fund_rows(
    specs: tuple[tuple[str, str, str], ...],
    stats: dict,
    currency: str,
    sector_stats: dict[str, dict] | None,
) -> list[dict]:
    """Build FA rows for metrics present in ``stats``."""
    rows: list[dict] = []
    for mid, label, help_text in specs:
        if mid not in stats or stats[mid] is None:
            continue
        try:
            ticker = float(stats[mid])
        except (TypeError, ValueError):
            continue
        # Dividend yield on .info is sometimes already a percent (>1); normalize to ratio.
        if mid == "dividend_yield" and abs(ticker) > 1.0:
            ticker = ticker / 100.0
        rows.append(build_fund_row(mid, label, help_text, ticker, currency, sector_stats))
    return rows

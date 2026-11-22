"""Convert Yahoo's minor-unit quotes (pence, cents, agorot) to the major currency.

Every Yahoo read in ``services/prices.py`` passes its raw payload through one of these
before anything else looks at it, so a London listing quoted ``GBp`` arrives as GBP — the
price, its history, its dividends and its targets alike. Which fields Yahoo reports in the
minor unit was observed live (VOD.L, 2026-11-22): every per-share price and its absolute
changes are in pence; ``marketCap``, ``dividendRate``, ``trailingAnnualDividendRate`` and
``lastDividendValue`` are already pounds; EPS and book value are in ``financialCurrency``.
Only the first group is scaled. See ``core/quote_units.py``.
"""

from __future__ import annotations

from typing import Any

from backend.core.quote_units import major_currency, to_major_factor

# v7 quote + ``.info`` + ``fast_info`` keys that carry a per-share price in the QUOTE unit.
QUOTE_PRICE_FIELDS: frozenset[str] = frozenset(
    {
        "regularMarketPrice",
        "regularMarketPreviousClose",
        "regularMarketOpen",
        "regularMarketDayHigh",
        "regularMarketDayLow",
        "regularMarketChange",
        "previousClose",
        "currentPrice",
        "lastPrice",
        "open",
        "dayHigh",
        "dayLow",
        "bid",
        "ask",
        "fulldayPrice",
        "fulldayChange",
        "preMarketPrice",
        "preMarketChange",
        "postMarketPrice",
        "postMarketChange",
        "overnightMarketPrice",
        "overnightMarketChange",
        "fiftyTwoWeekHigh",
        "fiftyTwoWeekLow",
        "fiftyTwoWeekHighChange",
        "fiftyTwoWeekLowChange",
        "fiftyDayAverage",
        "fiftyDayAverageChange",
        "twoHundredDayAverage",
        "twoHundredDayAverageChange",
        "targetLowPrice",
        "targetMeanPrice",
        "targetMedianPrice",
        "targetHighPrice",
        "chartPreviousClose",
    }
)

# History frame columns in the quote unit (never Volume or Stock Splits).
_FRAME_PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Adj Close", "Dividends")


def _scaled(value: Any, factor: float) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return value * factor


def quote_to_major(quote: dict) -> dict:
    """A v7 quote / ``.info`` dict with its price fields in the major currency and
    ``currency`` set to it. A quote already in a major currency is returned unchanged."""
    raw = quote.get("currency")
    factor = to_major_factor(raw)
    if factor == 1.0:
        return quote
    out = {k: (_scaled(v, factor) if k in QUOTE_PRICE_FIELDS else v) for k, v in quote.items()}
    out["currency"] = major_currency(raw)
    return out


def frame_to_major(frame, raw_currency: str | None):
    """A yfinance history DataFrame with its price columns in the major currency."""
    factor = to_major_factor(raw_currency)
    if factor == 1.0 or frame is None or getattr(frame, "empty", True):
        return frame
    frame = frame.copy()
    for column in _FRAME_PRICE_COLUMNS:
        if column in frame.columns:
            frame[column] = frame[column] * factor
    return frame


def series_to_major(series, raw_currency: str | None):
    """A yfinance Series of per-share amounts (``Ticker.dividends``) in the major currency."""
    factor = to_major_factor(raw_currency)
    if factor == 1.0 or series is None:
        return series
    return series * factor


def chart_to_major(payload: dict | None) -> dict | None:
    """A v8 chart payload with each result's bars, meta prices and dividend events in the
    major currency (the result's own ``meta.currency`` decides)."""
    results = ((payload or {}).get("chart") or {}).get("result")
    if not results:
        return payload
    converted = [_chart_result_to_major(r) for r in results]
    return {**payload, "chart": {**payload["chart"], "result": converted}}


def _chart_result_to_major(result: dict) -> dict:
    meta = result.get("meta") or {}
    factor = to_major_factor(meta.get("currency"))
    if factor == 1.0:
        return result
    out = dict(result)
    out["meta"] = {**quote_to_major(meta), "currency": major_currency(meta.get("currency"))}
    indicators = dict(result.get("indicators") or {})
    if indicators.get("quote"):
        indicators["quote"] = [
            {
                key: (
                    [_scaled(v, factor) for v in values]
                    if key in ("open", "high", "low", "close") and isinstance(values, list)
                    else values
                )
                for key, values in (row or {}).items()
            }
            for row in indicators["quote"]
        ]
    if indicators.get("adjclose"):
        indicators["adjclose"] = [
            {**row, "adjclose": [_scaled(v, factor) for v in (row or {}).get("adjclose") or []]}
            for row in indicators["adjclose"]
        ]
    out["indicators"] = indicators
    events = result.get("events") or {}
    if events.get("dividends"):
        out["events"] = {
            **events,
            "dividends": {
                key: {**ev, "amount": _scaled(ev.get("amount"), factor)}
                for key, ev in events["dividends"].items()
            },
        }
    return out

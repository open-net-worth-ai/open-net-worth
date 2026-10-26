"""The demo book's investment side: instruments, price history, trades and the RSU grant.

Real tickers with their real price sources, so a hosted demo with network access refreshes
live and keeps refreshing. The seeded HISTORY is synthetic — the demo must look right the
moment it boots, before any provider has been called, and must stay right on a host with no
outbound network at all (the price ladder falls back to the last recorded close).

Where the two meet is the interesting part: the synthetic history is anchored on TODAY'S REAL
price when one can be fetched, so it walks to the number the live series then continues from.
Anchoring it on a guess instead leaves a visible cliff at the join.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta

from backend.scripts.demo._timeline import Timeline, on_business_day, rng

log = logging.getLogger(__name__)

# symbol → (name, asset_class, asset_subclass, sector, source, source_id, anchor price today,
#           annual drift, annual volatility, precision)
INSTRUMENTS: list[dict] = [
    {
        "symbol": "VOO",
        "name": "Vanguard S&P 500 ETF",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "sector": "",
        "price_source": "yahoo",
        "price_source_id": "VOO",
        "anchor": 604.0,
        "drift": 0.11,
        "vol": 0.15,
    },
    {
        "symbol": "VXUS",
        "name": "Vanguard Total International Stock ETF",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "sector": "",
        "price_source": "yahoo",
        "price_source_id": "VXUS",
        "anchor": 73.2,
        "drift": 0.07,
        "vol": 0.14,
    },
    {
        "symbol": "SCHD",
        "name": "Schwab U.S. Dividend Equity ETF",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "sector": "",
        "price_source": "yahoo",
        "price_source_id": "SCHD",
        "anchor": 28.9,
        "drift": 0.06,
        "vol": 0.12,
        "dividend_rate_annual": 1.05,
        "dividend_frequency": "quarterly",
        "quarterly_dividend": 0.26,
    },
    {
        "symbol": "BND",
        "name": "Vanguard Total Bond Market ETF",
        "asset_class": "bond",
        "asset_subclass": "etf",
        "sector": "",
        "price_source": "yahoo",
        "price_source_id": "BND",
        "anchor": 74.6,
        "drift": 0.015,
        "vol": 0.05,
        "dividend_rate_annual": 2.85,
        "dividend_frequency": "quarterly",
        "quarterly_dividend": 0.24,
    },
    {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "sector": "Technology",
        "price_source": "yahoo",
        "price_source_id": "AAPL",
        "anchor": 241.0,
        "drift": 0.13,
        "vol": 0.26,
        "dividend_rate_annual": 1.04,
        "dividend_frequency": "quarterly",
        "quarterly_dividend": 0.26,
    },
    {
        "symbol": "NVDA",
        "name": "NVIDIA Corporation",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "sector": "Technology",
        "price_source": "yahoo",
        "price_source_id": "NVDA",
        "anchor": 186.0,
        "drift": 0.34,
        "vol": 0.46,
    },
    {
        "symbol": "GLDM",
        "name": "SPDR Gold MiniShares Trust",
        "asset_class": "commodity",
        "asset_subclass": "etf",
        "sector": "",
        "price_source": "yahoo",
        "price_source_id": "GLDM",
        "anchor": 79.4,
        "drift": 0.12,
        "vol": 0.16,
    },
    {
        "symbol": "BTC",
        "name": "Bitcoin",
        "asset_class": "crypto",
        "asset_subclass": "coin",
        "sector": "",
        "price_source": "coingecko",
        "price_source_id": "bitcoin",
        "anchor": 96500.0,
        "drift": 0.38,
        "vol": 0.58,
        "precision": 2,
    },
    {
        "symbol": "ETH",
        "name": "Ethereum",
        "asset_class": "crypto",
        "asset_subclass": "coin",
        "sector": "",
        "price_source": "coingecko",
        "price_source_id": "ethereum",
        "anchor": 3420.0,
        "drift": 0.22,
        "vol": 0.66,
        "precision": 2,
    },
    # The employer whose RSUs Alex holds. A REAL listed company on purpose: a privately-held
    # employer would be a manual instrument nobody can quote, so the grant would sit at a
    # made-up mark, its chart would be a straight line, and every equity screen would show a
    # holding the app cannot actually price. Every instrument in this book is a real ticker.
    {
        "symbol": "NET",
        "name": "Cloudflare, Inc.",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "sector": "Technology",
        "price_source": "yahoo",
        "price_source_id": "NET",
        "anchor": 168.0,
        "drift": 0.21,
        "vol": 0.42,
    },
]


def live_anchors(specs: list[dict] | None = None) -> dict[str, float]:
    """Today's REAL price per symbol, when the host can reach a provider. ``{}`` otherwise.
    ``specs`` defaults to ``INSTRUMENTS``; the Singapore chapter's ETFs ask for their own.

    These are real tickers with real price sources, so a demo with network gets live prices the
    moment the app boots — and a synthetic history ending on a GUESSED anchor then joins that
    live price at a visible cliff (a real build put Bitcoin at 96,500 on the Friday and 69,360
    on the Sunday). Anchoring the generated series on the real current price removes the seam:
    the history walks to today's actual number, and tomorrow's live tick is an ordinary daily
    move rather than a jump.

    Best-effort by construction. A demo must build with no network at all, so every failure —
    unreachable provider, rate limit, a symbol the provider does not know — falls back to that
    symbol's static anchor alone.
    """
    specs = INSTRUMENTS if specs is None else specs
    tickers = [spec["price_source_id"] for spec in specs if spec["price_source_id"]]
    try:
        from backend.services import prices as prices_service

        # record_history=False: this build writes the series itself; asking is not an event in it.
        result = prices_service.fetch_live(tickers, ["USD"], record_history=False)
    except Exception as exc:  # noqa: BLE001 — offline is a supported way to build the demo
        # Type only, never the message: a provider error can carry the request URL, and a
        # non-demo instance running `make seed` may have a keyed provider URL to carry.
        log.info("demo: no live prices (%s); using the built-in anchors", type(exc).__name__)
        return {}
    quoted = result.get("prices") or {}
    out: dict[str, float] = {}
    for spec in specs:
        value = quoted.get(spec["price_source_id"])
        if isinstance(value, (int, float)) and value > 0:
            out[spec["symbol"]] = float(value)
    if out:
        log.info("demo: anchored %d of %d instruments on live prices", len(out), len(specs))
    return out


def price_series(spec: dict, tl: Timeline, anchor: float | None = None) -> list[dict]:
    """A daily close series for one instrument, ending on today's price.

    ``anchor`` is today's real price when one could be fetched; without it the spec's own
    built-in anchor is used, which is what an offline build gets.

    The noise is a Brownian BRIDGE (pinned to zero at both ends) laid over a deterministic
    drift, so the series has a realistic path AND an exact total return of ``exp(drift * T)``
    across the window. A plain random walk rescaled to the anchor does not: whatever return
    the walk happened to draw becomes the holding's P&L, and NVDA lands flat while a bond
    fund doubles. Pinning the endpoints is what makes "this position is up 34%" a decision
    rather than a dice roll.
    """
    r = rng(f"price:{spec['symbol']}")
    days = tl.total_days
    dt = 1.0 / 365.0
    mu, sigma = spec["drift"], spec["vol"]
    end_price = float(anchor or spec["anchor"])
    walk = [0.0]
    for _ in range(days):
        walk.append(walk[-1] + r.gauss(0.0, 1.0) * math.sqrt(dt))
    drift_total = walk[-1]
    precision = spec.get("precision", 2)

    raw: list[tuple[str, float]] = []
    for i in range(days + 1):
        d = tl.start + timedelta(days=i)
        # Equities do not print a close at the weekend; crypto does.
        if spec["asset_class"] != "crypto" and d.weekday() >= 5:
            continue
        bridge = walk[i] - (i / days) * drift_total if days else 0.0
        raw.append((d.isoformat(), math.exp(mu * (i * dt) + sigma * bridge)))

    # Scale so the LAST EMITTED point is the anchor — not the last calendar day. On a weekend
    # build an equity's newest close is Friday's, and Friday's close IS what a provider quotes
    # as its current price today; anchoring the skipped Sunday instead would leave the series
    # ending a couple of percent away from the live number it is supposed to meet.
    scale = end_price / raw[-1][1]
    return [{"date": day, "price": round(factor * scale, precision)} for day, factor in raw]


def price_on(series: list[dict], d: date) -> float:
    """Closing price on ``d``, or the last close before it (weekends/holidays)."""
    best = series[0]["price"]
    target = d.isoformat()
    for point in series:
        if point["date"] > target:
            break
        best = point["price"]
    return best


# ── The trade plan ────────────────────────────────────────────────────────────
# Each entry is one recurring purchase: (account key, symbol, monthly dollars, start month
# offset, day of month). Dollar-cost averaging is what gives the lot list a real shape.

DCA_PLAN = [
    ("brokerage", "VOO", 700.0, 0, 5),
    ("brokerage", "VXUS", 250.0, 0, 5),
    ("brokerage", "SCHD", 200.0, 3, 6),
    ("brokerage", "GLDM", 150.0, 8, 6),
    ("roth", "VOO", 380.0, 0, 7),
    ("roth", "BND", 200.0, 0, 7),
    ("401k", "VOO", 1150.0, 0, 12),
    ("401k", "BND", 380.0, 0, 12),
    ("crypto", "BTC", 110.0, 0, 14),
    ("crypto", "ETH", 60.0, 2, 14),
]

# One-off convictions: (account key, symbol, month offset, dollars).
ONE_OFF_BUYS = [
    ("brokerage", "AAPL", 2, 3200.0),
    ("brokerage", "NVDA", 6, 2600.0),
    ("brokerage", "AAPL", 15, 1800.0),
    ("brokerage", "NVDA", 21, 2200.0),
]

# Trims: (account key, symbol, month offset, fraction of units held to sell).
TRIMS = [
    ("brokerage", "VXUS", 19, 0.35),
    ("brokerage", "NVDA", 26, 0.30),
]


def build_trades(tl: Timeline, series: dict[str, list[dict]]) -> list[dict]:
    """Flatten the plan into dated, priced buy orders.

    Returns ``[{account, symbol, date, quantity, unit_cost, fees, gross}]`` — the shape both
    the lot writer and the cash-leg writer consume, so a lot can never disagree with the
    transaction that paid for it.
    """
    r = rng("trades")
    out = []
    for account, symbol, monthly, start, day in DCA_PLAN:
        for mi, m in enumerate(tl.months):
            if mi < start:
                continue
            d = tl.clamp(on_business_day(m.replace(day=day)))
            if d is None:
                continue
            price = price_on(series[symbol], d)
            gross = round(monthly * (1.0 + 0.004 * mi), 2)
            fees = 0.0 if account != "crypto" else round(gross * 0.006, 2)
            out.append(_order(account, symbol, d, gross, price, fees))
    for account, symbol, mi, gross in ONE_OFF_BUYS:
        if mi >= len(tl.months):
            continue
        d = tl.clamp(on_business_day(tl.months[mi].replace(day=r.randint(8, 24))))
        if d is None:
            continue
        out.append(_order(account, symbol, d, gross, price_on(series[symbol], d), 0.0))
    out.sort(key=lambda o: (o["date"], o["symbol"]))
    return out


def _order(account: str, symbol: str, d: date, gross: float, price: float, fees: float) -> dict:
    qty = round((gross - fees) / price, 8 if symbol in ("BTC", "ETH") else 4)
    return {
        "account": account,
        "symbol": symbol,
        "date": d,
        "quantity": qty,
        "unit_cost": price,
        "fees": fees,
        "gross": round(qty * price + fees, 2),
    }


def build_trims(tl: Timeline, series: dict[str, list[dict]], trades: list[dict]) -> list[dict]:
    """Dated sell orders sized off the units actually held on the sale date."""
    out = []
    for account, symbol, mi, fraction in TRIMS:
        if mi >= len(tl.months):
            continue
        d = tl.clamp(on_business_day(tl.months[mi].replace(day=13)))
        if d is None:
            continue
        held = sum(
            t["quantity"]
            for t in trades
            if t["account"] == account and t["symbol"] == symbol and t["date"] <= d
        )
        qty = round(held * fraction, 4)
        if qty <= 0:
            continue
        price = price_on(series[symbol], d)
        out.append(
            {
                "account": account,
                "symbol": symbol,
                "date": d,
                "quantity": qty,
                "unit_price": price,
                "fees": 0.0,
                "gross": round(qty * price, 2),
            }
        )
    return out


# ── The RSU grant ─────────────────────────────────────────────────────────────

# Size the grant by VALUE, not by a unit count. A fixed number of units is only meaningful
# against a fixed share price — 1,600 units was calibrated to a fabricated $68.50 stock, and
# the moment that became a real ticker the same grant was worth more than the entire rest of
# the book (net worth $628k, of which $234k unvested). Deriving units from a target value
# keeps the grant proportionate whatever the employer's shares actually trade at.
GRANT_TARGET_VALUE = 130_000.0
GRANT_MONTHS_AGO = 26
GRANT_CLIFF_MONTHS = 12
GRANT_TRANCHES = 16  # quarterly over four years


def grant_schedule(tl: Timeline, series: list[dict] | None = None) -> dict:
    """Grant date + the sixteen quarterly tranches, with the cliff folded into the first.

    A four-year quarterly ladder granted 26 months ago leaves a handful vested behind us and
    a clearly-dated next one ahead — which is the whole point of showing unvested equity.

    ``series`` is the employer's price history; the unit count is derived from the share price
    on the grant date so the grant is worth GRANT_TARGET_VALUE then, whatever the ticker costs.
    """
    from backend.scripts.demo._timeline import add_months

    granted = add_months(tl.today.replace(day=15), -GRANT_MONTHS_AGO)
    # Units the grant would have been worth GRANT_TARGET_VALUE at the price on the grant date.
    at_grant = price_on(series, granted) if series else EMPLOYER_SPEC["anchor"]
    total_units = round(GRANT_TARGET_VALUE / max(at_grant, 1.0), 0)
    per_quarter = round(total_units / GRANT_TRANCHES, 4)
    tranches = []
    for i in range(GRANT_TRANCHES):
        months = GRANT_CLIFF_MONTHS + i * 3
        vest_date = add_months(granted, months)
        units = per_quarter * (GRANT_CLIFF_MONTHS // 3) if i == 0 else per_quarter
        tranches.append({"vest_date": vest_date.isoformat(), "units": round(units, 4)})
    # The cliff tranche absorbs the first year, so the ladder is 13 dates, not 16.
    tranches = tranches[: GRANT_TRANCHES - (GRANT_CLIFF_MONTHS // 3) + 1]
    total = round(sum(t["units"] for t in tranches), 4)
    return {"grant_date": granted.isoformat(), "tranches": tranches, "total_units": total}


# The employer instrument, by symbol — used to size the grant when no series is supplied.
EMPLOYER_SPEC = next(i for i in INSTRUMENTS if i["symbol"] == "NET")

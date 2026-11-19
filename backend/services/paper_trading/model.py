"""The paper book's value types — assets, quotes, sessions and the three decisions an order can
get. Pure data, no I/O: every adapter's ``decide`` / ``value`` / ``lifecycle`` works on these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Basis = Literal["quote", "nav", "modelled", "synthetic", "yours"]

BASIS_LABEL: dict[str, str | None] = {
    "quote": None,
    "nav": None,
    "modelled": "Modelled",
    "synthetic": "Synthetic",
    "yours": "Your price",
}

KIND_LABEL = {
    "listed": "Stocks & ETFs",
    "fund_nav": "Mutual funds",
    "crypto": "Crypto",
    "metal": "Metals",
    "fx": "Currencies",
    "index": "Indices",
    "manual": "Your-priced assets",
}
KIND_ORDER = tuple(KIND_LABEL)

EVENT_KINDS = frozenset(
    {
        "deposit",
        "withdraw",
        "buy",
        "sell",
        "convert",
        "dividend",
        "distribution",
        "split",
        "freeze",
        "mark",
        "fee",
    }
)
ORDER_TYPES = ("market", "limit", "stop", "nav")
TIFS = ("day", "gtc")
FRESH_SECONDS = 20 * 60  # in an open market, a quote older than this is not fresh
NOTE_MAX = 280

DEFAULT_SETTINGS: dict = {
    "slippage_bps": 5.0,
    "commission": {"fixed": 0.0, "pct": 0.0},
    "fx_spread_pct": 0.1,
    "metal_premium_buy_pct": 3.0,
    "metal_discount_sell_pct": 1.0,
    "fractional": True,
    "extended_hours": False,
    "auto_convert": True,
    "fund_distributions": "cash",  # or "reinvest": a fund's distribution buys more units
}


def settings_of(portfolio: dict) -> dict:
    """The portfolio's fill settings over the defaults (a missing key reads as its default)."""
    return {**DEFAULT_SETTINGS, **(portfolio.get("settings") or {})}


@dataclass(frozen=True)
class PaperAsset:
    """One tradeable thing. ``key`` is ``inst:<instrument_id>``, ``ccy:<ISO>`` or ``ctr:<id>``;
    ``symbol`` is what its quote is fetched by (the instrument's price_source_id)."""

    key: str
    kind: str
    symbol: str
    name: str
    currency: str
    unit: str = ""
    quote_per_unit: float = 1.0
    price_source: str = ""
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PaperQuote:
    last: float | None
    currency: str
    as_of: datetime | None
    basis: Basis
    source: str
    bid: float | None = None
    ask: float | None = None
    session_state: str = "UNKNOWN"
    is_open: bool = False
    next_open: datetime | None = None
    timezone: str = ""
    extended: bool = False
    avg_volume: float | None = None
    # fund_nav only: NAV by date {"YYYY-MM-DD": nav}
    navs: dict = field(default_factory=dict)
    # dividends / splits seen for the symbol: [{"date", "kind": "dividend"|"split", "amount"|"ratio"}]
    actions: tuple = ()


@dataclass(frozen=True)
class Session:
    is_open: bool
    state: str
    next_open: datetime | None = None
    timezone: str = ""
    always_open: bool = False


@dataclass(frozen=True)
class Fill:
    price: float
    basis: Basis
    source: str
    quote_as_of: datetime | None
    session: str
    slippage: float = 0.0
    spread_or_premium: float = 0.0
    nav_date: str | None = None


@dataclass(frozen=True)
class Wait:
    reason: str


@dataclass(frozen=True)
class Reject:
    reason: str
    available: float | None = None


Decision = Fill | Wait | Reject

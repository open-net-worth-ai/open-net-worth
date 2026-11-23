"""One name per sector, from any source (ADR 2026-11-23-a-fund-is-looked-through-by-what-…).

A price source says `consumer_cyclical` for a fund and "Consumer Cyclical" for a stock; a person
types "Information Technology" or "Tech". Every reader of a sector bucket goes through
`canonical_sector`, so they all land in one bar: the 11 GICS sectors plus the non-equity buckets
the app already uses.
"""

from __future__ import annotations

import re

_ALIASES: dict[str, str] = {
    "technology": "Technology",
    "information technology": "Technology",
    "tech": "Technology",
    "communication services": "Communication Services",
    "communication": "Communication Services",
    "communications": "Communication Services",
    "telecommunications": "Communication Services",
    "consumer cyclical": "Consumer Discretionary",
    "consumer discretionary": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "consumer staples": "Consumer Staples",
    "energy": "Energy",
    "financial services": "Financials",
    "financials": "Financials",
    "financial": "Financials",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "industrials": "Industrials",
    "basic materials": "Materials",
    "materials": "Materials",
    "realestate": "Real Estate",
    "real estate": "Real Estate",
    "utilities": "Utilities",
    "fixed income": "Fixed income",
    "bonds": "Fixed income",
    "digital assets": "Digital assets",
    "crypto": "Digital assets",
    "commodities": "Commodities",
    "cash": "Cash",
}


def canonical_sector(name: str | None) -> str:
    """The one display name for `name`, or `name` itself (trimmed) when it is none we know —
    an unknown label is shown as typed, never dropped or guessed into another bucket."""
    raw = (name or "").strip()
    if not raw:
        return ""
    key = re.sub(r"[_\s]+", " ", raw).lower()
    return _ALIASES.get(key, raw)

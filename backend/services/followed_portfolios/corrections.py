"""The owner's ticker corrections on a fund follow.

A filing names each holding by CUSIP; the ticker is looked up, and a lookup can miss or be
wrong. A copied portfolio that silently drops or mislabels a position is a real loss, so the
owner can set the ticker themselves. It is kept on the follow (``ticker_overrides``), outranks
every lookup on every refresh, and an empty ticker hands the holding back to the lookup.
"""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.services.followed_portfolios import edgar_13f
from backend.services.followed_portfolios.model import normalize_symbol


def ticker_patch(row: dict, key: str, symbol: str) -> dict:
    """The store patch that sets (or, with ``symbol`` empty, clears) the ticker for the
    holding filed under ``key``, restamping both stored filings."""
    if row.get("kind") != "fund_13f":
        raise ValidationError("Only a fund's 13F holdings take a ticker correction.")
    snaps = [s for s in (row.get("current"), row.get("previous")) if s]
    if not any(p.get("key") == key for s in snaps for p in s.get("positions") or []):
        raise ValidationError("That holding is not in this portfolio's filings.")
    clean = normalize_symbol(symbol)
    if symbol.strip() and not clean:
        raise ValidationError("That is not a ticker.")
    overrides = dict(row.get("ticker_overrides") or {})
    if clean:
        overrides[key] = clean
    else:
        overrides.pop(key, None)
    cusip_map = row.get("cusip_map") or {}
    stamped = [edgar_13f.stamp(s, cusip_map, overrides) for s in snaps]
    return {
        "ticker_overrides": overrides,
        "current": stamped[0],
        "previous": stamped[1] if len(stamped) > 1 else None,
    }

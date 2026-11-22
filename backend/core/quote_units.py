"""Minor-unit quote currencies: a feed that prices a listing in pence, cents or agorot.

Yahoo quotes most London listings in pence (``GBp``, sometimes ``GBX``), Johannesburg in
South African cents (``ZAc``) and Tel Aviv in agorot (``ILA``). Only the CASE tells ``GBp``
from ``GBP``, so an ``.upper()`` before this check turned 26.5 pence into £26.50 — every
holding on those venues valued 100x too high. A feed price is converted to the major
currency at the boundary where it enters the app, and nothing downstream ever sees a minor
unit (ADR ``2026-11-22-a-minor-unit-quote-is-converted-to-its-major-currency-at-the-feed``).
"""

from __future__ import annotations

# Raw feed code (case-sensitive) -> (ISO major currency, major units per one minor unit).
_MINOR_UNITS: dict[str, tuple[str, float]] = {
    "GBp": ("GBP", 0.01),
    "GBX": ("GBP", 0.01),
    "ZAc": ("ZAR", 0.01),
    "ZAC": ("ZAR", 0.01),
    "ILA": ("ILS", 0.01),
}


def is_minor_unit(raw_currency: str | None) -> bool:
    """True when the feed's currency code names a minor unit (pence, cents, agorot)."""
    return (raw_currency or "").strip() in _MINOR_UNITS


def major_currency(raw_currency: str | None) -> str:
    """The ISO-4217 currency a feed code denominates, upper-cased: ``GBp`` -> ``GBP``."""
    raw = (raw_currency or "").strip()
    hit = _MINOR_UNITS.get(raw)
    return hit[0] if hit else raw.upper()


def to_major_factor(raw_currency: str | None) -> float:
    """Multiply a feed price by this to express it in the major currency (1.0 if already)."""
    hit = _MINOR_UNITS.get((raw_currency or "").strip())
    return hit[1] if hit else 1.0

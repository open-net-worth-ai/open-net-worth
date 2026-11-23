"""move_significance — the ONE definition of "is this day's move notable?". Pure: no I/O, no clock.

A move is judged against the instrument's OWN usual daily move (the standard deviation of its
last ``LOOKBACK_RETURNS`` daily returns), never a fixed percentage: 2% is an ordinary day for a
volatile chip stock, a big one for a utility or a bond fund, and noise for crypto. Without enough
history to know what "usual" is, a fixed per-asset-class floor stands in — nothing is ever
estimated from thin data.

Readers: the Intel watch rules (``watch_stats`` / ``watch_history_rules`` / ``watch_rules``) and
the Notable Price Movement timeline (``price_movements`` — its highlight and its live-row rewrite
trigger). ADR ``2026-11-24-a-notable-move-is-measured-against-the-instruments-usual-move``.
"""

from __future__ import annotations

import math

LOOKBACK_RETURNS = 60  # trading days of daily returns for "usual move"
MIN_RETURNS = 30  # fewer than this → no usual move, the fixed floors below apply

Z_NOTABLE = 2.0  # a move this many usual daily moves is worth a look
Z_HIGH = 3.0  # …and this many is unusual
Z_MATERIAL = 2.5  # …and this many, company-specific, is worth an explained alert
MIN_ABS_MOVE_PCT = 1.5  # never call a sub-1.5% day notable, however calm the instrument

# Without history: day-move floors (%) per asset class — "worth a look", not "cratering".
FALLBACK_MOVE_PCT = {"equity": 5.0, "commodity": 6.0, "crypto": 10.0}
_DEFAULT_CLASS = "equity"


def daily_returns(closes: list[float]) -> list[float]:
    """Close-to-close % returns, oldest first (closes oldest first)."""
    return [(b - a) / a * 100.0 for a, b in zip(closes, closes[1:]) if a > 0]


def stdev(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var) if var > 0 else None


def usual_move(closes: list[float]) -> float | None:
    """The instrument's usual daily move in % (σ of its last LOOKBACK_RETURNS daily returns),
    from closes oldest first that END BEFORE the day being judged. None on thin history."""
    rets = daily_returns([c for c in closes if c and c > 0])[-LOOKBACK_RETURNS:]
    if len(rets) < MIN_RETURNS:
        return None
    return stdev(rets)


def fallback_threshold(asset_class: str | None) -> float:
    """The fixed day-move floor (%) used when there is no usual move to compare with."""
    return FALLBACK_MOVE_PCT.get((asset_class or "").lower(), FALLBACK_MOVE_PCT[_DEFAULT_CLASS])


def notable_threshold(sigma: float | None, asset_class: str | None = None) -> float:
    """The |day move| (%) at or above which a move is notable for this instrument."""
    if sigma:
        return max(MIN_ABS_MOVE_PCT, Z_NOTABLE * sigma)
    return fallback_threshold(asset_class)


def is_notable(day_pct: float | None, sigma: float | None, asset_class: str | None = None) -> bool:
    if day_pct is None:
        return False
    return abs(day_pct) >= notable_threshold(sigma, asset_class)


def times_usual(day_pct: float | None, sigma: float | None) -> float | None:
    """How many usual daily moves this one is (unsigned), or None without both."""
    if day_pct is None or not sigma:
        return None
    return abs(day_pct) / sigma

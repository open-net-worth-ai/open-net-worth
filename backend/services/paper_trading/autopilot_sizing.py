"""How big an autopilot proposal's order is: the model says how sure it is, code says how much.

A conviction maps to a target share of the portfolio (low 4%, medium 7%, high 10% — owner,
2026-11-21), never above the mandate's per-holding cap. A buy (new or an add) fills the gap to
that target, as far as the cash above the mandate's floor allows; a trim sells down to it. Pure:
amounts are in the home currency, the caller prices them. ADR
``2026-11-21-autopilot-proposes-the-app-sizes``."""

from __future__ import annotations

import math

CONVICTION_WEIGHT = {"low": 0.04, "medium": 0.07, "high": 0.10}
CONVICTIONS = tuple(CONVICTION_WEIGHT)
# A buy is planned this much under its room, so slippage, spread and commission at the fill can
# never push it over the cap or under the cash floor (the choke would refuse the whole order).
FILL_ROOM = 0.98
# A gap smaller than this share of the portfolio is not worth an order.
MIN_TRADE = 0.005


def target_weight(conviction: str, cap_pct: float | None) -> float:
    weight = CONVICTION_WEIGHT[conviction]
    return min(weight, cap_pct / 100) if cap_pct is not None else weight


def buy_amount(
    *,
    conviction: str,
    total: float,
    current: float,
    cash: float,
    floor_pct: float | None,
    cap_pct: float | None,
) -> tuple[float, str]:
    """(amount to buy in home currency, why). 0 when there is nothing to buy, with the reason."""
    if total <= 0:
        return 0.0, "the portfolio has no value to size against"
    weight = target_weight(conviction, cap_pct)
    capped = weight < CONVICTION_WEIGHT[conviction]
    label = f"to {weight:.0%} ({'the mandate cap' if capped else f'{conviction} conviction'})"
    want = weight * total - current
    if want <= MIN_TRADE * total:
        return 0.0, f"already at {current / total:.1%}, at or above its target {weight:.0%}"
    room_cash = cash - (floor_pct or 0) / 100 * total
    if room_cash <= MIN_TRADE * total:
        return 0.0, "no cash above the mandate's floor"
    if room_cash < want:
        return room_cash * FILL_ROOM, f"{label}, cut to the cash above the floor"
    return want * FILL_ROOM, label


def trim_amount(
    *, conviction: str, total: float, current: float, cap_pct: float | None = None
) -> tuple[float, str]:
    """(amount to sell in home currency, why): down to the conviction's weight, and never above
    the mandate's cap — a trim of a holding over its cap must end under it."""
    if total <= 0:
        return 0.0, "the portfolio has no value to size against"
    weight = target_weight(conviction, cap_pct)
    extra = current - weight * total
    if extra <= MIN_TRADE * total:
        return 0.0, f"already at {current / total:.1%}, below its {weight:.0%} target"
    return extra, f"down to {weight:.0%} ({conviction} conviction)"


def units(amount: float, price: float, *, fractional: bool) -> float:
    """How many units ``amount`` buys at ``price`` (same currency): whole shares for a stock,
    six decimals for what trades in fractions."""
    if price <= 0 or amount <= 0:
        return 0.0
    raw = amount / price
    return float(math.floor(raw)) if not fractional else math.floor(raw * 1e6) / 1e6

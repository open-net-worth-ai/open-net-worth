"""The action line on "Lucius's take": one call — hold / add / trim / sell for a holding, buy /
wait for a watched ticker — why, and the levels that would change it.

The model picks the action and may suggest a stop, trim or entry price. The user's own thesis
levels always win; a suggested level is kept only on the right side of the price it was made at
and within reach of it. Distances are computed from the live price when the page is read.
"""

from __future__ import annotations

from collections.abc import Callable

HELD_ACTIONS = ("hold", "add", "trim", "sell")
WATCHED_ACTIONS = ("buy", "wait")
_WHY_MAX = 240

# A suggested level outside these bands of the price it was made at is a slip, not a call.
_SUGGESTION_BOUNDS: dict[str, Callable[[float, float], bool]] = {
    "stop": lambda level, price: 0.5 * price <= level < price,
    "trim": lambda level, price: price < level <= 3 * price,
    "entry": lambda level, price: 0.5 * price <= level <= 1.05 * price,
}
# Which thesis field stands for each level — the user's figure, when set, replaces Lucius's.
_THESIS_FIELD = {"stop": "stop_below", "trim": "trim_above", "entry": "buy_below"}


def _levels_for(held: bool) -> tuple[str, ...]:
    return ("stop", "trim") if held else ("entry",)


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def normalize(raw: object, *, held: bool, price: float | None) -> dict | None:
    """The model's verdict as stored on the Asset Brief, or None when unusable: an action from
    the held / watched set, a one-line why, and each suggested level that passes its bound at
    ``price`` (no price → no suggested levels)."""
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action") or "").strip().lower()
    why = " ".join(str(raw.get("why") or "").split())
    if action not in (HELD_ACTIONS if held else WATCHED_ACTIONS) or not why:
        return None
    out: dict = {"action": action, "why": why[:_WHY_MAX], "held": held}
    if price and price > 0:
        out["price_at"] = price
        for kind in _levels_for(held):
            level = _num(raw.get(kind))
            if level and _SUGGESTION_BOUNDS[kind](level, price):
                out[kind] = level
    return out


def compose(
    stored: dict | None,
    thesis: dict | None,
    *,
    held: bool,
    price: float | None,
    currency: str,
) -> dict | None:
    """The verdict the page shows: the stored call plus its levels — the thesis figure when the
    user set one (``source: "thesis"``), else Lucius's suggestion (``"lucius"``) — each with its
    distance from ``price`` in percent (negative = below). None when there is no stored call or
    it was made for the other side of held / watched."""
    if not stored or bool(stored.get("held")) != held:
        return None
    levels = []
    for kind in _levels_for(held):
        mine = _num((thesis or {}).get(_THESIS_FIELD[kind]))
        level, source = (mine, "thesis") if mine else (_num(stored.get(kind)), "lucius")
        if not level:
            continue
        distance = round((level / price - 1) * 100, 1) if price else None
        levels.append({"kind": kind, "price": level, "source": source, "distance_pct": distance})
    return {
        "action": stored["action"],
        "why": stored.get("why") or "",
        "levels": levels,
        "currency": currency,
    }

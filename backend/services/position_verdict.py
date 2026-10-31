"""The action line on "Lucius's take": one call — hold / add / trim / sell for a holding, buy /
wait for a watched ticker — why, and the levels that would change it.

The model picks the action and may suggest a stop, trim or entry price. The user's own thesis
levels always win; a suggested level is kept only on the right side of the price it was made at
and within reach of it. Distances are computed from the live price when the page is read. Each
new take is shown the previous call (``prior_block``) so a level moves on purpose, never by
re-anchoring to whatever today's price is.
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
# A call that moves money comes with the order to place: its side, and a limit within this band
# of the price the call was made at (outside it the order stays, without a limit).
_ORDER_SIDE = {"add": "buy", "buy": "buy", "trim": "sell", "sell": "sell"}
_LIMIT_BAND = (0.8, 1.2)
_ALERT_OPS = {"<=": "≤", "<": "<", ">=": "≥", ">": ">"}


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


def _order(raw: dict, action: str, price: float, held_qty: float | None) -> dict | None:
    """The order behind a trim / sell / add / buy, or None: a positive quantity (a sell never
    more than ``held_qty``) and, when plausible, a limit price."""
    side = _ORDER_SIDE.get(action)
    qty = _num(raw.get("quantity"))
    if not side or not qty:
        return None
    if side == "sell" and (not held_qty or qty > held_qty * (1 + 1e-9)):
        return None
    order: dict = {"side": side, "quantity": qty}
    limit = _num(raw.get("limit"))
    if limit and _LIMIT_BAND[0] * price <= limit <= _LIMIT_BAND[1] * price:
        order["limit"] = limit
    return order


def normalize(
    raw: object, *, held: bool, price: float | None, held_qty: float | None = None
) -> dict | None:
    """The model's verdict as stored on the Asset Brief, or None when unusable: an action from
    the held / watched set, a one-line why, each suggested level that passes its bound at
    ``price``, and the order a trim / sell / add / buy implies (``_order``). No price → no
    suggested levels and no order."""
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
        order = _order(raw, action, price, held_qty)
        if order:
            out["order"] = order
    return out


def prior_block(stored: dict | None, *, held: bool, currency: str = "") -> str:
    """The previous call as prompt context, or "" with none (or one made for the other side of
    held / watched). Only Lucius's own levels — the thesis block already carries the user's."""
    if not stored or bool(stored.get("held")) != held:
        return ""
    unit = f"{currency} " if currency else ""
    when = str(stored.get("at") or "")[:10]
    price_at = _num(stored.get("price_at"))
    meta = ", ".join(x for x in (when, f"price then {unit}{price_at:g}" if price_at else "") if x)
    levels = ", ".join(
        f"{kind} {unit}{stored[kind]:g}" for kind in _levels_for(held) if _num(stored.get(kind))
    )
    lines = [
        f"YOUR PREVIOUS CALL on this {'position' if held else 'ticker'}"
        f"{f' ({meta})' if meta else ''}: {str(stored.get('action', '')).upper()} — "
        f"{stored.get('why') or ''}",
    ]
    if levels:
        lines.append(f"Levels you suggested then: {levels}.")
    order = stored.get("order") or {}
    if order.get("quantity"):
        limit = f" at {unit}{order['limit']:g} limit" if order.get("limit") else ""
        lines.append(f"Order you suggested then: {order['side']} {order['quantity']:g}{limit}.")
    lines.append(
        "Start from this call. If the price has crossed one of those levels, say so plainly. "
        "If you change the action or move a level, say in verdict.why what changed to "
        "justify it — never move a level only because the price moved."
    )
    return "\n".join(lines)


def alerts_block(alerts: list[dict], *, currency: str = "") -> str:
    """The user's price alerts on this ticker as prompt context, or "" with none — so a take
    reuses a level the user already watches instead of suggesting one a hair away from it."""
    unit = f"{currency} " if currency else ""
    shown = []
    for a in alerts:
        value = _num(a.get("value"))
        if (a.get("kind") or "price") != "price" or not value or a.get("op") not in _ALERT_OPS:
            continue
        name = " ".join(str(a.get("name") or "").split())[:60]
        shown.append(f"{_ALERT_OPS[a['op']]} {unit}{value:g}" + (f" ({name})" if name else ""))
    if not shown:
        return ""
    return (
        "YOUR PRICE ALERTS on this ticker: "
        + "; ".join(shown)
        + ". Where one fits your call, use it as the level rather than suggesting a level just "
        "beside it."
    )


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
        "order": stored.get("order") or None,
        "currency": currency,
    }


def call_text(stored: dict | None, thesis: dict | None, *, currency: str = "") -> str:
    """The call as one line for a chat bound to the stock — so a request like "sell 7 at a
    $243 limit, as you suggested" has what it refers to. "" with no call."""
    if not stored or not stored.get("action"):
        return ""
    held = bool(stored.get("held"))
    unit = f"{currency} " if currency else ""
    parts = [f"{str(stored['action']).upper()} — {stored.get('why') or ''}".strip()]
    order = stored.get("order") or {}
    if order.get("quantity"):
        limit = f" at a {unit}{order['limit']:g} limit" if order.get("limit") else " at market"
        parts.append(f"Order: {order['side']} {order['quantity']:g}{limit}.")
    shown = compose(stored, thesis, held=held, price=None, currency=currency) or {}
    levels = [
        f"{kind} {unit}{level['price']:g} ({'your thesis' if level['source'] == 'thesis' else 'suggested'})"
        for level in shown.get("levels", [])
        for kind in [level["kind"]]
    ]
    if levels:
        parts.append("Levels: " + ", ".join(levels) + ".")
    if stored.get("at"):
        parts.append(f"(as of {str(stored['at'])[:10]})")
    return " ".join(parts)

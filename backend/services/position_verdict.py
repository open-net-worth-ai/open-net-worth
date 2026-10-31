"""The call on "Lucius's take": ONE verdict — hold / add / trim / sell for a holding, buy /
wait for a watched ticker — with its reason, plus the ACTIONS Lucius proposes behind it.

Lucius decides everything: the verdict, its stop / trim (or entry) levels — memory for the next
take, never a button — and the actions, each with its own prices: an order (side, quantity,
limit) or a price alert (below / above a price). Usually there are none; doing nothing is the
normal answer, and the prompt asks for at most three. This module only CHECKS what Lucius wrote and drops what can't be right —
total sells beyond the position, a limit far from the price, an alert on the wrong side of it —
never adjusts a number. Each new take is shown the previous call (``prior_block``) so a level
moves on purpose, never by re-anchoring to whatever today's price is.
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
# The only order side each call allows — the chips can never contradict the verdict above them.
_ORDER_SIDE = {"add": "buy", "buy": "buy", "trim": "sell", "sell": "sell"}
# The checks on Lucius's actions: a limit within this band of the price the call was made at, and
# an alert below / above the price no further than these bounds. How many is Lucius's call — the
# prompt asks for at most three; nothing here drops a fourth.
_LIMIT_BAND = (0.8, 1.2)
_ALERT_BAND = (0.5, 3.0)
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


def _order(item: dict, action: str, price: float) -> dict | None:
    """One order Lucius proposed, or None when it can't be placed as written: the side its own
    call implies (``_ORDER_SIDE`` — a hold or wait has no orders), a positive quantity, and a
    limit — when one is given — within ``_LIMIT_BAND`` of the price. No limit means at market."""
    side = str(item.get("side") or "").lower()
    qty = _num(item.get("quantity"))
    if side != _ORDER_SIDE.get(action) or not qty:
        return None
    order: dict = {"type": "order", "side": side, "quantity": qty}
    if item.get("limit") not in (None, ""):
        limit = _num(item.get("limit"))
        if not limit or not _LIMIT_BAND[0] * price <= limit <= _LIMIT_BAND[1] * price:
            return None
        order["limit"] = limit
    return order


def _alert(item: dict, price: float) -> dict | None:
    """One price alert Lucius proposed, or None: below a price under today's, or above one over
    it, within ``_ALERT_BAND``."""
    when = str(item.get("when") or "").lower()
    at = _num(item.get("price"))
    if not at or when not in ("below", "above"):
        return None
    if when == "below" and not _ALERT_BAND[0] * price <= at < price:
        return None
    if when == "above" and not price < at <= _ALERT_BAND[1] * price:
        return None
    return {"type": "alert", "when": when, "price": at}


def _actions(raw: object, *, action: str, price: float, held_qty: float | None) -> list[dict]:
    """Lucius's actions, each checked on its own (``_order`` / ``_alert``). Sells are kept in
    Lucius's order only while their running total stays within ``held_qty`` — a staged trim may
    never add up to more than the position."""
    out: list[dict] = []
    sold = 0.0
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").lower()
        checked = _order(item, action, price) if kind == "order" else None
        if kind == "alert":
            checked = _alert(item, price)
        if checked and checked.get("side") == "sell":
            if not held_qty or sold + checked["quantity"] > held_qty * (1 + 1e-9):
                continue
            sold += checked["quantity"]
        if checked:
            out.append(checked)
    return out


def normalize(
    raw: object, *, held: bool, price: float | None, held_qty: float | None = None
) -> dict | None:
    """The model's verdict as stored on the Asset Brief, or None when unusable: ONE verdict from
    the held / watched set, a one-line why, each level that passes its bound at ``price``, and
    the actions that pass their checks (``_actions``). No price → no levels and no actions."""
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
        actions = _actions(raw.get("actions"), action=action, price=price, held_qty=held_qty)
        if actions:
            out["actions"] = actions
    return out


def stored_actions(stored: dict) -> list[dict]:
    """A stored call's actions — reading a call saved before actions were a list (one ``order``,
    an ``alert`` flag on its levels) as the same list, so an older take still shows its buttons."""
    if isinstance(stored.get("actions"), list):
        return stored["actions"]
    out: list[dict] = []
    order = stored.get("order") or {}
    if order.get("quantity"):
        out.append({"type": "order", **order})
    if stored.get("alert") is True:
        for kind, when in (("stop", "below"), ("entry", "below"), ("trim", "above")):
            if _num(stored.get(kind)):
                out.append({"type": "alert", "when": when, "price": stored[kind]})
    return out


def action_text(item: dict, unit: str = "") -> str:
    """One action as a short phrase: "sell 5 at USD 260 limit", "alert below USD 112"."""
    if item.get("type") == "order":
        limit = f" at {unit}{item['limit']:g} limit" if item.get("limit") else " at market"
        return f"{item['side']} {item['quantity']:g}{limit}"
    return f"alert {item.get('when')} {unit}{item.get('price', 0):g}"


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
    actions = stored_actions(stored)
    if actions:
        lines.append(
            "Actions you suggested then: " + "; ".join(action_text(a, unit) for a in actions) + "."
        )
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
        "beside it, and never propose an alert one of these already covers."
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
        "actions": stored_actions(stored),
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
    actions = stored_actions(stored)
    if actions:
        parts.append("Suggested: " + "; ".join(action_text(a, unit) for a in actions) + ".")
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

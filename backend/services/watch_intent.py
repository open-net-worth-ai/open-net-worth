"""watch_intent — rules from WHY you watch a ticker. Pure: no I/O.

A watch item can carry an ``intent`` (validated at ``db/watchlist_store``): a ``thesis`` line, a
buy zone (``buy_below`` — the price you'd start buying at or under — and an optional floor,
``buy_above``) and a ``fair_value``. These turn "AMD is down 9%" into "AMD just entered the zone
you said you'd buy in", which is the difference between a price alert and an educated one.
"""

from __future__ import annotations

from backend.services import thesis_judgment
from backend.services.watch_findings import finding as _finding

FAIR_VALUE_GAP_PCT = 10.0  # this far under your fair value is worth saying


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if v >= 1000 else f"{v:g}"


def in_buy_zone(price: float | None, intent: dict | None) -> bool | None:
    """True/False once a zone exists (``buy_below`` set); None when there's no zone."""
    if not price or not intent or not intent.get("buy_below"):
        return None
    hi = float(intent["buy_below"])
    lo = float(intent["buy_above"]) if intent.get("buy_above") else None
    return price <= hi and (lo is None or price >= lo)


def zone_label(intent: dict) -> str:
    hi = float(intent["buy_below"])
    if intent.get("buy_above"):
        return f"{_fmt(float(intent['buy_above']))}–{_fmt(hi)}"
    return f"≤ {_fmt(hi)}"


def intent_findings(snap: dict, intent: dict | None) -> list[dict]:
    price, prev = snap.get("price"), snap.get("prev_close")
    out: list[dict] = []
    if not intent or not price:
        return out
    now_in = in_buy_zone(price, intent)
    if now_in:
        was_in = in_buy_zone(prev, intent) if prev else True
        label = zone_label(intent)
        if not was_in:
            out.append(
                _finding(
                    "entered_buy_zone", "good", 45, "medium", f"Entered your buy zone ({label})"
                )
            )
        else:
            out.append(_finding("in_buy_zone", "good", 18, "low", f"In your buy zone ({label})"))
    fair = intent.get("fair_value")
    if fair:
        fair = float(fair)
        gap = (price / fair - 1) * 100
        prev_gap = (prev / fair - 1) * 100 if prev else None
        if gap <= -FAIR_VALUE_GAP_PCT:
            crossed = prev_gap is not None and prev_gap > -FAIR_VALUE_GAP_PCT
            out.append(
                _finding(
                    "crossed_below_fair_value" if crossed else "below_fair_value",
                    "good",
                    35 if crossed else 16,
                    "medium" if crossed else "low",
                    f"{abs(gap):.0f}% below your fair value of {_fmt(fair)}",
                )
            )
        elif gap >= FAIR_VALUE_GAP_PCT:
            out.append(
                _finding(
                    "above_fair_value",
                    "warn",
                    12,
                    "low",
                    f"{gap:.0f}% above your fair value of {_fmt(fair)}",
                )
            )
    return out


def held_thesis_findings(snap: dict, thesis: dict | None) -> list[dict]:
    """A HELD name read against its full thesis: at its stop, at or nearing its trim level, or
    nearing its fair value (``thesis_judgment``'s one stance). A move alone is never one of
    these — that is the point of a thesis. The pulse only; it raises no story or alert."""
    price = snap.get("price")
    j = thesis_judgment.judge(thesis, price, held=True) if price else None
    if not j:
        return []
    lv = j["levels"]
    stance = j["stance"]
    if stance == "below_stop":
        level = lv["stop_below"]["price"]
        return [_finding("at_stop", "bad", 50, "medium", f"At or below your stop of {_fmt(level)}")]
    if stance in ("above_trim", "near_trim"):
        level = lv["trim_above"]
        if stance == "above_trim":
            text = f"At or above your trim level of {_fmt(level['price'])}"
        else:
            text = (
                f"{abs(level['distance_pct']):.0f}% under your trim level of {_fmt(level['price'])}"
            )
        return [_finding(stance, "warn", 40 if stance == "above_trim" else 25, "low", text)]
    if stance == "near_fair_value":
        level = lv["fair_value"]
        text = f"{abs(level['distance_pct']):.0f}% under your fair value of {_fmt(level['price'])}"
        return [_finding("near_fair_value", "warn", 14, "low", text)]
    return []


def thesis_of(item: dict) -> str:
    """The thesis line: the intent's own, else the watch item's older free-text note."""
    intent = item.get("intent") or {}
    return (intent.get("thesis") or item.get("note") or "").strip()

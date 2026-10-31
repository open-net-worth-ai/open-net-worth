"""Thesis judgment — a price move or an entry point read against the user's own investment thesis.

A thesis (``services.investment_theses``) says why the user owns or would buy a name, its role,
what would break it, and the prices that matter: stop, buy zone, fair value, trim. Every surface
that says what to do about a ticker — the Analyst's move/news alerts, price-alert narration, the
Intel scan, the daily briefing, the pulse and Lucius's tools — reads ONE judgment from here,
so a 15% drop on a speculative 3-year holding reads as noise against that horizon everywhere,
never as a sell signal on one surface and a hold on another.

``judge`` is pure: where the price sits against each level, and the stance that follows. Price is
the only thing it can check; ``breaks_if`` is a business fact, so a model reads the news against
it under ``THESIS_RULE``. With no thesis every function here is a no-op, so a ticker without one
behaves exactly as before.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

NEAR_PCT = 5.0  # within this % under trim / fair value is "approaching" — worth flagging

# Instruction appended to every system prompt that may say what to do about a ticker.
THESIS_RULE = (
    "THE USER'S INVESTMENT THESIS: when a ticker has a 'YOUR THESIS' block, judge every price "
    "move and every entry point against it, never against the move alone. Cite it whenever you "
    "say what to do: its role, its reason (including any holding period it states), what would "
    "break it, and where the price sits against the stop, buy zone, fair value and trim. A drop "
    "that leaves the thesis intact (price still above the stop and nothing in 'breaks if' has "
    "happened in the news) is noise against the holding period — say so and NEVER suggest "
    "selling, trimming or cutting it. Suggest selling only when the price is at or below the "
    "stop, or when the news shows the 'breaks if' condition actually happened — then write "
    "'thesis broken:' and name what happened. A rise alone is not a reason to act; reaching or "
    "approaching the trim level or fair value is worth flagging. For a watched ticker, entering "
    "the buy zone is the signal. The levels are the user's own views, never your estimates.\n"
)

_HORIZON_RE = re.compile(
    r"\b(\d+(?:\.\d+)?\s*\+?\s*(?:(?:-|–|to)\s*\d+(?:\.\d+)?\s*\+?\s*)?"
    r"(?:years?|yrs?|months?|decades?)\+?|long[- ]term|for decades|forever)",
    re.IGNORECASE,
)


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.4g}"


def _pct(price: float, level: float) -> float:
    return round((price / level - 1) * 100, 1)


def _clean(text: object) -> str:
    return " ".join(str(text or "").split())


def holding_period(reason: str) -> str:
    """The holding period the reason states ("3+ years", "long-term"), or ""."""
    m = _HORIZON_RE.search(reason or "")
    return " ".join(m.group(1).split()) if m else ""


def _levels(thesis: dict, price: float | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key in ("stop_below", "buy_above", "buy_below", "fair_value", "trim_above"):
        raw = thesis.get(key)
        if raw in (None, ""):
            continue
        level = float(raw)
        out[key] = {"price": level, "distance_pct": _pct(price, level) if price else None}
    return out


def _in_zone(price: float, thesis: dict) -> bool | None:
    hi, lo = thesis.get("buy_below"), thesis.get("buy_above")
    if not hi:
        return None
    return price <= float(hi) and (not lo or price >= float(lo))


def _stance(thesis: dict, price: float | None, held: bool) -> str:
    """The one price-derived stance, most decisive first."""
    if not price:
        return "unpriced"
    stop, trim = thesis.get("stop_below"), thesis.get("trim_above")
    fair, floor = thesis.get("fair_value"), thesis.get("buy_above")
    if held and stop and price <= float(stop):
        return "below_stop"
    if held and trim and price >= float(trim):
        return "above_trim"
    if held and trim and price >= float(trim) * (1 - NEAR_PCT / 100):
        return "near_trim"
    if _in_zone(price, thesis):
        return "in_buy_zone"
    if not held and floor and price < float(floor):
        return "below_buy_zone"
    if fair and price >= float(fair):
        return "above_fair_value"
    if fair and price >= float(fair) * (1 - NEAR_PCT / 100):
        return "near_fair_value"
    if not held and thesis.get("buy_below"):
        return "above_buy_zone"
    return "intact"


def judge(
    thesis: dict | None,
    price: float | None,
    *,
    held: bool,
    currency: str = "",
    ticker: str = "",
) -> dict | None:
    """Where ``price`` sits against ``thesis`` and the stance that follows, or None with no
    thesis. ``sell_ok`` is True only past the stop; ``trim_ok`` only at/above the trim level."""
    if not thesis:
        return None
    price = float(price) if price else None
    reason = _clean(thesis.get("reason"))
    stance = _stance(thesis, price, held)
    j = {
        "ticker": ticker,
        "held": held,
        "currency": (currency or "").upper(),
        "price": price,
        "role": thesis.get("role") or None,
        "reason": reason,
        "holding_period": holding_period(reason),
        "breaks_if": _clean(thesis.get("breaks_if")),
        "levels": _levels(thesis, price),
        "stance": stance,
        "sell_ok": stance == "below_stop",
        "trim_ok": stance == "above_trim",
    }
    j["position"] = position_text(j)
    j["read"] = read_text(j)
    return j


def _money(j: dict, v: float) -> str:
    return f"{j['currency']} {_fmt(v)}".strip()


def position_text(j: dict) -> str:
    """ "US$34 is 70% above your 20 stop, 43% below your 60 trim" — every level, in one line."""
    price, lv = j["price"], j["levels"]
    if not price:
        return "no live price"
    names = {
        "stop_below": "stop",
        "buy_below": "buy-zone top",
        "buy_above": "buy-zone floor",
        "fair_value": "fair value",
        "trim_above": "trim level",
    }
    parts = []
    for key, name in names.items():
        if key not in lv:
            continue
        d = lv[key]["distance_pct"]
        side = "at" if abs(d) < 0.05 else ("above" if d > 0 else "below")
        parts.append(f"{abs(d):.0f}% {side} your {_fmt(lv[key]['price'])} {name}")
    head = f"At {_money(j, price)}"
    return f"{head}: " + ", ".join(parts) if parts else head


_READS = {
    "below_stop": "at or below your stop — the exit line your thesis set",
    "above_trim": "at or above your trim level — where your thesis says to take some off",
    "near_trim": "approaching your trim level",
    "in_buy_zone": "inside your buy zone",
    "below_buy_zone": "under the floor of your buy zone — check what would break the thesis "
    "before buying",
    "above_buy_zone": "above your buy zone — not yet the price you said you'd buy at",
    "above_fair_value": "at or above your fair value",
    "near_fair_value": "approaching your fair value",
    "intact": "between your levels — the thesis is intact on price",
    "unpriced": "unpriced right now",
}


def read_text(j: dict) -> str:
    """What the price position means for the thesis, in the user's terms."""
    read = _READS[j["stance"]]
    if j["stance"] == "intact" and j["held"] and j["holding_period"]:
        read += f"; a move alone is noise against a {j['holding_period']} holding period"
    return read


def cite_line(j: dict) -> str:
    """The thesis, cited in one line: role, reason, breaks-if, and where the price sits."""
    role = f"{j['role']}" if j["role"] else ""
    head = f"Your thesis{f' ({role})' if role else ''}"
    bits = [f'{head}: "{j["reason"]}"' if j["reason"] else head]
    if j["breaks_if"]:
        bits.append(f'breaks if "{j["breaks_if"]}"')
    position = j["position"][:1].lower() + j["position"][1:]
    bits.append(f"{position} — {j['read']}")
    return "; ".join(bits) + "."


def prompt_block(j: dict | None, label: str = "") -> str:
    """The YOUR THESIS block a prompt shows the model, or "" with no thesis."""
    if not j:
        return ""
    name = label or j["ticker"] or "this ticker"
    lines = [f"YOUR THESIS for {name} (the user's own, {'held' if j['held'] else 'watched'}):"]
    if j["role"]:
        lines.append(f"  Role: {j['role']}")
    if j["reason"]:
        lines.append(f"  Reason: {j['reason']}")
    if j["holding_period"]:
        lines.append(f"  Holding period stated: {j['holding_period']}")
    if j["breaks_if"]:
        lines.append(f"  Thesis breaks if: {j['breaks_if']}")
    lines.append(f"  Price vs levels: {j['position']}")
    lines.append(f"  Read: {j['read']}")
    return "\n".join(lines) + "\n"


def thesis_action(j: dict) -> str:
    """The advice the thesis itself gives at this price — what replaces a contradicting one."""
    stance = j["stance"]
    if stance == "below_stop":
        verb = "Review an exit — the price reached your stop"
    elif stance == "above_trim":
        verb = "Consider trimming — the price reached your trim level"
    elif stance in ("near_trim", "near_fair_value", "above_fair_value"):
        verb = "Hold, and watch the level you set"
    elif stance == "in_buy_zone":
        verb = "In your buy zone — the entry your thesis set" if not j["held"] else "Hold"
    elif not j["held"]:
        verb = "Keep watching"
    else:
        verb = "Hold — the thesis is intact"
    return f"{verb}. {cite_line(j)}"


def local_price(inst: dict) -> float | None:
    """The instrument's last price on file — the live cache, else the newest recorded close, else
    its manual mark. No network."""
    from backend.db.price_history_store import PriceHistoryStore
    from backend.services.prices import latest_live_prices

    pid = inst.get("price_source_id") or ""
    if pid:
        live = latest_live_prices().get(pid)
        if live:
            return float(live)
        recorded = PriceHistoryStore().latest_map().get(pid)
        if recorded:
            return float(recorded)
    manual = inst.get("manual_price")
    return float(manual) if manual else None


def for_instrument(
    instrument_id: str | None,
    price: float | None,
    *,
    ticker: str = "",
    currency: str = "",
    held: bool | None = None,
    theses: dict[str, dict] | None = None,
    held_ids: set[str] | None = None,
) -> dict | None:
    """``judge`` for one instrument, reading its thesis (``theses`` / ``held_ids`` let a loop read
    each table once). No ``price`` → the last price on file (``local_price``, no network). None
    with no instrument or no thesis. Never raises — a read failure is "no thesis", so the
    caller's old behaviour stands."""
    if not instrument_id:
        return None
    try:
        from backend.db.instrument_store import InstrumentStore
        from backend.services import investment_theses

        thesis = (
            theses.get(instrument_id)
            if theses is not None
            else investment_theses.thesis_for(instrument_id)
        )
        if not thesis:
            return None
        if held is None:
            ids = held_ids if held_ids is not None else investment_theses.held_instrument_ids()
            held = instrument_id in ids
        if not currency or not ticker or not price:
            inst = InstrumentStore().find_by_id(instrument_id) or {}
            currency = currency or inst.get("quote_currency") or ""
            ticker = ticker or inst.get("price_source_id") or inst.get("symbol") or ""
            price = price or local_price(inst)
    except Exception:
        logger.debug("thesis judgment unavailable for %s", instrument_id, exc_info=True)
        return None
    return judge(thesis, price, held=held, currency=currency, ticker=ticker)


def for_ticker(
    ticker: str, price: float | None, *, held: bool | None = None, currency: str = ""
) -> dict | None:
    """``for_instrument`` for the instrument a ticker names (no network), or None."""
    try:
        from backend.services import investment_theses

        inst = investment_theses.instrument_for_ticker(ticker)
    except Exception:
        logger.debug("thesis judgment: ticker lookup failed for %s", ticker, exc_info=True)
        return None
    if not inst:
        return None
    return for_instrument(inst["id"], price, ticker=ticker, currency=currency, held=held)


def compact(j: dict | None) -> dict | None:
    """The judgment as a tool field: the thesis's words (sanitized — they reach a model), each
    level with its distance from the price, the stance, and the one-line citation."""
    if not j:
        return None
    from backend.core.untrusted import sanitize_untrusted

    keep = ("role", "reason", "holding_period", "breaks_if", "levels", "stance", "position", "read")
    out = {k: j[k] for k in keep}
    for key in ("reason", "holding_period", "breaks_if", "position"):
        out[key] = sanitize_untrusted(out[key] or "")
    out["cite"] = sanitize_untrusted(cite_line(j))
    return out


def book_judgments(
    held_rows: list[dict], watched_items: list[dict] | None = None
) -> dict[str, dict]:
    """ticker → judgment for every held row (``hydrated`` / ``investments_book`` shape: ticker or
    symbol, instrument_id, price, currency) and watched ticker item that has a thesis. Reads the
    theses table once; {} when the book has none. Never hits the network."""
    try:
        from backend.services import investment_theses

        theses = investment_theses.theses_by_instrument()
        if not theses:
            return {}
        held_ids = investment_theses.held_instrument_ids()
        out: dict[str, dict] = {}
        for r in held_rows:
            iid = r.get("instrument_id")
            ticker = (r.get("ticker") or r.get("symbol") or "").strip().upper()
            if iid in theses and ticker and ticker not in out:
                out[ticker] = judge(
                    theses[iid],
                    r.get("price") or None,
                    held=True,
                    currency=r.get("currency") or "",
                    ticker=ticker,
                )
        for item in watched_items or []:
            ticker = (item.get("value") or "").strip().upper()
            iid = investment_theses.thesis_instrument_for_item(item)
            if iid in theses and ticker and ticker not in out:
                j = for_instrument(iid, None, ticker=ticker, held=iid in held_ids, theses=theses)
                if j:
                    out[ticker] = j
        return out
    except Exception:
        logger.debug("thesis judgment: book read failed", exc_info=True)
        return {}


def theses_block(judgments: dict[str, dict], labels: dict[str, str] | None = None) -> str:
    """Every judgment's YOUR THESIS block, then the rule that governs them — "" with none."""
    if not judgments:
        return ""
    labels = labels or {}
    blocks = [prompt_block(j, labels.get(t) or t) for t, j in judgments.items()]
    return "\n".join(blocks) + "\n" + THESIS_RULE

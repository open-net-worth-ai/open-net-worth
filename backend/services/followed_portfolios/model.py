"""The model portfolio a follow shows — derived from its stored snapshots.

Pure: no store, no network. A snapshot holds raw positions (value and a comparable quantity);
the model keeps the positions that have a ticker, takes the top N and renormalises the weights
to sum to 1. "What changed" compares quantities, so a fund's price move alone is not a change.
"""

from __future__ import annotations

import re
from datetime import date

# A 13F older than two quarters, or a member with no report for a year, reads as out of date.
DEFAULT_TOP_N = 20
MAX_TOP_N = 50
STALE_DAYS = {"fund_13f": 200, "house_member": 365}
_CHANGE_TOLERANCE = 0.01
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9\-]{0,11}")


def normalize_symbol(symbol: str) -> str:
    """Upper case, share-class dot as a dash. Anything that is not a plain ticker becomes ""
    — a symbol is copied into a broker order, so it is never free text."""
    cleaned = str(symbol or "").strip().upper().replace(".", "-")
    return cleaned if _SYMBOL.fullmatch(cleaned) else ""


def _merged(snapshot: dict | None) -> dict[str, dict]:
    """Ticker-bearing positions merged per symbol (two share classes can map to one ticker)."""
    out: dict[str, dict] = {}
    for pos in (snapshot or {}).get("positions") or []:
        symbol = normalize_symbol(pos.get("symbol"))
        if not symbol or float(pos.get("value") or 0) <= 0:
            continue
        slot = out.setdefault(
            symbol, {"symbol": symbol, "name": pos.get("name") or symbol, "value": 0.0, "qty": 0.0}
        )
        slot["value"] += float(pos["value"])
        slot["qty"] += float(pos.get("qty") or 0)
    return out


def _top_weights(merged: dict[str, dict], top_n: int) -> dict[str, float]:
    top = sorted(merged.values(), key=lambda p: (-p["value"], p["symbol"]))[:top_n]
    total = sum(p["value"] for p in top)
    return {p["symbol"]: p["value"] / total for p in top} if total > 0 else {}


def _change(symbol: str, now: dict, before: dict[str, dict], has_previous: bool) -> str:
    if not has_previous:
        return "unchanged"
    prior = before.get(symbol)
    if prior is None or prior["qty"] <= 0:
        return "new"
    ratio = now["qty"] / prior["qty"]
    if ratio > 1 + _CHANGE_TOLERANCE:
        return "increased"
    if ratio < 1 - _CHANGE_TOLERANCE:
        return "reduced"
    return "unchanged"


def _age_days(filed_at: str | None, today: date) -> int | None:
    try:
        return max(0, (today - date.fromisoformat(str(filed_at))).days)
    except (TypeError, ValueError):
        return None


def _unmatched(positions: list[dict], total: float, top_n: int) -> list[dict]:
    """Positions with no ticker that are large enough to have made the top N."""
    out, matched = [], 0
    for pos in sorted(positions, key=lambda p: -float(p.get("value") or 0)):
        if matched >= top_n or total <= 0:
            break
        if pos.get("symbol"):
            matched += 1
        else:
            out.append(
                {
                    "name": pos.get("name") or pos.get("key") or "",
                    "weight": float(pos["value"]) / total,
                }
            )
    return out


def _fund_detail(now: dict, prior: dict | None, has_previous: bool) -> dict:
    """What a 13F says about one holding's move: share counts at both quarter-ends and the
    quarter-end price the filing implies (value / shares). It gives no trade date and no trade
    price, so ``change_value`` is the share change AT that quarter-end price — an estimate."""
    shares = now["qty"]
    price = now["value"] / shares if shares > 0 else None
    if not has_previous:
        return {"shares": shares, "price": price}
    before = prior["qty"] if prior else 0.0
    moved = shares - before
    return {
        "shares": shares,
        "price": price,
        "prev_shares": before,
        "shares_change": moved,
        "shares_change_pct": moved / before if before > 0 else None,
        "change_value": moved * price if price else None,
    }


def _last_trades(follow: dict) -> dict[str, dict]:
    """A member's newest counted trade per symbol (``activity`` is stored newest first)."""
    out: dict[str, dict] = {}
    for trade in follow.get("activity") or []:
        symbol = normalize_symbol(trade.get("symbol"))
        if symbol and trade.get("counted") and symbol not in out:
            out[symbol] = {
                k: trade.get(k) for k in ("date", "filed_at", "side", "amount_low", "amount_high")
            }
    return out


def build_model(follow: dict, today: date, top_n: int = DEFAULT_TOP_N) -> dict | None:
    """``{as_of, prev_as_of, filed_at, age_days, estimated, holdings, exited, unmatched,
    unread_reports}`` for a follow, or None when it has no snapshot yet. A fund holding carries
    its share counts and move (``_fund_detail``); a member holding its ``last_trade``."""
    current = follow.get("current")
    if not current:
        return None
    top_n = max(1, min(int(top_n or DEFAULT_TOP_N), MAX_TOP_N))
    previous = follow.get("previous")
    now, before = _merged(current), _merged(previous)
    fund = follow.get("kind") == "fund_13f"
    last_trades = {} if fund else _last_trades(follow)
    weights = _top_weights(now, top_n)
    prev_weights = _top_weights(before, top_n)
    holdings = [
        {
            "symbol": symbol,
            "name": now[symbol]["name"],
            "weight": weight,
            "prev_weight": prev_weights.get(symbol),
            "change": _change(symbol, now[symbol], before, previous is not None),
            **(
                _fund_detail(now[symbol], before.get(symbol), previous is not None)
                if fund
                else {"last_trade": last_trades.get(symbol)}
            ),
        }
        for symbol, weight in weights.items()
    ]
    exited = [
        {
            "symbol": symbol,
            "name": before[symbol]["name"],
            **(
                {"prev_shares": before[symbol]["qty"], "prev_value": before[symbol]["value"]}
                if fund
                else {"last_trade": last_trades.get(symbol)}
            ),
        }
        for symbol in prev_weights
        if symbol not in now
    ]
    total = float(current.get("total_value") or 0) or sum(
        float(p.get("value") or 0) for p in current.get("positions") or []
    )
    unmatched = _unmatched(current.get("positions") or [], total, top_n)
    return {
        "as_of": current.get("as_of"),
        "prev_as_of": (previous or {}).get("as_of"),
        "filed_at": current.get("filed_at"),
        "age_days": _age_days(current.get("filed_at"), today),
        "estimated": follow.get("kind") == "house_member",
        "holdings": holdings,
        "exited": exited,
        "unmatched": unmatched,
        "unread_reports": int(current.get("unread_reports") or 0),
    }


def is_stale(follow: dict, model: dict | None) -> bool:
    age = (model or {}).get("age_days")
    limit = STALE_DAYS.get(follow.get("kind") or "")
    return age is not None and limit is not None and age > limit


def status_of(follow: dict, model: dict | None) -> str:
    """``error`` (last refresh failed) · ``empty`` (nothing readable) · ``stale`` · ``ok``."""
    if follow.get("last_error"):
        return "error"
    if not model or not model["holdings"]:
        return "empty"
    return "stale" if is_stale(follow, model) else "ok"

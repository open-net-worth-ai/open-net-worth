"""What the delivery decider knows about the person (``monitoring.delivery_decider``).

One bounded read of the nickname, the local time, their own words about what Intel should tell
them, the top of the book, what they watch, and what they were already told in the last 48
hours. Best-effort: a piece that cannot be read is left out, never raised — the decision is made
with what is there.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from backend.core.user_time import user_now

logger = logging.getLogger(__name__)

_HOLDINGS = 12
_WATCHING = 40


def _nickname() -> str:
    from backend.services.profile import read_profile

    profile = read_profile()
    name = str(profile.get("nickname") or "").strip()
    return name or str(profile.get("name") or "").strip().split(" ")[0]


def _holdings() -> list[dict]:
    """Largest holdings by share of the invested book, valued by the ledger's one position fold.
    Unvested units and rows with no FX rate are left out, never valued 1:1."""
    from backend.core.errors import MissingFxRateError
    from backend.core.fx import base_currency
    from backend.services import ledger
    from backend.services import positions as positions_service

    base = base_currency().upper()
    by_name: dict[tuple[str, str], float] = {}
    for row in positions_service.hydrated():
        if row.get("vesting_state") == "unvested":
            continue
        try:
            value = float(ledger.row_base_value(row, base) or 0)
        except MissingFxRateError:
            continue
        if value > 0:
            ticker = row.get("ticker") or row.get("symbol") or ""
            key = (ticker, row.get("label") or ticker)
            by_name[key] = by_name.get(key, 0.0) + value
    total = sum(by_name.values()) or 1.0
    ranked = sorted(by_name.items(), key=lambda kv: -kv[1])[:_HOLDINGS]
    return [
        {"ticker": ticker, "label": label, "weight_pct": round(100 * value / total, 1)}
        for (ticker, label), value in ranked
    ]


def _watching() -> list[str]:
    """Everything on their watchlists, tickers and topics alike, as "<value> (<type>)"."""
    from backend.services.watchlist import read_watchlist

    items = read_watchlist().get("items") or []
    return [f"{i.get('value')} ({i.get('type', 'ticker')})" for i in items if i.get("value")][
        :_WATCHING
    ]


def _local(iso: str, zone: Any) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(zone).strftime("%a %d %b %H:%M")
    except ValueError:
        return iso


def _told(zone: Any) -> list[dict]:
    from backend.services import news_delivery

    return [
        {
            "at": _local(row.get("told_at") or "", zone),
            "statement": row.get("message_sent") or row.get("headline") or "",
        }
        for row in news_delivery.recently_told(48)
    ]


def _safely(label: str, fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except Exception as exc:
        logger.warning("delivery: could not read %s: %s", label, type(exc).__name__)
        return default


def gather(state: dict) -> dict:
    """The decider's ``context`` (see ``delivery_prompt``); ``state`` is the roundup bookkeeping."""
    from backend.services.watchlist import read_watchlist

    now = user_now()
    config = _safely("config", lambda: read_watchlist().get("config") or {}, {})
    return {
        "nickname": _safely("profile", _nickname, ""),
        "local_time": now.strftime("%A %d %B %Y, %H:%M (UTC%z)"),
        "instructions": str(config.get("delivery_instructions") or ""),
        "holdings": _safely("holdings", _holdings, []),
        "watching": _safely("watchlist", _watching, []),
        "recently_told": _safely("told items", lambda: _told(now.tzinfo), []),
        "last_roundup_at": state.get("last_roundup_at"),
    }

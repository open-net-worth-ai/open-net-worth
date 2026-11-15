"""Deterministic paper quotes for a QA process only (``ONW_QA_MODE``): no network, a stable
price per symbol, and one market switch the end-to-end run flips (closed → open) to watch a
waiting order fill. Refuses to run outside QA mode."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta, timezone

from backend.core.config import get_settings
from backend.services.paper_trading.model import PaperAsset, PaperQuote

_lock = threading.Lock()
_state: dict = {"market": "open", "prices": {}, "fx": {}, "opens_at": None}


def _assert_qa() -> None:
    if not get_settings().onw_qa_mode:
        raise RuntimeError("QA paper quotes are disabled outside QA mode")


def set_state(
    market: str | None = None,
    prices: dict | None = None,
    fx: dict | None = None,
    opens_in_s: float | None = None,
):
    """``opens_in_s`` closes the market now and opens it that many seconds later, so an
    end-to-end run can place a market order, wait for the open and watch the engine fill it."""
    _assert_qa()
    with _lock:
        if market in ("open", "closed"):
            _state["market"] = market
            _state["opens_at"] = None
        if opens_in_s is not None:
            _state["market"] = "closed"
            _state["opens_at"] = datetime.now(timezone.utc) + timedelta(seconds=float(opens_in_s))
        if prices:
            _state["prices"].update({k.upper(): float(v) for k, v in prices.items() if v})
        if fx:
            _state["fx"].update({k.upper(): float(v) for k, v in fx.items() if v})
        return snapshot()


def snapshot() -> dict:
    opens = _state.get("opens_at")
    return {
        "market": _market(),
        "opens_at": opens.isoformat() if opens else None,
        "prices": dict(_state["prices"]),
        "fx": dict(_state["fx"]),
    }


def _market() -> str:
    opens = _state.get("opens_at")
    if opens is not None and datetime.now(timezone.utc) >= opens:
        _state["market"], _state["opens_at"] = "open", None
    return _state["market"]


def price_for(symbol: str) -> float:
    fixed = _state["prices"].get(symbol.upper())
    if fixed:
        return fixed
    digest = int(hashlib.sha256(symbol.upper().encode()).hexdigest()[:6], 16)
    return round(20 + (digest % 48000) / 100, 2)


def quotes(assets: list[PaperAsset], now: datetime) -> dict[str, PaperQuote]:
    _assert_qa()
    is_open = _market() == "open"
    opens = _state.get("opens_at")
    out: dict[str, PaperQuote] = {}
    for a in assets:
        last = round(price_for(a.symbol) * (a.quote_per_unit or 1.0), 6)
        always = a.kind == "crypto"
        out[a.key] = PaperQuote(
            last=last,
            bid=round(last * 0.9998, 6),
            ask=round(last * 1.0002, 6),
            currency=a.currency,
            as_of=now,
            basis="synthetic"
            if a.kind == "index"
            else ("nav" if a.kind == "fund_nav" else "quote"),
            source="QA fixture",
            session_state="REGULAR" if (is_open or always) else "CLOSED",
            is_open=is_open or always,
            next_open=None if is_open else (opens or now + timedelta(hours=1)),
            timezone="America/New_York",
            navs={now.date().isoformat(): last} if a.kind == "fund_nav" and is_open else {},
        )
    return out


def fx_quote(paid: str, bought: str, now: datetime) -> PaperQuote:
    _assert_qa()
    pair = f"{bought}{paid}"
    rate = _state["fx"].get(pair) or (1.0 if paid == bought else 1.1)
    return PaperQuote(
        last=rate, currency=paid, as_of=now, basis="quote", source="QA fixture", is_open=True
    )

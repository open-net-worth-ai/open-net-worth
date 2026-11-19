"""How a paper portfolio starts: with cash, as a copy of your holdings, or as a followed
portfolio bought with the cash you choose. Everything here is read and priced BEFORE the write
lock; the choke then appends the returned opening events in one transaction.

A copy of your holdings reads ONLY ``services.holdings_snapshot`` (read-only): positions at
today's prices and owned assets at their newest valuation, each bought at that price so P&L
starts at zero. Cash is not copied unless you add it. Option contracts arrive with phase 2 and
are named as left out.
"""

from __future__ import annotations

import logging
from datetime import datetime

from backend.core.errors import ValidationError
from backend.core.fx import base_currency as _base_currency
from backend.core.ids import new_id
from backend.services import holdings_snapshot, instruments, risk_metrics
from backend.services.followed_portfolios import service as followed
from backend.services.paper_trading import assets, quotes

logger = logging.getLogger(__name__)

DEFAULT_CASH = 10_000.0
MAX_FOLLOWED_LINES = 40


def base_currency() -> str:
    return _base_currency()


def default_benchmark() -> str:
    return risk_metrics.preferred_benchmark()


def _round_nice(x: float) -> float:
    """240,123 → 240,000: a starting amount reads as a round number."""
    if x <= 0:
        return 0.0
    step = 10 ** max(0, len(str(int(x))) - 2)
    return float(round(x / step) * step)


def default_cash() -> float:
    """The same as your investments (rounded) when you hold any, else 10,000."""
    home = base_currency()
    rows = [r for r in holdings_snapshot.snapshot()["holdings"] if r["source"] == "position"]
    rates = quotes.fx_rates({r["currency"] for r in rows}, home) if rows else {}
    total = 0.0
    for r in rows:
        rate = rates.get(r["currency"])
        if r.get("price") and rate:
            total += (
                float(r["quantity"])
                * float(r["price"])
                * float(r.get("quote_per_unit") or 1)
                * rate
            )
    return _round_nice(total) or DEFAULT_CASH


def _deposit(amount: float, ccy: str, at: str, *, opening: bool, key: str) -> dict:
    return {
        "kind": "deposit",
        "at": at,
        "cash_delta": {ccy: round(float(amount), 2)},
        "payload": {"opening": opening},
        "idem_key": key,
    }


def _opening_buy(asset_key: str, qty: float, price: float, ccy: str, at: str, i: int) -> list[dict]:
    """An opening holding: the cash that bought it arrives, then is spent — so the run's flows
    say what it started with, and its P&L starts at zero."""
    cost = round(qty * price, 8)
    return [
        _deposit(cost, ccy, at, opening=True, key=f"open:fund:{i}"),
        {
            "kind": "buy",
            "at": at,
            "asset_key": asset_key,
            "quantity_delta": qty,
            "cash_delta": {ccy: -cost},
            "price": price,
            "price_basis": "quote",
            "payload": {"currency": ccy, "commission": 0.0, "opening": True},
            "idem_key": f"open:buy:{i}",
        },
    ]


def _cash_amount(change: dict) -> float:
    raw = change.get("cash")
    if raw in (None, ""):
        return DEFAULT_CASH if change.get("start_with", "cash") == "cash" else 0.0
    try:
        amount = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Starting cash must be a number.") from exc
    if amount < 0 or amount > 1e12:
        raise ValidationError("Starting cash must be between 0 and 1,000,000,000,000.")
    return amount


def _from_holdings(home: str, at: str) -> tuple[list[dict], list[dict], list[str]]:
    snap = holdings_snapshot.snapshot()
    events: list[dict] = []
    contracts: list[dict] = []
    for i, h in enumerate(snap["holdings"]):
        price = h.get("price")
        if not price:
            continue
        if h["source"] == "position":
            qpu = float(h.get("quote_per_unit") or 1.0)
            events += _opening_buy(
                f"inst:{h['instrument_id']}",
                float(h["quantity"]),
                float(price) * qpu,
                h["currency"],
                at,
                i,
            )
        else:
            ctr = {
                "id": new_id("pctr"),
                "kind": "manual",
                "name": h["name"][:80],
                "currency": h.get("currency") or home,
            }
            contracts.append(ctr)
            key = f"ctr:{ctr['id']}"
            events.append(
                {
                    "kind": "mark",
                    "at": at,
                    "asset_key": key,
                    "price": float(price),
                    "price_basis": "yours",
                    "payload": {"opening": True},
                    "idem_key": f"open:mark:{i}",
                }
            )
            events += _opening_buy(key, 1.0, float(price), ctr["currency"], at, i)
            events[-1]["price_basis"] = "yours"
    left_out = [o["name"] for o in snap["options"]]
    return events, contracts, left_out


def _from_followed(followed_id: str, cash: float, home: str, at: str, now: datetime):
    if cash <= 0:
        raise ValidationError("Choose how much cash buys the followed portfolio.")
    row = followed.resolve(followed_id)
    model = followed.view(row, top_n=MAX_FOLLOWED_LINES).get("model") or {}
    lines = [h for h in model.get("holdings") or [] if h.get("symbol") and h.get("weight")]
    if not lines:
        raise ValidationError("That followed portfolio has no holdings to copy yet.")
    events = [_deposit(cash, home, at, opening=True, key="open:deposit")]
    resolved = []
    for h in lines:
        try:
            inst, _ = instruments.resolve_instrument(str(h["symbol"]), allow_duplicate_symbol=True)
        except Exception:
            inst = None
        if inst:
            resolved.append((assets.from_instrument(inst), float(h["weight"])))
    got = quotes.fetch([a for a, _w in resolved], now)
    rates = quotes.fx_rates({a.currency for a, _w in resolved}, home)
    for i, (a, w) in enumerate(resolved):
        q, rate = got.get(a.key), rates.get(a.currency)
        if not q or not q.last or not rate:
            continue
        spend_home = cash * w
        qty = round(spend_home / rate / float(q.last), 6)
        if qty <= 0:
            continue
        cost_native = round(qty * float(q.last), 8)
        if a.currency != home:
            events.append(
                {
                    "kind": "convert",
                    "at": at,
                    "asset_key": f"ccy:{a.currency}",
                    "cash_delta": {home: -round(cost_native * rate, 8), a.currency: cost_native},
                    "price": rate,
                    "price_basis": "quote",
                    "payload": {"from": home, "to": a.currency, "rate": rate, "opening": True},
                    "idem_key": f"open:convert:{i}",
                }
            )
        events.append(
            {
                "kind": "buy",
                "at": at,
                "asset_key": a.key,
                "quantity_delta": qty,
                "cash_delta": {a.currency: -cost_native},
                "price": float(q.last),
                "price_basis": q.basis,
                "fx_rate": rate,
                "payload": {"currency": a.currency, "commission": 0.0, "opening": True},
                "idem_key": f"open:buy:{i}",
            }
        )
    return events, row["name"]


def opening(change: dict, home: str, now: datetime) -> dict:
    """{"label", "events", "contracts"} for the start the change asks for."""
    start = str(change.get("start_with") or "cash")
    at = now.isoformat()
    cash = _cash_amount(change)
    if start == "cash":
        return {
            "label": "Cash",
            "events": [_deposit(cash, home, at, opening=True, key="open:deposit")] if cash else [],
            "contracts": [],
        }
    if start == "holdings":
        events, contracts, left_out = _from_holdings(home, at)
        if cash:
            events.insert(0, _deposit(cash, home, at, opening=True, key="open:deposit"))
        if not events:
            raise ValidationError("You have no priced holdings to copy yet.")
        return {
            "label": "Copy of my holdings",
            "events": events,
            "contracts": contracts,
            "left_out": left_out,
        }
    if start == "followed":
        events, name = _from_followed(str(change.get("followed_id") or ""), cash, home, at, now)
        return {"label": f"Copies {name}", "events": events, "contracts": []}
    raise ValidationError("Start with cash, holdings or followed.")

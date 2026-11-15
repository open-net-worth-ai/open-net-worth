"""Valuing a paper book in its home currency — each holding through its kind's own ``value``,
then the live FX rate. Also the value per pricing basis (quote, nav, synthetic, yours), so a
screen can say how much of the figure is an estimate. Reads quotes; writes nothing."""

from __future__ import annotations

from datetime import datetime

from backend.services.paper_trading import assets, quotes
from backend.services.paper_trading.book import Book
from backend.services.paper_trading.kinds import kind_for
from backend.services.paper_trading.model import BASIS_LABEL, KIND_LABEL, KIND_ORDER


def holdings(
    row: dict, book: Book, now: datetime, *, fetch_quotes: bool = True
) -> tuple[list[dict], dict[str, float]]:
    """(holding rows, FX rates used). Without quotes a holding is valued at its cost."""
    home = row["home_currency"]
    held = [assets.resolve_key(k) for k in book.held_keys()]
    got = quotes.fetch(held, now, marks=book.marks) if fetch_quotes else {}
    rates = (
        quotes.fx_rates({a.currency for a in held} | set(book.cash), home)
        if fetch_quotes
        else {home: 1.0}
    )
    rows: list[dict] = []
    for a in held:
        q = got.get(a.key)
        qty = book.quantity(a.key)
        cost = book.cost(a.key)
        native = kind_for(a).value(qty, q) if q else None
        rate = rates.get(a.currency)
        value_home = native * rate if native is not None and rate else None
        cost_home = book.cost_home(a.key, rate or (1.0 if a.currency == home else None))
        if value_home is None and not fetch_quotes and a.currency == home:
            value_home = cost
        basis = q.basis if q else kind_for(a).basis
        mark = book.marks.get(a.key)
        rows.append(
            {
                **assets.describe(a),
                "quantity": qty,
                "price": (native / qty) if native is not None and qty else None,
                "price_currency": a.currency,
                "basis": basis,
                "basis_label": BASIS_LABEL.get(basis),
                "quote_as_of": q.as_of.isoformat() if q and q.as_of else None,
                "value_home": round(value_home, 2) if value_home is not None else None,
                "cost_home": round(cost_home, 2) if cost_home is not None else None,
                "pnl_home": round(value_home - cost_home, 2)
                if value_home is not None and cost_home is not None
                else None,
                "pnl_pct": round((value_home - cost_home) / cost_home, 6)
                if value_home is not None and cost_home
                else None,
                "frozen": book.frozen.get(a.key),
                "marked_on": (mark or {}).get("at", "")[:10] or None,
            }
        )
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    rows.sort(key=lambda r: (order.get(r["kind"], 99), -(r["value_home"] or 0)))
    return rows, rates


def cash_rows(row: dict, book: Book, rates: dict[str, float]) -> list[dict]:
    home = row["home_currency"]
    out = []
    for ccy, amt in sorted(book.cash.items(), key=lambda kv: (kv[0] != home, kv[0])):
        if abs(amt) < 0.005 and ccy != home:
            continue
        rate = rates.get(ccy)
        out.append(
            {
                "currency": ccy,
                "amount": round(amt, 2),
                "home": round(amt * rate, 2) if rate else None,
            }
        )
    return out


def value(row: dict, book: Book, now: datetime, *, fetch_quotes: bool = True) -> dict:
    hold, rates = holdings(row, book, now, fetch_quotes=fetch_quotes)
    cash = cash_rows(row, book, rates)
    cash_home = round(sum(c["home"] or 0.0 for c in cash), 2)
    priced = [h for h in hold if h["value_home"] is not None]
    by_basis: dict[str, float] = {}
    for h in priced:
        by_basis[h["basis"]] = round(by_basis.get(h["basis"], 0.0) + h["value_home"], 2)
    invested = round(sum(h["value_home"] for h in priced), 2)
    total = round(invested + cash_home, 2)
    for h in hold:
        h["weight"] = round(h["value_home"] / total, 6) if h["value_home"] and total else None
    unrealized = round(sum(h["pnl_home"] or 0.0 for h in hold), 2)
    realized = 0.0
    for ccy, gain in book.realized_by_currency().items():
        rate = rates.get(ccy)
        realized += gain * rate if rate else 0.0
    return {
        "holdings": hold,
        "cash": cash,
        "cash_home": cash_home,
        "invested_home": invested,
        "value_home": total,
        "by_basis": by_basis,
        "unrealized_home": unrealized,
        "realized_home": round(realized, 2),
        "unpriced": [h["asset_key"] for h in hold if h["value_home"] is None],
        "rates": rates,
    }


def flow_amount_home(flow: dict, home: str, rates: dict[str, float] | None) -> float:
    """One deposit or withdrawal in the home currency; a foreign leg at ``rates`` (price of one
    unit in home). A leg with no rate is left out rather than counted 1:1."""
    total = 0.0
    for ccy, amount in (flow.get("cash_delta") or {}).items():
        rate = 1.0 if ccy == home else (rates or {}).get(ccy)
        if rate:
            total += float(amount) * float(rate)
    return total


def flows_home(
    row: dict, book: Book, *, since: str | None = None, rates: dict[str, float] | None = None
) -> float:
    """Net deposits less withdrawals in home currency (on or after ``since``)."""
    home = row["home_currency"]
    total = 0.0
    for f in book.flows:
        if since and str(f.get("at") or "")[:10] < since:
            continue
        total += flow_amount_home(f, home, rates)
    return round(total, 2)


def kind_groups(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["kind"], []).append(r)
    return [
        {"kind": k, "label": KIND_LABEL.get(k, k), "holdings": groups[k]}
        for k in KIND_ORDER
        if k in groups
    ]

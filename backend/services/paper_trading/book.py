"""The paper book, derived — pure. ``derive(events)`` folds a portfolio's append-only event
ledger into lots per asset, cash per currency, realised P&L, frozen holdings and manual marks.
Nothing is stored that this cannot rebuild.

Lots have the real book's shape and are consumed by the real book's own math
(``services.lot_math``: FIFO selection and the per-line realised gain), so a paper sale and a
real one can never compute a gain differently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services import lot_math

_EPS = 1e-9


@dataclass
class Book:
    lots: dict[str, list[dict]] = field(default_factory=dict)
    cash: dict[str, float] = field(default_factory=dict)
    realized: list[dict] = field(default_factory=list)
    frozen: dict[str, str] = field(default_factory=dict)
    marks: dict[str, dict] = field(default_factory=dict)
    flows: list[dict] = field(default_factory=list)
    income: list[dict] = field(default_factory=list)

    def quantity(self, asset_key: str) -> float:
        q = sum(float(lot["remaining_qty"]) for lot in self.lots.get(asset_key, []))
        return 0.0 if abs(q) < _EPS else q

    def held_keys(self) -> list[str]:
        return [k for k in self.lots if self.quantity(k) > 0]

    def cost(self, asset_key: str) -> float:
        """Remaining cost of a holding (fees included), in its cost currency."""
        total = 0.0
        for lot in self.lots.get(asset_key, []):
            unit = lot_math.effective_unit_cost(lot) or 0.0
            total += unit * float(lot["remaining_qty"])
        return total

    def realized_by_currency(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for r in self.realized:
            if r.get("gain") is not None:
                out[r["currency"]] = out.get(r["currency"], 0.0) + float(r["gain"])
        return out


def _add_cash(book: Book, delta: dict | None) -> None:
    for ccy, amount in (delta or {}).items():
        book.cash[ccy] = round(book.cash.get(ccy, 0.0) + float(amount), 8)


def _buy(book: Book, ev: dict) -> None:
    qty = float(ev.get("quantity_delta") or 0.0)
    if qty <= 0:
        return
    payload = ev.get("payload") or {}
    book.lots.setdefault(ev["asset_key"], []).append(
        {
            "id": ev["id"],
            "acquired_at": (ev.get("at") or "")[:10],
            "original_qty": qty,
            "remaining_qty": qty,
            "unit_cost": float(ev.get("price") or 0.0),
            "fees": float(payload.get("commission") or 0.0),
            "cost_currency": payload.get("currency") or "",
        }
    )


def _sell(book: Book, ev: dict) -> None:
    key = ev["asset_key"]
    qty = -float(ev.get("quantity_delta") or 0.0)
    if qty <= 0:
        return
    payload = ev.get("payload") or {}
    ccy = payload.get("currency") or ""
    open_lots = [lot for lot in book.lots.get(key, []) if float(lot["remaining_qty"]) > _EPS]
    plan = lot_math.select_lots_fifo(open_lots, qty)
    fee_per_unit = float(payload.get("commission") or 0.0) / qty
    gain = 0.0
    for lot, take in plan:
        line = lot_math.line_realized_gain(
            take,
            float(ev.get("price") or 0.0),
            lot,
            ccy,
            {ccy: 1.0, (lot.get("cost_currency") or ccy): 1.0},
            disposed_at=(ev.get("at") or "")[:10],
            disposal_fee_per_unit=fee_per_unit,
        )
        gain += float(line["realized_gain"] or 0.0)
        lot["remaining_qty"] = max(0.0, float(lot["remaining_qty"]) - take)
    book.realized.append(
        {
            "asset_key": key,
            "at": ev.get("at"),
            "quantity": qty,
            "gain": round(gain, 2),
            "currency": ccy,
            "order_id": ev.get("order_id"),
            "event_id": ev["id"],
        }
    )


def _split(book: Book, ev: dict) -> None:
    ratio = float((ev.get("payload") or {}).get("ratio") or 0.0)
    if ratio <= 0:
        return
    before = (ev.get("payload") or {}).get("before")
    for lot in book.lots.get(ev["asset_key"], []):
        # A lot bought on or after the split's effective date was bought at the new price.
        if before and str(lot.get("acquired_at") or "") >= str(before):
            continue
        lot["original_qty"] = float(lot["original_qty"]) * ratio
        lot["remaining_qty"] = float(lot["remaining_qty"]) * ratio
        if lot.get("unit_cost") is not None:
            lot["unit_cost"] = float(lot["unit_cost"]) / ratio


def _freeze(book: Book, ev: dict) -> None:
    payload = ev.get("payload") or {}
    if payload.get("frozen", True):
        book.frozen[ev["asset_key"]] = payload.get("reason") or "Frozen"
    else:
        book.frozen.pop(ev["asset_key"], None)


def _mark(book: Book, ev: dict) -> None:
    book.marks[ev["asset_key"]] = {
        "price": float(ev.get("price") or 0.0),
        "at": ev.get("at"),
        "proxy_price": (ev.get("payload") or {}).get("proxy_price"),
    }


def apply(book: Book, ev: dict) -> None:
    """Fold one event into the book."""
    kind = ev.get("kind")
    _add_cash(book, ev.get("cash_delta"))
    if kind in ("deposit", "withdraw"):
        book.flows.append({"at": ev.get("at"), "cash_delta": ev.get("cash_delta") or {}})
    elif kind == "buy":
        _buy(book, ev)
    elif kind == "sell":
        _sell(book, ev)
    elif kind == "split":
        _split(book, ev)
    elif kind == "freeze":
        _freeze(book, ev)
    elif kind == "mark":
        _mark(book, ev)
    elif kind in ("dividend", "distribution"):
        book.income.append(ev)


def derive(events: list[dict]) -> Book:
    """The book after every event, in ledger order."""
    book = Book()
    for ev in events:
        apply(book, ev)
    return book

"""Planning one paper order — pure. ``plan`` takes the portfolio, its derived book, the asset,
the request and the quotes, and returns the decision (fill, wait or reject) with the exact
events a fill writes. The ticket's preview and the real placement call the same function on
the same quote, so a preview always shows what placing would do (the contract
``ledger.resolve_transaction_row`` keeps for the real ledger).

Cover: without margin a buy spends only FREE cash — cash less what open orders reserve — and a
sell is capped at what is held less what open sells already promise. A buy abroad converts the
shortfall from the home currency at the live rate plus the FX spread when automatic conversion
is on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from backend.core.errors import ValidationError
from backend.services.paper_trading.book import Book
from backend.services.paper_trading.kinds import kind_for
from backend.services.paper_trading.kinds.base import fmt_price
from backend.services.paper_trading.model import (
    BASIS_LABEL,
    NOTE_MAX,
    ORDER_TYPES,
    TIFS,
    Fill,
    PaperAsset,
    PaperQuote,
    Reject,
    Session,
    Wait,
)

_EPS = 1e-9
# A real order above this share of average daily volume would likely move the price.
ADV_WARN_SHARE = 0.05


@dataclass
class Plan:
    decision: Fill | Wait | Reject
    session: Session
    quantity: float | None = None
    gross: float = 0.0
    commission: float = 0.0
    total: float = 0.0
    currency: str = ""
    convert: dict | None = None  # {"from": home, "amount": x, "receive": y, "rate": r}
    reserve: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    next_line: str = ""


def normalize(req: dict, asset: PaperAsset, home: str) -> dict:
    """A clean order request, or ValidationError naming the first thing wrong with it."""
    side = str(req.get("side") or "").lower()
    if side not in ("buy", "sell"):
        raise ValidationError("Choose buy or sell.")
    otype = str(req.get("order_type") or "market").lower()
    if asset.kind == "fund_nav":
        otype = "nav"
    elif asset.kind in ("fx", "manual"):
        otype = "market"
    if otype not in ORDER_TYPES:
        raise ValidationError(f"Order type is one of: {', '.join(ORDER_TYPES[:3])}.")
    tif = str(req.get("tif") or "day").lower()
    if tif not in TIFS:
        raise ValidationError("Good for is 'day' or 'gtc'.")
    qty = _positive(req.get("quantity"), "Quantity")
    notional = _positive(req.get("notional"), "Amount")
    if asset.kind == "fx":
        qty = None
        if notional is None:
            raise ValidationError("Enter the amount to convert.")
    elif (qty is None) == (notional is None):
        raise ValidationError("Enter a quantity or an amount, not both.")
    limit = _positive(req.get("limit_price"), "Limit price")
    stop = _positive(req.get("stop_price"), "Stop price")
    if otype == "limit" and limit is None:
        raise ValidationError("A limit order needs a limit price.")
    if otype == "stop" and stop is None:
        raise ValidationError("A stop order needs a stop price.")
    note = str(req.get("note") or "").strip()
    if len(note) > NOTE_MAX:
        raise ValidationError(f"The note is at most {NOTE_MAX} characters.")
    from_ccy = str(req.get("from_currency") or home).upper()
    if asset.kind == "fx" and from_ccy == asset.currency:
        raise ValidationError("Convert between two different currencies.")
    return {
        "side": "buy" if asset.kind == "fx" else side,
        "order_type": otype,
        "tif": tif,
        "quantity": qty,
        "notional": notional,
        "limit_price": limit if otype == "limit" else None,
        "stop_price": stop if otype == "stop" else None,
        "extended_ok": bool(req.get("extended_ok")),
        "note": note or None,
        "from_currency": from_ccy if asset.kind == "fx" else None,
        "price": _positive(req.get("price"), "Price") if asset.kind == "manual" else None,
    }


def _positive(value, label: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be a number.") from exc
    if not math.isfinite(n) or n <= 0:
        raise ValidationError(f"{label} must be more than zero.")
    return n


def commission_for(asset: PaperAsset, gross: float, settings: dict) -> float:
    if asset.kind in ("index", "fx"):
        return 0.0
    c = settings.get("commission") or {}
    return round(float(c.get("fixed") or 0.0) + gross * float(c.get("pct") or 0.0) / 100.0, 8)


def reserved(open_orders: list[dict], exclude_id: str | None = None) -> tuple[dict, dict]:
    """(cash reserved per currency, quantity promised per asset) by the other open orders."""
    cash: dict[str, float] = {}
    qty: dict[str, float] = {}
    for o in open_orders:
        if o.get("id") == exclude_id or o.get("status") != "open":
            continue
        for ccy, amt in (o.get("reserve") or {}).items():
            cash[ccy] = cash.get(ccy, 0.0) + float(amt)
        if o.get("side") == "sell" and o.get("asset_key"):
            qty[o["asset_key"]] = qty.get(o["asset_key"], 0.0) + float(o.get("quantity") or 0.0)
    return cash, qty


def free_cash(book: Book, reserves: dict[str, float], ccy: str) -> float:
    return round(book.cash.get(ccy, 0.0) - reserves.get(ccy, 0.0), 8)


def _size(order: dict, price: float, settings: dict, asset: PaperAsset) -> float:
    if order.get("quantity") is not None:
        qty = float(order["quantity"])
    else:
        qty = float(order["notional"]) / price if price > 0 else 0.0
    decimals = kind_for(asset).quantity_decimals
    if asset.kind == "listed" and not settings.get("fractional", True):
        return float(math.floor(qty + _EPS))
    return math.floor(qty * 10**decimals + _EPS) / 10**decimals


def plan(
    *,
    portfolio: dict,
    settings: dict,
    book: Book,
    open_orders: list[dict],
    asset: PaperAsset,
    order: dict,
    quote: PaperQuote | None,
    fx_quote: PaperQuote | None,
    now: datetime,
) -> Plan:
    """Decide one order against the book as it stands. ``order`` is a normalized request (or a
    stored open order being re-tried); ``fx_quote`` prices the home currency in the asset's
    currency (an fx order: the currency paid for the one bought)."""
    adapter = kind_for(asset)
    if asset.kind != "fx":
        asset = PaperAsset(
            **{**asset.__dict__, "extra": {**asset.extra, **_extra(order, settings)}}
        )
    session = adapter.session(asset, fx_quote if asset.kind == "fx" else quote, now)
    if asset.key in book.frozen:
        return _rejected(session, f"Frozen: {book.frozen[asset.key]}. Settle it before trading.")
    if asset.kind == "fx":
        decision = adapter.decide(order, fx_quote, session, settings, now)
    else:
        decision = adapter.decide(order, quote, session, settings, now)
    if isinstance(decision, Reject):
        return _rejected(session, decision.reason, decision.available)
    home = portfolio["home_currency"]
    cash_res, qty_res = reserved(open_orders, exclude_id=order.get("id"))
    if asset.kind == "fx":
        return _plan_fx(order, decision, session, book, cash_res, asset, adapter)
    ref = decision.price if isinstance(decision, Fill) else _reference_price(order, quote)
    if ref is None:
        return Plan(decision=decision, session=session, next_line=adapter.next_line(decision))
    qty = _size(order, ref, settings, asset)
    if qty <= 0:
        return _rejected(session, "That amount buys less than the smallest quantity.")
    gross = round(qty * ref, 8)
    commission = commission_for(asset, gross, settings)
    p = Plan(
        decision=decision,
        session=session,
        quantity=qty,
        gross=gross,
        currency=asset.currency,
        commission=commission,
        next_line=adapter.next_line(decision),
    )
    if order["side"] == "sell":
        return _cover_sell(p, book, qty_res, asset, qty)
    p.total = round(gross + commission, 8)
    _volume_warning(p, quote, qty)
    return _cover_buy(p, book, cash_res, asset, home, settings, fx_quote)


def _extra(order: dict, settings: dict) -> dict:
    """Pre/post-market fills: allowed by the order itself or by the portfolio's setting."""
    return {"extended_ok": bool(order.get("extended_ok") or settings.get("extended_hours"))}


def _reference_price(order: dict, quote: PaperQuote | None) -> float | None:
    if order.get("order_type") == "limit" and order.get("limit_price"):
        return float(order["limit_price"])
    if order.get("order_type") == "stop" and order.get("stop_price"):
        return float(order["stop_price"])
    if quote is not None and quote.last:
        return float(quote.last)
    return None


def _rejected(session: Session, reason: str, available: float | None = None) -> Plan:
    return Plan(decision=Reject(reason, available), session=session, next_line=reason)


def _cover_sell(p: Plan, book: Book, qty_res: dict, asset: PaperAsset, qty: float) -> Plan:
    available = round(book.quantity(asset.key) - qty_res.get(asset.key, 0.0), 8)
    if qty - available > _EPS:
        reason = f"You hold {available:g} to sell." if available > 0 else "You hold none to sell."
        return _rejected(p.session, reason, max(available, 0.0))
    p.total = round(p.gross - p.commission, 8)
    if isinstance(p.decision, Wait):
        p.reserve = {}
    return p


def _cover_buy(
    p: Plan,
    book: Book,
    cash_res: dict,
    asset: PaperAsset,
    home: str,
    settings: dict,
    fx_quote: PaperQuote | None,
) -> Plan:
    ccy = asset.currency
    have = free_cash(book, cash_res, ccy)
    need = p.total
    if need - have <= _EPS:
        p.reserve = {ccy: need} if isinstance(p.decision, Wait) else {}
        return p
    if ccy == home or not settings.get("auto_convert", True):
        return _rejected(
            p.session,
            f"Not enough free cash: {max(have, 0):,.2f} {ccy} available, this needs {need:,.2f}.",
            max(have, 0.0),
        )
    if fx_quote is None or not fx_quote.last:
        return _rejected(p.session, f"No {home}/{ccy} rate to convert with right now.")
    shortfall = need - max(have, 0.0)
    # fx_quote.last = price of one unit of the asset currency in home; pay the spread on top.
    rate = float(fx_quote.last) * (1 + float(settings.get("fx_spread_pct") or 0.0) / 100.0)
    home_needed = round(shortfall * rate, 8)
    home_free = free_cash(book, cash_res, home)
    if home_needed - home_free > _EPS:
        return _rejected(
            p.session,
            f"Not enough free cash: {max(home_free, 0):,.2f} {home} available, this needs "
            f"about {home_needed:,.2f} {home}.",
            max(home_free, 0.0),
        )
    p.convert = {"from": home, "amount": home_needed, "receive": round(shortfall, 8), "rate": rate}
    if isinstance(p.decision, Wait):
        p.reserve = {k: v for k, v in {home: home_needed, ccy: max(have, 0.0)}.items() if v > 0}
        p.convert = None
    p.warnings.append(
        f"Converts about {home_needed:,.2f} {home} to {ccy} at {rate:.6g}, spread included."
    )
    return p


def _plan_fx(order, decision, session, book, cash_res, asset, adapter) -> Plan:
    paid = order["from_currency"]
    amount = float(order["notional"])
    have = free_cash(book, cash_res, paid)
    if amount - have > _EPS:
        return _rejected(
            session, f"Not enough {paid}: {max(have, 0):,.2f} available.", max(have, 0.0)
        )
    p = Plan(
        decision=decision,
        session=session,
        currency=paid,
        gross=amount,
        total=amount,
        next_line=adapter.next_line(decision),
    )
    if isinstance(decision, Fill):
        p.quantity = round(amount / decision.price, 8)
    else:
        p.reserve = {paid: amount}
    return p


def _volume_warning(p: Plan, quote: PaperQuote | None, qty: float) -> None:
    adv = quote.avg_volume if quote else None
    if adv and qty > ADV_WARN_SHARE * float(adv):
        p.warnings.append("A real order this size would likely move the price.")


def fill_events(
    *,
    order: dict,
    asset: PaperAsset,
    p: Plan,
    now: datetime,
    home_rate: float | None,
    thesis: dict | None = None,
) -> tuple[dict, list[dict]]:
    """(the fill record for the order, the events a fill appends), from a Plan whose decision is
    a Fill. Pure: ids and sequence are given by the writer."""
    d = p.decision
    assert isinstance(d, Fill)
    at = now.isoformat()
    fill = {
        "price": d.price,
        "quantity": p.quantity,
        "gross": round(p.gross, 2),
        "commission": round(p.commission, 2),
        "total": round(p.total, 2),
        "currency": p.currency,
        "basis": d.basis,
        "basis_label": BASIS_LABEL.get(d.basis),
        "source": d.source,
        "quote_as_of": d.quote_as_of.isoformat() if d.quote_as_of else None,
        "session": d.session,
        "slippage": d.slippage,
        "spread_or_premium": d.spread_or_premium,
        "nav_date": d.nav_date,
        "fx_rate": home_rate,
        "filled_at": at,
        "thesis": thesis,
    }
    events: list[dict] = []
    if asset.kind == "fx":
        events.append(
            _event(
                "convert",
                at,
                asset.key,
                0.0,
                {order["from_currency"]: -p.total, asset.currency: p.quantity},
                d.price,
                d.basis,
                order,
                home_rate,
                {"from": order["from_currency"], "to": asset.currency, "rate": d.price},
            )
        )
        return fill, events
    if p.convert:
        c = p.convert
        events.append(
            _event(
                "convert",
                at,
                f"ccy:{asset.currency}",
                0.0,
                {c["from"]: -c["amount"], asset.currency: c["receive"]},
                c["rate"],
                "quote",
                order,
                None,
                {"from": c["from"], "to": asset.currency, "rate": c["rate"], "auto": True},
            )
        )
    if asset.kind == "manual":
        events.append(_event("mark", at, asset.key, 0.0, {}, d.price, "yours", order, None, {}))
    sign = 1.0 if order["side"] == "buy" else -1.0
    cash = -p.total if order["side"] == "buy" else p.total
    events.append(
        _event(
            order["side"],
            at,
            asset.key,
            sign * float(p.quantity or 0.0),
            {asset.currency: cash},
            d.price,
            d.basis,
            order,
            home_rate,
            {
                "currency": asset.currency,
                "commission": p.commission,
                "slippage": d.slippage,
                "spread_or_premium": d.spread_or_premium,
                "source": d.source,
                "note": order.get("note"),
                "thesis": thesis,
            },
        )
    )
    return fill, events


def _event(kind, at, asset_key, qty, cash, price, basis, order, fx_rate, payload) -> dict:
    return {
        "kind": kind,
        "at": at,
        "asset_key": asset_key,
        "quantity_delta": qty,
        "cash_delta": {k: round(float(v), 8) for k, v in cash.items()},
        "price": price,
        "price_basis": basis,
        "order_id": order.get("id"),
        "fx_rate": fx_rate,
        "payload": payload,
        "idem_key": f"fill:{order.get('id')}:{kind}" if order.get("id") else None,
    }


def describe_wait(p: Plan) -> str:
    return p.next_line or (p.decision.reason if isinstance(p.decision, (Wait, Reject)) else "")


__all__ = ["Plan", "normalize", "plan", "fill_events", "fmt_price"]

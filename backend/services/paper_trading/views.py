"""What every paper surface reads — the REST routes and Lucius's tools shape their answers
here, from the same derivation, so the screen and Lucius always quote the same figures. Every
shape says ``simulated: true``: nothing here is the owner's money."""

from __future__ import annotations

from datetime import date, datetime, timezone

from backend.core.user_time import user_today
from backend.db.paper_store import MAX_ACTIVE_PORTFOLIOS, PaperPortfolioStore, PaperValueStore
from backend.services.paper_trading import assets, performance, service, valuation
from backend.services.paper_trading.book import Book
from backend.services.paper_trading.kinds.base import fmt_price, quote_age
from backend.services.paper_trading.model import (
    BASIS_LABEL,
    KIND_LABEL,
    Fill,
    PaperAsset,
    Reject,
    Wait,
)
from backend.services.paper_trading.orders import Plan

SIMULATED = {"simulated": True}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def source_label(row: dict) -> str:
    by = str(row.get("created_by") or "owner")
    origin = (row.get("origin") or {}).get("label") or "Cash"
    if by.startswith("automation:") and row.get("automation_name"):
        return f"Run by Lucius: {row['automation_name']}"
    if by == "lucius":
        return f"{origin} · started by Lucius"
    return origin


def day_count(row: dict) -> int:
    try:
        started = date.fromisoformat(str(row.get("started_at") or "")[:10])
    except ValueError:
        return 1
    end = user_today()
    if row.get("archived_at"):
        try:
            end = date.fromisoformat(str(row["archived_at"])[:10])
        except ValueError:
            pass
    return max(1, (end - started).days + 1)


def card(row: dict, *, live: bool = True) -> dict:
    """One portfolio as the list shows it: value, return since start, a sparkline."""
    values = PaperValueStore().for_portfolio(row["id"])
    bk = service.derive(row["id"])
    holdings: list[dict] = []
    if live and row.get("status") == "active":
        val = valuation.value(row, bk, _now())
        value, cash_home = val["value_home"], val["cash_home"]
        holdings = val["holdings"]
    else:
        last = values[-1] if values else {}
        value, cash_home = last.get("value_home"), last.get("cash_home")
    perf = performance.summary(row, bk, values, value)
    orders = service.portfolio_orders(row["id"])
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row.get("status"),
        "home_currency": row["home_currency"],
        "benchmark": row.get("benchmark"),
        "origin": row.get("origin") or {"kind": "cash", "ref": None, "label": "Cash"},
        "created_by": row.get("created_by") or "owner",
        "source_label": source_label(row),
        "run_no": row.get("run_no") or 1,
        "lineage_id": row.get("lineage_id"),
        "started_at": row.get("started_at"),
        "archived_at": row.get("archived_at"),
        "day_count": day_count(row),
        "value": value,
        "cash_home": cash_home,
        "return_pct": perf["return_pct"],
        "benchmark_return_pct": perf["benchmark_return_pct"],
        "open_orders": sum(1 for o in orders if o.get("status") == "open"),
        "sparkline": [{"date": v["date"], "value": v["value_home"]} for v in values[-60:]]
        + ([{"date": user_today().isoformat(), "value": value}] if value is not None else []),
        "settings": row.get("settings") or {},
        # What the card's ring and trade lines show: its largest holdings by weight (a live
        # card only) and its two newest buys or sells.
        "holdings_count": len(holdings),
        "weights": _card_weights(holdings),
        "recent_trades": [
            {"label": t["label"], "side": t["kind"], "symbol": t["symbol"], "at": t["at"]}
            for t in activity(row["id"], limit=2, kinds=["buy", "sell"])["activity"]
        ],
        **SIMULATED,
    }


def _card_weights(holdings: list[dict], top: int = 8) -> list[dict]:
    ranked = sorted(
        (h for h in holdings if h.get("weight") is not None),
        key=lambda h: h["weight"],
        reverse=True,
    )
    return [
        {"symbol": h.get("symbol") or h.get("name") or "?", "weight": h["weight"]}
        for h in ranked[:top]
    ]


def list_portfolios() -> dict:
    rows = PaperPortfolioStore().all()
    active = [card(r) for r in rows if r.get("status") == "active"]
    archived = [card(r, live=False) for r in rows if r.get("status") != "active"]
    archived.sort(key=lambda c: c.get("archived_at") or "", reverse=True)
    return {"portfolios": active, "archived": archived, **SIMULATED}


def order_view(o: dict) -> dict:
    fill = o.get("fill")
    return {
        k: o.get(k)
        for k in (
            "id",
            "portfolio_id",
            "asset_key",
            "kind",
            "symbol",
            "name",
            "side",
            "quantity",
            "notional",
            "order_type",
            "limit_price",
            "stop_price",
            "tif",
            "status",
            "state_reason",
            "placed_at",
            "expires_at",
            "note",
            "origin",
            "from_currency",
            "nav_date",
        )
    } | {"fill": fill, **SIMULATED}


def _event_label(ev: dict, name: str | None) -> str:
    kind = ev.get("kind")
    qty = abs(float(ev.get("quantity_delta") or 0.0))
    price = ev.get("price")
    sym = name or ev.get("asset_key") or ""
    cash = ev.get("cash_delta") or {}
    money = ", ".join(f"{abs(v):,.2f} {c}" for c, v in cash.items())
    if kind == "buy":
        return f"Bought {qty:g} {sym} at {fmt_price(float(price or 0))}"
    if kind == "sell":
        return f"Sold {qty:g} {sym} at {fmt_price(float(price or 0))}"
    if kind == "convert":
        p = ev.get("payload") or {}
        return f"Converted {p.get('from')} to {p.get('to')} at {float(price or 0):.6g}"
    if kind == "deposit":
        return f"Added {money}"
    if kind == "withdraw":
        return f"Withdrew {money}"
    if kind in ("dividend", "distribution"):
        return f"{kind.capitalize()} from {sym}: {money}"
    if kind == "split":
        return f"{sym} split {(ev.get('payload') or {}).get('label') or ''}".strip()
    if kind == "freeze":
        p = ev.get("payload") or {}
        if p.get("frozen", True):
            return f"{sym} frozen: {p.get('reason') or ''}".strip()
        return f"{sym} settled"
    if kind == "mark":
        return f"Your price for {sym}: {fmt_price(float(price or 0))}"
    if kind == "fee":
        return f"Fee: {money}"
    return str(kind)


def event_view(ev: dict, names: dict[str, tuple[str, str]]) -> dict:
    sym, name = names.get(ev.get("asset_key") or "", (None, None))
    basis = ev.get("price_basis")
    payload = ev.get("payload") or {}
    return {
        "id": ev["id"],
        "at": ev.get("at"),
        "kind": ev.get("kind"),
        "asset_key": ev.get("asset_key"),
        "symbol": sym,
        "name": name,
        "quantity_delta": ev.get("quantity_delta"),
        "cash_delta": ev.get("cash_delta") or {},
        "price": ev.get("price"),
        "price_basis": basis,
        "basis_label": BASIS_LABEL.get(basis) if basis else None,
        "order_id": ev.get("order_id"),
        "note": payload.get("note"),
        "thesis": payload.get("thesis"),
        "label": _event_label(ev, sym),
        "opening": bool(payload.get("opening")),
    }


def _names(evs: list[dict]) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for k in {e.get("asset_key") for e in evs if e.get("asset_key")}:
        try:
            d = assets.describe(assets.resolve_key(k))
            out[k] = (d["symbol"], d["name"])
        except Exception:
            out[k] = (k, k)
    return out


def activity(
    pid: str,
    *,
    cursor: int = 0,
    limit: int = 50,
    kinds: list[str] | None = None,
    asset_key: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Newest first. ``cursor`` is an offset into the filtered list."""
    evs = list(reversed(service.events(pid)))
    if kinds:
        evs = [e for e in evs if e.get("kind") in kinds]
    if asset_key:
        evs = [e for e in evs if e.get("asset_key") == asset_key]
    if since:
        evs = [e for e in evs if str(e.get("at") or "")[:10] >= since]
    if until:
        evs = [e for e in evs if str(e.get("at") or "")[:10] <= until]
    page = evs[cursor : cursor + limit]
    names = _names(page)
    nxt = cursor + limit if cursor + limit < len(evs) else None
    return {
        "activity": [event_view(e, names) for e in page],
        "next_cursor": nxt,
        "total": len(evs),
        **SIMULATED,
    }


def page(pid: str) -> dict:
    row = service.portfolio(pid)
    bk = service.derive(pid)
    live = row.get("status") == "active"
    val = valuation.value(row, bk, _now(), fetch_quotes=live)
    values = PaperValueStore().for_portfolio(pid)
    perf = performance.summary(row, bk, values, val["value_home"], with_yours=True)
    all_orders = service.portfolio_orders(pid)
    open_ = [o for o in all_orders if o.get("status") == "open"]
    recent = [o for o in reversed(all_orders) if o.get("status") != "open"][:50]
    return {
        "portfolio": card(row, live=False)
        | {
            "value": val["value_home"],
            "cash_home": val["cash_home"],
            "return_pct": perf["return_pct"],
            "benchmark_return_pct": perf["benchmark_return_pct"],
        },
        "figures": {
            "value": val["value_home"],
            "return_pct": perf["return_pct"],
            "benchmark_return_pct": perf["benchmark_return_pct"],
            "your_return_pct": perf.get("your_return_pct"),
            "cash_home": val["cash_home"],
            "cash": val["cash"],
            "open_orders": len(open_),
            "unrealized_home": val["unrealized_home"],
            "realized_home": val["realized_home"],
            "by_basis": val["by_basis"],
            "unpriced": val["unpriced"],
        },
        "holdings": val["holdings"],
        "holding_groups": valuation.kind_groups(val["holdings"]),
        "orders": [order_view(o) for o in open_ + recent],
        "activity": activity(pid, limit=50)["activity"],
        **SIMULATED,
    }


def preview(
    row: dict,
    bk: Book,
    asset: PaperAsset,
    order: dict,
    p: Plan,
    quote,
    fxq,
    now: datetime,
    thesis: dict | None,
) -> dict:
    d = p.decision
    decision = "fill" if isinstance(d, Fill) else "wait" if isinstance(d, Wait) else "reject"
    used = fxq if asset.kind == "fx" else quote
    basis = d.basis if isinstance(d, Fill) else (used.basis if used else "quote")
    home = row["home_currency"]
    rate = 1.0 if asset.currency == home else (float(fxq.last) if fxq and fxq.last else None)
    if asset.kind == "fx":
        rate = None
    total_home = (
        round(p.total * rate, 2)
        if rate and p.total
        else (
            round(p.total, 2) if asset.kind == "fx" and order.get("from_currency") == home else None
        )
    )
    cash_now = bk.cash.get(home, 0.0)
    spend = (total_home or 0.0) if order["side"] == "buy" else -(total_home or 0.0)
    if p.convert:
        spend = p.convert["amount"]
    return {
        "decision": decision,
        "reason": d.reason if isinstance(d, (Wait, Reject)) else None,
        "available": d.available if isinstance(d, Reject) else None,
        "price": d.price if isinstance(d, Fill) else (used.last if used else None),
        "basis": basis,
        "basis_label": BASIS_LABEL.get(basis),
        "source": d.source if isinstance(d, Fill) else (used.source if used else None),
        "quote_as_of": used.as_of.isoformat() if used and used.as_of else None,
        "quote_age_s": round(quote_age(used, now) or 0) if used and used.as_of else None,
        "session_open": p.session.is_open,
        "next_line": p.next_line,
        "estimate": {
            "quantity": p.quantity,
            "gross": round(p.gross, 2),
            "commission": round(p.commission, 2),
            "spread_or_premium": round(d.spread_or_premium * (p.quantity or 0), 2)
            if isinstance(d, Fill)
            else None,
            "total": round(p.total, 2),
            "currency": p.currency or asset.currency,
            "total_home": total_home,
            "fx_rate": rate,
        },
        "cash_after_home": round(cash_now - spend, 2) if decision != "reject" else None,
        "weight_after": None,
        "thesis": thesis,
        "warnings": p.warnings,
        **assets.describe(asset),
        **SIMULATED,
    }


def toast(order: dict) -> str:
    side = "buy" if order.get("side") == "buy" else "sell"
    if order.get("kind") == "fx":
        side = "conversion"
    sym = order.get("symbol") or order.get("name") or ""
    fill = order.get("fill")
    if order.get("status") == "filled" and fill:
        qty = fill.get("quantity")
        return f"Paper {side} filled: {qty:g} {sym} at {fmt_price(float(fill['price']))}"
    if order.get("status") == "rejected":
        return f"Paper {side} not placed: {order.get('state_reason') or ''}".strip()
    return f"Paper {side} placed: {order.get('state_reason') or 'waiting'}"


def defaults() -> dict:
    from backend.services.followed_portfolios import service as followed
    from backend.services.paper_trading import seed

    rows = PaperPortfolioStore().all()
    try:
        follows = [{"id": f["id"], "name": f["name"]} for f in followed.list_follows(5)["follows"]]
    except Exception:
        follows = []
    return {
        "base_currency": seed.base_currency(),
        "default_cash": seed.default_cash(),
        "benchmark": seed.default_benchmark(),
        "settings": dict(service.DEFAULT_SETTINGS),
        "followed": follows,
        "max_active": MAX_ACTIVE_PORTFOLIOS,
        "active_count": sum(1 for r in rows if r.get("status") == "active"),
        "kinds": KIND_LABEL,
        **SIMULATED,
    }


def positions_in(instrument_id: str) -> dict:
    """Every live paper position in one instrument across ACTIVE paper portfolios — for the
    asset page, which shows each as its own labelled row and never adds it to the real totals.
    Phase 1 has no short options or cover; the fields are present so a row's shape is stable."""
    key = f"inst:{str(instrument_id or '').strip()}"
    asset = assets.resolve_key(key)
    rows: list[dict] = []
    for row in PaperPortfolioStore().all():
        if row.get("status") != "active":
            continue
        bk = service.derive(row["id"])
        if bk.quantity(key) <= 0:
            continue
        val = valuation.value(row, bk, _now())
        held = next((h for h in val["holdings"] if h["asset_key"] == key), None)
        if held is None:
            continue
        rows.append(
            {
                "portfolio_id": row["id"],
                "portfolio_name": row["name"],
                "quantity": held["quantity"],
                "price": held["price"],
                "value_home": held["value_home"],
                "currency": row["home_currency"],
                "price_currency": held["price_currency"],
                "cost_home": held["cost_home"],
                "return_pct": held["pnl_pct"],
                "basis": held["basis"],
                "basis_label": held["basis_label"],
                "frozen": held["frozen"],
                "short_options": [],
                "cover": None,
                **SIMULATED,
            }
        )
    return {
        "instrument_id": key.split(":", 1)[1],
        "symbol": assets.describe(asset)["symbol"],
        "positions": rows,
        **SIMULATED,
    }

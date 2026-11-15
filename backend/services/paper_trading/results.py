"""How a paper portfolio did — the figures Lucius's ``get_paper_results`` returns, read from the
same derivation, performance and compare code as the paper page, so Lucius and the screen
always agree. Per-trade outcomes use the price each trade filled at and the thesis stance
stored ON the fill (what was true then), never a re-derived one."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.services.paper_trading import facade, service, valuation

MAX_TRADES = 10


def _trades(row: dict, holdings: list[dict]) -> list[dict]:
    """Every filled paper buy, with how it has done since: against today's price while held,
    else against what it was sold for (FIFO, as the book realises it)."""
    price_now = {h["asset_key"]: h.get("price") for h in holdings}
    out: list[dict] = []
    for o in service.portfolio_orders(row["id"]):
        fill = o.get("fill") or {}
        if o.get("status") != "filled" or o.get("side") != "buy" or o.get("kind") == "fx":
            continue
        now = price_now.get(o["asset_key"])
        paid = float(fill.get("price") or 0)
        ret = (float(now) / paid - 1.0) if now and paid else None
        out.append(
            {
                "symbol": o.get("symbol"),
                "filled_at": fill.get("filled_at"),
                "price": paid,
                "quantity": fill.get("quantity"),
                "currency": fill.get("currency"),
                "basis_label": fill.get("basis_label"),
                "note": o.get("note"),
                "thesis": fill.get("thesis"),
                "origin": o.get("origin"),
                "return_pct": round(ret, 6) if ret is not None else None,
            }
        )
    return out


def _by_stance(trades: list[dict]) -> list[dict]:
    groups: dict[str, list[float]] = {}
    for t in trades:
        label = (t.get("thesis") or {}).get("label") or "No thesis"
        if t["return_pct"] is not None:
            groups.setdefault(label, []).append(t["return_pct"])
    return [
        {
            "thesis": label,
            "trades": len(rs),
            "average_return_pct": round(sum(rs) / len(rs), 6),
            "hit_rate": round(sum(1 for r in rs if r > 0) / len(rs), 4),
        }
        for label, rs in sorted(groups.items())
    ]


def results(pid: str, rng: str = "ALL") -> dict:
    row = service.portfolio(pid)
    bk = service.derive(pid)
    live = row.get("status") == "active"
    val = valuation.value(row, bk, datetime.now(timezone.utc), fetch_quotes=live)
    perf = facade.performance_series(pid, rng)
    cmp_ = facade.compare(pid)
    trades = _trades(row, val["holdings"])
    scored = [t for t in trades if t["return_pct"] is not None]
    ranked = sorted(scored, key=lambda t: t["return_pct"])
    return {
        "portfolio": row["name"],
        "portfolio_id": pid,
        "status": row.get("status"),
        "home_currency": row["home_currency"],
        "range": rng,
        "value": val["value_home"],
        "return_pct": perf["return_pct"],
        "benchmark": perf["benchmark"],
        "your_investments_return_pct": perf["yours_return_pct"],
        "realized_home": val["realized_home"],
        "unrealized_home": val["unrealized_home"],
        "risk": cmp_["risk"]["paper"],
        "correlation_with_your_book": cmp_["risk"]["correlation"],
        "overlap_with_your_book": {
            "active_share": cmp_["overlap"]["active_share"],
            "shared": [s["symbol"] for s in cmp_["overlap"]["shared"]][:MAX_TRADES],
        },
        "estimated_value_by_basis": {
            k: v for k, v in val["by_basis"].items() if k in ("synthetic", "yours", "modelled")
        },
        "trades": {
            "count": len(trades),
            "hit_rate": round(sum(1 for t in scored if t["return_pct"] > 0) / len(scored), 4)
            if scored
            else None,
            "best": list(reversed(ranked[-3:])),
            "worst": ranked[:3],
            "by_thesis_stance": _by_stance(trades),
        },
        "simulated": True,
    }

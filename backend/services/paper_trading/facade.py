"""The paper REST router's one service: each route calls exactly one function here, which reads
through ``views`` or writes through ``apply_paper_change``. Lucius's tools call the same
functions, so a tool and the screen can never disagree."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.errors import ValidationError
from backend.db.paper_store import PaperValueStore
from backend.services import portfolio_compare
from backend.services.followed_portfolios import compare as followed_compare
from backend.services.followed_portfolios.model import normalize_symbol
from backend.services.paper_trading import assets, performance, service, valuation, views
from backend.services.paper_trading.model import KIND_LABEL

SIMULATED = views.SIMULATED


def defaults() -> dict:
    return views.defaults()


def list_portfolios() -> dict:
    return views.list_portfolios()


def create(body: dict, *, origin: str = "owner") -> dict:
    row = service.apply_paper_change({"op": "create", "origin": origin, **body})["portfolio"]
    return {"portfolio": views.card(row), **SIMULATED}


def page(pid: str) -> dict:
    return views.page(pid)


def update(pid: str, body: dict) -> dict:
    row = service.apply_paper_change({"op": "update", "id": pid, **body})["portfolio"]
    return {"portfolio": views.card(row, live=False), **SIMULATED}


def start_over(pid: str) -> dict:
    out = service.apply_paper_change({"op": "start_over", "id": pid})
    return {
        "portfolio": views.card(out["portfolio"]),
        "archived": views.card(out["archived"], live=False),
        **SIMULATED,
    }


def delete(pid: str) -> dict:
    return {**service.apply_paper_change({"op": "delete", "id": pid}), **SIMULATED}


def _order_change(pid: str, body: dict, origin: str) -> dict:
    order = {k: v for k, v in body.items() if k != "override_mandate"}
    return {
        "portfolio_id": pid,
        "order": order,
        "origin": origin,
        "override_mandate": bool(body.get("override_mandate")),
    }


def preview(pid: str, body: dict) -> dict:
    return service.preview(_order_change(pid, body, "owner"))


def place(pid: str, body: dict, *, origin: str = "owner") -> dict:
    out = service.apply_paper_change({"op": "place", **_order_change(pid, body, origin)})
    return {
        "order": views.order_view(out["order"]),
        "preview": out["preview"],
        "toast": out["toast"],
        **SIMULATED,
    }


def cancel(order_id: str) -> dict:
    out = service.apply_paper_change({"op": "cancel", "order_id": order_id})
    return {"order": views.order_view(out["order"]), **SIMULATED}


def activity(pid: str, **kw) -> dict:
    service.portfolio(pid)
    return views.activity(pid, **kw)


def performance_series(pid: str, rng: str = "ALL") -> dict:
    row = service.portfolio(pid)
    bk = service.derive(pid)
    live = row.get("status") == "active"
    value = valuation.value(row, bk, datetime.now(timezone.utc), fetch_quotes=live)["value_home"]
    values = PaperValueStore().for_portfolio(pid)
    return performance.series(row, bk, values, value, rng, events=service.events(pid))


def _paper_weights(holdings: list[dict]) -> dict[str, float]:
    """A ticker per listed holding; everything else is one slice per kind, as Copy Trading
    treats crypto and funds."""
    total = sum(h["value_home"] or 0.0 for h in holdings)
    out: dict[str, float] = {}
    if total <= 0:
        return out
    for h in holdings:
        if not h.get("value_home"):
            continue
        key = (
            normalize_symbol(h["symbol"])
            if h["kind"] in ("listed", "fund_nav")
            else KIND_LABEL.get(h["kind"], h["kind"])
        )
        out[key] = out.get(key, 0.0) + h["value_home"] / total
    return out


def compare(pid: str) -> dict:
    row = service.portfolio(pid)
    bk = service.derive(pid)
    now = datetime.now(timezone.utc)
    val = valuation.value(row, bk, now, fetch_quotes=row.get("status") == "active")
    paper_w = _paper_weights(val["holdings"])
    try:
        yours = followed_compare.your_weights(None)
    except Exception:
        yours = {}
    perf = performance.series(row, bk, PaperValueStore().for_portfolio(pid), val["value_home"])
    pts = perf["series"]
    paper_line = {p["date"]: p["paper"] for p in pts}
    your_line = {p["date"]: p["yours"] for p in pts}
    bench_line = {p["date"]: p["benchmark"] for p in pts}
    grid = portfolio_compare.weekly(pts[0]["date"], pts[-1]["date"]) if pts else []
    pl = portfolio_compare.levels(paper_line, grid)
    yl = portfolio_compare.levels(your_line, grid)
    bl = portfolio_compare.levels(bench_line, grid)
    return {
        "overlap": portfolio_compare.overlap(paper_w, yours),
        "spread": {
            "paper": portfolio_compare.spread(paper_w),
            "yours": portfolio_compare.spread(yours),
        },
        "risk": {
            "paper": portfolio_compare.line_risk(pl, bl),
            "yours": portfolio_compare.line_risk(yl, bl),
            "correlation": portfolio_compare.correlation(pl, yl),
            "weeks": len(grid),
        },
        **SIMULATED,
    }


def mark(pid: str, body: dict) -> dict:
    out = service.apply_paper_change({"op": "mark", "portfolio_id": pid, **body})
    return {**out, **SIMULATED}


def positions(instrument_id: str) -> dict:
    return views.positions_in(instrument_id)


def search(q: str) -> dict:
    if len(q or "") > 80:
        raise ValidationError("Search is at most 80 characters.")
    return {"results": assets.search(q), **SIMULATED}


def qa_market(
    market: str | None, prices: dict | None, fx: dict | None, opens_in_s: float | None = None
) -> dict:
    """QA only: set the deterministic paper market, then retry open orders against it."""
    from backend.services.paper_trading import engine, qa_quotes

    state = qa_quotes.set_state(market, prices, fx, opens_in_s)
    return {"market": state, "tick": engine.tick()}


def resolve_ref(ref: str, *, active_only: bool = False) -> dict:
    """A paper portfolio by id or by its name (case-insensitive) — for Lucius's tools. An
    active run wins over archived runs of the same name; two active matches raise, naming both."""
    from backend.core.errors import NotFoundError
    from backend.db.paper_store import PaperPortfolioStore

    wanted = str(ref or "").strip()
    rows = PaperPortfolioStore().all()
    hits = [r for r in rows if r["id"] == wanted]
    if not hits:
        named = [r for r in rows if r["name"].lower() == wanted.lower()]
        active = [r for r in named if r.get("status") == "active"]
        hits = active or (
            []
            if active_only
            else sorted(named, key=lambda r: r.get("archived_at") or "", reverse=True)[:1]
        )
    if active_only:
        hits = [r for r in hits if r.get("status") == "active"]
    if not hits:
        raise NotFoundError(f"No {'active ' if active_only else ''}paper portfolio '{wanted}'.")
    if len(hits) > 1:
        raise ValidationError(
            "More than one paper portfolio matches: "
            + ", ".join(f"{r['name']} ({r['id']})" for r in hits)
        )
    return hits[0]

"""Paper trading — a SIMULATED book: pretend money at real market prices. Never the owner's
money, never part of net worth, and never an order to any broker (ADR
2026-11-15-paper-trading-is-a-simulated-book-never-an-order-path).

Every result says ``"simulated": true`` and names its paper portfolio. Writes declare
``mutates=("paper",)`` — a simulation domain, so they are seeded Always allow; deleting a whole
portfolio declares ``destructive=True`` and stays Ask. No tool here has a connector, an
external effect, or any path to a broker.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import NestedInput, ToolInput, schema_of
from backend.core.automation_runner import get_automation_approval
from backend.core.errors import OpenNetWorthError
from backend.services.feature_flags import enabled as flag_enabled
from backend.services.paper_trading import facade, results, views

_OFF = "Paper Trading is switched off on this install (Settings → Developer → Experimental)."
_ABOUT = (
    "PAPER results are simulated — never the owner's money and never part of net worth; say "
    "'paper' when you quote an amount. Fills use the latest price the app saw plus slippage, "
    "spread or premium; a value labelled Synthetic or Your price is an estimate."
)
_MAX_ACTIVITY = 50
_MAX_HOLDINGS = 40
_MAX_ORDERS = 20


def _refusal():
    if flag_enabled("paper_trading"):
        return None
    return json.dumps({"error": _OFF, "simulated": True}), None


def _origin() -> str:
    ctx = get_automation_approval()
    return f"automation:{ctx.automation_id}" if ctx is not None else "lucius"


MAX_ORDERS_PER_CALL = 20


def _guard_target(row: dict) -> None:
    """A paper Automation bound to a paper portfolio may write only that portfolio's runs (its
    lineage, so a Start over keeps it bound)."""
    from backend.core.errors import ValidationError
    from backend.services.paper_trading import service

    ctx = get_automation_approval()
    if ctx is None or not ctx.simulation_only or not ctx.paper_portfolio_id:
        return
    try:
        bound = service.portfolio(ctx.paper_portfolio_id)
    except OpenNetWorthError:
        bound = {"id": ctx.paper_portfolio_id, "lineage_id": None}
    if row["id"] != bound["id"] and row.get("lineage_id") != bound.get("lineage_id"):
        raise ValidationError(
            f"This Automation trades only its own paper portfolio, not '{row.get('name')}'."
        )


def _dump(payload: Any):
    return json.dumps(payload, ensure_ascii=False, default=str), None


def _err(exc: OpenNetWorthError) -> str:
    return exc.message or str(exc)


# ── reads ──────────────────────────────────────────────────────────────────────────────────


class GetPaperPortfoliosInput(ToolInput):
    portfolio: Optional[str] = Field(
        default=None,
        description="One paper portfolio by name or id, with its holdings, open orders and "
        "recent activity. Omit to list every paper portfolio.",
    )
    include_archived: bool = Field(
        default=False, description="Also list archived runs (Start over keeps each old run)."
    )


def _one_portfolio(pid: str) -> dict:
    page = views.page(pid)
    return {
        "portfolio": page["portfolio"],
        "figures": page["figures"],
        "holdings": page["holdings"][:_MAX_HOLDINGS],
        "holdings_total": len(page["holdings"]),
        "open_orders": [o for o in page["orders"] if o["status"] == "open"][:_MAX_ORDERS],
        "recent_activity": page["activity"][:10],
        "simulated": True,
    }


@tool(
    "get_paper_portfolios",
    description=(
        "The owner's paper portfolios (Paper Trading): each one's value, return "
        "since start against its benchmark, cash and open orders; or one portfolio in full with "
        "its holdings, open orders and recent activity. " + _ABOUT
    ),
    input_schema=schema_of(GetPaperPortfoliosInput, tool_name="get_paper_portfolios"),
)
async def get_paper_portfolios(input_data: dict):
    if refused := _refusal():
        return refused
    try:
        if input_data.get("portfolio"):
            row = facade.resolve_ref(input_data["portfolio"])
            return _dump(await asyncio.to_thread(_one_portfolio, row["id"]))
        listed = await asyncio.to_thread(views.list_portfolios)
    except OpenNetWorthError as exc:
        return _dump({"error": _err(exc), "simulated": True})
    if not input_data.get("include_archived"):
        listed = {**listed, "archived_count": len(listed["archived"]), "archived": []}
    for card in listed["portfolios"] + listed["archived"]:
        card.pop("sparkline", None)
    return _dump(listed)


class GetPaperResultsInput(ToolInput):
    portfolios: Optional[List[str]] = Field(
        default=None, description="Paper portfolios by name or id. Omit for every active one."
    )
    range: Literal["1M", "3M", "1Y", "ALL"] = Field(
        default="ALL", description="The window for the return figures."
    )


def _results(refs: Optional[List[str]], rng: str) -> list[dict]:
    if refs:
        rows = [facade.resolve_ref(r) for r in refs]
    else:
        rows = [{"id": c["id"]} for c in views.list_portfolios()["portfolios"]]
    return [results.results(r["id"], rng) for r in rows[:10]]


@tool(
    "get_paper_results",
    description=(
        "How paper portfolios did: return against the benchmark and the owner's real "
        "investments over the same days, realised and unrealised P&L, worst fall, swings, "
        "return per risk, overlap with the real book, and per-trade outcomes — hit rate, best "
        "and worst trades, and how trades did by the thesis stance they were placed in (e.g. in "
        "the buy zone). The same figures as the paper page. " + _ABOUT
    ),
    input_schema=schema_of(GetPaperResultsInput, tool_name="get_paper_results"),
)
async def get_paper_results(input_data: dict):
    if refused := _refusal():
        return refused
    try:
        out = await asyncio.to_thread(
            _results, input_data.get("portfolios"), input_data.get("range") or "ALL"
        )
    except OpenNetWorthError as exc:
        return _dump({"error": _err(exc), "simulated": True})
    return _dump({"results": out, "simulated": True})


class GetPaperActivityInput(ToolInput):
    portfolio: str = Field(description="The paper portfolio, by name or id.")
    kinds: Optional[List[str]] = Field(
        default=None,
        description="Only these event kinds: buy, sell, convert, deposit, withdraw, dividend, "
        "distribution, split, freeze, mark, fee.",
    )
    asset_key: Optional[str] = Field(default=None, description="Only this asset's events.")
    origin: Optional[str] = Field(
        default=None, description="Only orders placed by: owner, lucius, or automation:<id>."
    )
    since: Optional[str] = Field(default=None, description="From this date (YYYY-MM-DD).")
    until: Optional[str] = Field(default=None, description="Up to this date (YYYY-MM-DD).")
    cursor: int = Field(default=0, ge=0, description="next_cursor from the previous page.")
    limit: int = Field(default=25, ge=1, le=_MAX_ACTIVITY, description="Rows per page.")


def _activity(input_data: dict) -> dict:
    row = facade.resolve_ref(input_data["portfolio"])
    page = views.activity(
        row["id"],
        cursor=input_data.get("cursor") or 0,
        limit=min(int(input_data.get("limit") or 25), _MAX_ACTIVITY),
        kinds=input_data.get("kinds"),
        asset_key=input_data.get("asset_key"),
        since=input_data.get("since"),
        until=input_data.get("until"),
    )
    origin = input_data.get("origin")
    if origin:
        orders = {o["id"]: o.get("origin") for o in facade.service.portfolio_orders(row["id"])}
        page["activity"] = [e for e in page["activity"] if orders.get(e.get("order_id")) == origin]
    return {"portfolio": row["name"], **page}


@tool(
    "get_paper_activity",
    description=(
        "A paper portfolio's activity, newest first: fills with their journal notes and fill "
        "details, conversions, dividends, splits, freezes and your-price marks. Filter by date, "
        "kind, asset or who placed the order; paginated. " + _ABOUT
    ),
    input_schema=schema_of(GetPaperActivityInput, tool_name="get_paper_activity"),
)
async def get_paper_activity(input_data: dict):
    if refused := _refusal():
        return refused
    try:
        return _dump(await asyncio.to_thread(_activity, input_data))
    except OpenNetWorthError as exc:
        return _dump({"error": _err(exc), "simulated": True})


# ── orders ─────────────────────────────────────────────────────────────────────────────────


class PaperOrderInput(NestedInput):
    asset: str = Field(
        description="What to trade: an asset_key from a holding or a previous result "
        "(inst:…, ccy:EUR, ctr:…), or a ticker / coin / index symbol such as ACME, BTC, ^GSPC."
    )
    side: Literal["buy", "sell"] = Field(description="buy or sell.")
    quantity: Optional[float] = Field(default=None, description="Shares, coins or units.")
    notional: Optional[float] = Field(
        default=None, description="Or an amount of money in the asset's currency instead."
    )
    order_type: Optional[Literal["market", "limit", "stop"]] = Field(
        default=None, description="Default market. Mutual funds always fill at the next NAV."
    )
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    tif: Optional[Literal["day", "gtc"]] = Field(
        default=None, description="Good for today (default) or until cancelled."
    )
    extended_ok: Optional[bool] = Field(
        default=None,
        description="Stocks and ETFs: also fill pre- and after-market, not only in the regular "
        "session. Only when the owner asks for it; the portfolio's extended_hours setting does "
        "the same for every order.",
    )
    note: Optional[str] = Field(
        default=None, description="Why — the journal note shown on the trade (280 characters)."
    )
    from_currency: Optional[str] = Field(
        default=None, description="Currency conversions only: the currency paid (default home)."
    )
    price: Optional[float] = Field(
        default=None, description="Assets priced by the owner only: the price to trade at."
    )


class PaperOrdersInput(ToolInput):
    portfolio: str = Field(description="The active paper portfolio, by name or id.")
    orders: List[PaperOrderInput] = Field(
        max_length=MAX_ORDERS_PER_CALL,
        description=f"The orders, at most {MAX_ORDERS_PER_CALL}. Sells are placed first.",
    )


def _order_body(o: dict) -> dict:
    ref = str(o.get("asset") or "").strip()
    body = {k: v for k, v in o.items() if k != "asset" and v is not None}
    if ref.split(":", 1)[0] in ("inst", "ccy", "ctr"):
        body["asset_key"] = ref
    elif len(ref) == 3 and ref.isalpha() and ref.isupper() and o.get("notional"):
        body["asset_key"] = f"ccy:{ref}"
    else:
        body["asset"] = {"symbol": ref}
    return body


def _sells_first(orders: list[dict]) -> list[dict]:
    return sorted(orders, key=lambda o: 0 if o.get("side") == "sell" else 1)


def _preview_all(input_data: dict) -> dict:
    row = facade.resolve_ref(input_data["portfolio"])
    out = []
    for o in _sells_first(input_data.get("orders") or []):
        try:
            out.append(facade.preview(row["id"], _order_body(o)))
        except OpenNetWorthError as exc:
            out.append({"asset": o.get("asset"), "error": _err(exc), "simulated": True})
    return {"portfolio": row["name"], "previews": out, "simulated": True}


@tool(
    "preview_paper_orders",
    description=(
        "What placing paper orders would do now, without placing them: the price each would "
        "fill at (and its source and age), or why it would wait or be refused, the total and the "
        "cash left. Use it to size paper trades. " + _ABOUT
    ),
    input_schema=schema_of(PaperOrdersInput, tool_name="preview_paper_orders"),
)
async def preview_paper_orders(input_data: dict):
    if refused := _refusal():
        return refused
    try:
        return _dump(await asyncio.to_thread(_preview_all, input_data))
    except OpenNetWorthError as exc:
        return _dump({"error": _err(exc), "simulated": True})


def _place_all(input_data: dict) -> dict:
    row = facade.resolve_ref(input_data["portfolio"], active_only=True)
    _guard_target(row)
    origin = _origin()
    out = []
    for o in _sells_first(input_data.get("orders") or []):
        try:
            placed = facade.place(row["id"], _order_body(o), origin=origin)
            out.append(
                {
                    "ok": placed["order"]["status"] != "rejected",
                    "order": placed["order"],
                    "summary": placed["toast"],
                }
            )
        except OpenNetWorthError as exc:
            out.append({"ok": False, "asset": o.get("asset"), "error": _err(exc)})
    return {"portfolio": row["name"], "results": out, "simulated": True}


@tool(
    "place_paper_orders",
    description=(
        "Place PAPER orders in a paper portfolio — simulated, no money moves and nothing is sent "
        "to any broker. Market, limit and stop orders; long only. A market order fills at once "
        "when its market is open, else waits for the open; a fund fills at the next NAV. Sells "
        "are placed before buys. Never use this to stand in for a real order, and never call a "
        "paper fill the owner's money. " + _ABOUT
    ),
    input_schema=schema_of(PaperOrdersInput, tool_name="place_paper_orders"),
    mutates=("paper",),
)
async def place_paper_orders(input_data: dict):
    if refused := _refusal():
        return refused
    try:
        return _dump(await asyncio.to_thread(_place_all, input_data))
    except OpenNetWorthError as exc:
        return _dump({"error": _err(exc), "simulated": True})


class CancelPaperOrdersInput(ToolInput):
    order_ids: List[str] = Field(description="Open paper order ids (pord_…).")


def _cancel_all(ids: list[str]) -> dict:
    out = []
    from backend.services.paper_trading import service

    for oid in ids:
        try:
            order = service.PaperOrderStore().find_by_id(str(oid))
            if order:
                _guard_target(service.portfolio(order["portfolio_id"]))
            out.append({"ok": True, "order": facade.cancel(oid)["order"]})
        except OpenNetWorthError as exc:
            out.append({"ok": False, "order_id": oid, "error": _err(exc)})
    return {"results": out, "simulated": True}


@tool(
    "cancel_paper_orders",
    description="Cancel open PAPER orders (simulated; nothing is sent anywhere).",
    input_schema=schema_of(CancelPaperOrdersInput, tool_name="cancel_paper_orders"),
    mutates=("paper",),
)
async def cancel_paper_orders(input_data: dict):
    if refused := _refusal():
        return refused
    return _dump(await asyncio.to_thread(_cancel_all, list(input_data.get("order_ids") or [])))


# ── portfolios ─────────────────────────────────────────────────────────────────────────────


class CreatePaperPortfolioInput(ToolInput):
    name: Optional[str] = Field(default=None, description="Its name.")
    start_with: Literal["cash", "holdings", "followed"] = Field(
        default="cash",
        description="cash; holdings = a copy of the owner's holdings at today's prices; "
        "followed = a followed portfolio's weights bought with `cash`.",
    )
    cash: Optional[float] = Field(
        default=None, description="Starting cash in the home currency (default 10,000)."
    )
    followed_portfolio: Optional[str] = Field(
        default=None, description="With start_with=followed: the followed portfolio's name or id."
    )
    home_currency: Optional[str] = Field(
        default=None, description="Three-letter code; default the base currency. Fixed later."
    )
    benchmark: Optional[str] = Field(default=None, description="Default the preferred one.")
    settings: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Fill settings over the defaults: slippage_bps, fx_spread_pct, "
        "metal_premium_buy_pct, metal_discount_sell_pct, fractional, auto_convert, "
        "extended_hours, commission {fixed, pct}.",
    )


def _create(input_data: dict) -> dict:
    body = {k: v for k, v in input_data.items() if v is not None and k != "followed_portfolio"}
    if input_data.get("followed_portfolio"):
        from backend.services.followed_portfolios import service as followed

        body["followed_id"] = followed.resolve(input_data["followed_portfolio"])["id"]
    origin = _origin()
    ctx = get_automation_approval()
    if ctx is not None:
        body["automation_id"] = ctx.automation_id
    out = facade.create(body, origin=origin)
    card = out["portfolio"]
    card.pop("sparkline", None)
    return {"portfolio": card, "simulated": True}


@tool(
    "create_paper_portfolio",
    description=(
        "Start a PAPER portfolio (simulated): with cash, as a copy of the owner's holdings at "
        "today's prices, or as a followed portfolio bought with chosen cash. It never touches "
        "the owner's accounts or net worth. " + _ABOUT
    ),
    input_schema=schema_of(CreatePaperPortfolioInput, tool_name="create_paper_portfolio"),
    mutates=("paper",),
)
async def create_paper_portfolio(input_data: dict):
    if refused := _refusal():
        return refused
    try:
        return _dump(await asyncio.to_thread(_create, input_data))
    except OpenNetWorthError as exc:
        return _dump({"error": _err(exc), "simulated": True})


class PaperPortfolioUpdate(NestedInput):
    portfolio: str = Field(description="The paper portfolio, by name or id.")
    name: Optional[str] = None
    benchmark: Optional[str] = None
    settings: Optional[Dict[str, Any]] = Field(default=None, description="Fill settings to change.")
    archive: Optional[bool] = Field(default=None, description="True archives it (kept, readable).")
    start_over: Optional[bool] = Field(
        default=None,
        description="True archives this run and starts a new one with the same settings and cash.",
    )


class UpdatePaperPortfoliosInput(ToolInput):
    updates: List[PaperPortfolioUpdate] = Field(description="One entry per portfolio.")


def _update_all(updates: list[dict]) -> dict:
    out = []
    for u in updates:
        try:
            row = facade.resolve_ref(u["portfolio"])
            _guard_target(row)
            if u.get("start_over"):
                done = facade.start_over(row["id"])
                out.append(
                    {
                        "ok": True,
                        "portfolio": done["portfolio"]["name"],
                        "new_run_id": done["portfolio"]["id"],
                        "archived_id": row["id"],
                    }
                )
                continue
            body = {k: u[k] for k in ("name", "benchmark", "settings") if u.get(k) is not None}
            if u.get("archive"):
                body["status"] = "archived"
            card = facade.update(row["id"], body)["portfolio"]
            out.append(
                {"ok": True, "portfolio": card["name"], "id": card["id"], "status": card["status"]}
            )
        except OpenNetWorthError as exc:
            out.append({"ok": False, "portfolio": u.get("portfolio"), "error": _err(exc)})
    return {"results": out, "simulated": True}


@tool(
    "update_paper_portfolios",
    description=(
        "Rename a paper portfolio, change its fill settings or benchmark, archive it, or start "
        "it over (the old run stays readable under Archived). Simulated."
    ),
    input_schema=schema_of(UpdatePaperPortfoliosInput, tool_name="update_paper_portfolios"),
    mutates=("paper",),
)
async def update_paper_portfolios(input_data: dict):
    if refused := _refusal():
        return refused
    return _dump(await asyncio.to_thread(_update_all, list(input_data.get("updates") or [])))


class DeletePaperPortfoliosInput(ToolInput):
    portfolios: List[str] = Field(description="Paper portfolios by name or id.")


def _delete_all(refs: list[str]) -> dict:
    out = []
    for ref in refs:
        try:
            row = facade.resolve_ref(ref)
            _guard_target(row)
            facade.delete(row["id"])
            out.append({"ok": True, "deleted": row["name"], "id": row["id"]})
        except OpenNetWorthError as exc:
            out.append({"ok": False, "portfolio": ref, "error": _err(exc)})
    return {"results": out, "simulated": True}


@tool(
    "delete_paper_portfolios",
    description=(
        "Delete paper portfolios with their whole history — it cannot be undone. Archiving "
        "(update_paper_portfolios) keeps the history; prefer it unless the owner asked to delete."
    ),
    input_schema=schema_of(DeletePaperPortfoliosInput, tool_name="delete_paper_portfolios"),
    mutates=("paper",),
    destructive=True,
)
async def delete_paper_portfolios(input_data: dict):
    if refused := _refusal():
        return refused
    return _dump(await asyncio.to_thread(_delete_all, list(input_data.get("portfolios") or [])))


class PaperMarkInput(NestedInput):
    asset_key: str = Field(description="The asset priced by the owner (ctr:…).")
    price: float = Field(description="Its price, in its own currency.")
    date: Optional[str] = Field(default=None, description="YYYY-MM-DD; default today.")


class RecordPaperMarksInput(ToolInput):
    portfolio: str = Field(description="The paper portfolio, by name or id.")
    marks: List[PaperMarkInput] = Field(description="New prices.")


def _marks(input_data: dict) -> dict:
    row = facade.resolve_ref(input_data["portfolio"], active_only=True)
    _guard_target(row)
    out = []
    for m in input_data.get("marks") or []:
        try:
            facade.mark(row["id"], m)
            out.append({"ok": True, "asset_key": m["asset_key"], "price": m["price"]})
        except OpenNetWorthError as exc:
            out.append({"ok": False, "asset_key": m.get("asset_key"), "error": _err(exc)})
    return {"portfolio": row["name"], "results": out, "simulated": True}


@tool(
    "record_paper_marks",
    description=(
        "Record a price for a paper asset the owner prices themselves (a private share, a flat): "
        "its paper value moves to it. Simulated; labelled Your price wherever it shows."
    ),
    input_schema=schema_of(RecordPaperMarksInput, tool_name="record_paper_marks"),
    mutates=("paper",),
)
async def record_paper_marks(input_data: dict):
    if refused := _refusal():
        return refused
    try:
        return _dump(await asyncio.to_thread(_marks, input_data))
    except OpenNetWorthError as exc:
        return _dump({"error": _err(exc), "simulated": True})

"""
Portfolio read API + native position CRUD.

`read_rows()` is the canonical READ projection: every market holding joined from the
instrument/position/account stores into a flat row (a superset of the columns the old
data.csv exposed), so the frontend tables, the AI portfolio snapshot, net worth and
cash flow all consume one shape. There is no CSV anywhere — positions live in
positions.json (+ instruments.json + accounts.json).

Writes go through positions_service.{add,update,remove}_position by id — the single
source of truth shared with the AI tools and the Telegram apply path.

See .claude/rfcs/instrument-position-data-model.md.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services import positions as positions_service

router = APIRouter()


def read_rows() -> List[dict]:
    """All market positions as flat rows (instrument × account × position joined)."""
    return positions_service.hydrated()


@router.get("/api/portfolio")
async def get_portfolio(live: bool = True):
    """THE portfolio: every holding — market positions AND accounts — with its money already
    computed (base value, cost basis, unrealized P&L, day P&L, weight), plus the totals and the
    per-account holdings map.

    This replaced the browser's own valuation fold (`usePortfolio.compute()`), which re-derived
    qty x price x quote_per_unit x FX beside the backend's and drifted from it
    (ADR 2026-07-31-overview-aggregates-are-server-computed). Consumers render these fields.

    `live=1` (default) refreshes prices first — a real Yahoo/CoinGecko round trip, so it runs in
    a worker thread rather than queueing other sync routes behind it on Starlette's shared
    request pool (same reasoning as GET /api/prices). `live=0` values at last-known prices.

    BOTH branches go to a thread. Only the live one used to, on the reasonable-sounding
    assumption that skipping the network makes `live=0` cheap — but it still folds the WHOLE
    transaction table (`portfolio_rows.projected_rows` → `transactions.all()`), and this is an
    `async def`, so that fold ran on the event loop and starved every other request. Measured
    at >500ms over 1,684 rows, named by `core.loop_watch` live on 2026-08-11 20:31. "No
    network" is not the same as "not blocking".
    """
    from backend.services import portfolio_rows as portfolio_rows_service

    return await asyncio.to_thread(portfolio_rows_service.projected_rows, live=live)


@router.get("/api/portfolio/analytics")
async def get_portfolio_analytics(guideline_pct: Optional[float] = None):
    """Views DERIVED from the portfolio: allocation, reachability, venues, KPI figures, risk
    flags, liabilities, FX exposure, unrealized gains, today's movers, the holdings treemap,
    allocation drift, and the per-class book sections with their concentration ranking.

    Deliberately does NOT repeat `rows` — those live at GET /api/portfolio, so a caller needing
    both gets two cache-deduped reads rather than two copies of the row set on the wire. Rows
    are referenced by `row_key`.
    """
    from backend.services import portfolio_analytics as analytics_service

    return await asyncio.to_thread(analytics_service.portfolio_analytics, None, guideline_pct)


@router.get("/api/portfolio/raw", include_in_schema=False)
def get_portfolio_raw():
    """The un-valued position rows (instrument x account x position joined), as GET
    /api/portfolio returned before it started carrying computed money. Kept for an external
    script pinned to the old bare-array shape; nothing in this app reads it."""
    return read_rows()


class PositionCreate(BaseModel):
    instrument_id: str
    account_id: str
    quantity: float
    cost_basis: Optional[float] = None
    cost_currency: Optional[str] = None
    vesting_state: str = "vested"
    acquired_at: Optional[str] = None
    fees: float = 0
    idempotency_key: Optional[str] = None


class PositionUpdate(BaseModel):
    changes: dict


@router.post("/api/positions")
def create_position(body: PositionCreate):
    """Through the position choke, so the holding is created WITH its lot (an add never
    makes a quantity-only position — ADR 2026-10-07-a-position-quantity-is-derived-from-its-records)."""
    try:
        result = positions_service.apply_position_change({"type": "add", **body.model_dump()})
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if result.get("error"):
        raise HTTPException(409 if result.get("position_id") else 422, result["error"])
    return {"ok": True, "position": positions_service.get_position(result["position_id"])}


@router.patch("/api/positions/{position_id}")
def patch_position(position_id: str, body: PositionUpdate):
    changes = dict(body.changes)
    changes.pop("_grant_managed_ok", None)  # strip bypass flag — only equity.py may set it
    try:
        pos = positions_service.update_position(position_id, changes)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if pos is None:
        raise HTTPException(404, "Position not found")
    return {"ok": True, "position": pos}


@router.delete("/api/positions/{position_id}")
def delete_position(position_id: str):
    try:
        removed = positions_service.remove_position(position_id)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if not removed:
        raise HTTPException(404, "Position not found")
    return {"ok": True}


@router.get("/api/portfolio/book")
async def get_portfolio_book(guideline_pct: Optional[float] = None):
    """The INVESTED book — market holdings only (accounts belong to net worth), grouped by asset
    class with per-row weights, today's and unrealized P&L, single-name concentration,
    allocation vs target, bond YTM, and the cross-account instrument merge.

    A view of the portfolio, so it is named for that — not for the tab that happens to render
    it (it was `/api/investments/book`). Built on the one row projection, so a holding is valued
    identically here and in net worth.
    """
    from backend.services import investments_book as investments_book_service

    return await asyncio.to_thread(investments_book_service.investments_book, None, guideline_pct)

"""Paper trading — a SIMULATED book. Pretend money at real market prices; never an order to any
broker, never part of net worth (ADR 2026-11-15-paper-trading-is-a-simulated-book-never-an-order-path).

GET    /api/paper/defaults                          — what the new-portfolio dialog starts from
GET    /api/paper/portfolios                        — active cards + archived runs
POST   /api/paper/portfolios                        — create (cash | holdings | followed)
GET    /api/paper/portfolios/{id}                   — the page: figures, holdings, orders, activity
PATCH  /api/paper/portfolios/{id}                   — rename, settings, benchmark, archive
PATCH  /api/paper/portfolios/{id}/autopilot         — manual | auto, daily | weekly, instructions
POST   /api/paper/portfolios/{id}/autopilot/run     — review now (the scheduler runs it)
POST   /api/paper/portfolios/{id}/start-over        — archive this run, start the next
DELETE /api/paper/portfolios/{id}
POST   /api/paper/portfolios/{id}/orders/preview    — what placing would do, no write
POST   /api/paper/portfolios/{id}/orders            — place
DELETE /api/paper/orders/{order_id}                 — cancel
GET    /api/paper/portfolios/{id}/activity          — ?cursor=&limit=
GET    /api/paper/portfolios/{id}/performance       — ?range=1W|1M|3M|1Y|ALL
GET    /api/paper/portfolios/{id}/compare
POST   /api/paper/portfolios/{id}/marks             — a price for an asset you price yourself
GET    /api/paper/positions                         — ?instrument_id=: live paper positions in
                                                      one asset, for the asset page (never summed
                                                      into the real totals)
GET    /api/paper/assets/search                     — ?q=: every kind

Every route 404s while the ``paper_trading`` flag is off. Each validates, calls one service
and shapes the response; writes go through ``apply_paper_change``.
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services.feature_flags import enabled as flag_enabled
from backend.services.paper_trading import facade as paper


def _require_flag() -> None:
    if not flag_enabled("paper_trading"):
        raise HTTPException(404, "Not found.")


router = APIRouter(dependencies=[Depends(_require_flag)])


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


class CreateBody(BaseModel):
    name: str | None = Field(None, max_length=80)
    start_with: Literal["cash", "holdings", "followed"] = "cash"
    cash: float | None = Field(None, ge=0)
    followed_id: str | None = Field(None, max_length=80)
    home_currency: str | None = Field(None, min_length=3, max_length=3)
    benchmark: str | None = Field(None, max_length=20)
    settings: dict[str, Any] | None = None
    mandate: dict[str, Any] | None = None


class UpdateBody(BaseModel):
    name: str | None = Field(None, max_length=80)
    benchmark: str | None = Field(None, max_length=20)
    settings: dict[str, Any] | None = None
    status: Literal["archived"] | None = None
    # Sent = replace it (null clears it); not sent = leave it.
    mandate: dict[str, Any] | None = None


class OrderBody(BaseModel):
    asset_key: str | None = Field(None, max_length=80)
    asset: dict[str, Any] | None = None
    side: Literal["buy", "sell"]
    quantity: float | None = None
    notional: float | None = None
    order_type: Literal["market", "limit", "stop", "nav"] | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    tif: Literal["day", "gtc"] | None = None
    extended_ok: bool = False
    note: str | None = Field(None, max_length=280)
    from_currency: str | None = Field(None, min_length=3, max_length=3)
    price: float | None = None
    # Place a buy that breaks the mandate anyway (the owner chose to, after the preview said so).
    override_mandate: bool = False


class MarkBody(BaseModel):
    asset_key: str = Field(..., max_length=80)
    price: float
    date: str | None = Field(None, max_length=10)


@router.get("/api/paper/defaults")
def paper_defaults():
    return _run(paper.defaults)


@router.get("/api/paper/portfolios")
def list_paper_portfolios():
    return _run(paper.list_portfolios)


@router.post("/api/paper/portfolios")
def create_paper_portfolio(body: CreateBody):
    return _run(paper.create, body.model_dump(exclude_none=True))


@router.get("/api/paper/portfolios/{pid}")
def get_paper_portfolio(pid: str):
    return _run(paper.page, pid)


@router.patch("/api/paper/portfolios/{pid}")
def update_paper_portfolio(pid: str, body: UpdateBody):
    return _run(paper.update, pid, body.model_dump(exclude_unset=True))


class AutopilotBody(BaseModel):
    mode: Literal["manual", "auto"] | None = None
    cadence: Literal["daily", "weekly"] | None = None
    instructions: str | None = Field(None, max_length=600)


@router.patch("/api/paper/portfolios/{pid}/autopilot")
def set_paper_autopilot(pid: str, body: AutopilotBody):
    return _run(paper.set_autopilot, pid, body.model_dump(exclude_unset=True))


@router.post("/api/paper/portfolios/{pid}/autopilot/run")
def run_paper_autopilot(pid: str):
    return _run(paper.run_autopilot_now, pid)


@router.post("/api/paper/portfolios/{pid}/start-over")
def start_over_paper_portfolio(pid: str):
    return _run(paper.start_over, pid)


@router.delete("/api/paper/portfolios/{pid}")
def delete_paper_portfolio(pid: str):
    return _run(paper.delete, pid)


@router.post("/api/paper/portfolios/{pid}/orders/preview")
def preview_paper_order(pid: str, body: OrderBody):
    return _run(paper.preview, pid, body.model_dump(exclude_none=True))


@router.post("/api/paper/portfolios/{pid}/orders")
def place_paper_order(pid: str, body: OrderBody):
    return _run(paper.place, pid, body.model_dump(exclude_none=True))


@router.delete("/api/paper/orders/{order_id}")
def cancel_paper_order(order_id: str):
    return _run(paper.cancel, order_id)


@router.get("/api/paper/portfolios/{pid}/activity")
def paper_activity(pid: str, cursor: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    return _run(paper.activity, pid, cursor=cursor, limit=limit)


@router.get("/api/paper/portfolios/{pid}/performance")
def paper_performance(pid: str, range: Literal["1W", "1M", "3M", "1Y", "ALL"] = "ALL"):  # noqa: A002
    return _run(paper.performance_series, pid, range)


@router.get("/api/paper/portfolios/{pid}/compare")
def paper_compare(pid: str):
    return _run(paper.compare, pid)


@router.post("/api/paper/portfolios/{pid}/marks")
def record_paper_mark(pid: str, body: MarkBody):
    return _run(paper.mark, pid, body.model_dump(exclude_none=True))


@router.get("/api/paper/positions")
def paper_positions_in_instrument(instrument_id: str = Query(..., min_length=1, max_length=80)):
    return _run(paper.positions, instrument_id)


@router.get("/api/paper/assets/search")
def search_paper_assets(q: str = Query("", max_length=80)):
    return _run(paper.search, q)


# ── QA only: the deterministic market the end-to-end run drives ────────────────────────────
# Mounted only in a QA process (``ONW_QA_MODE``) and attested by that run's nonce, exactly like
# ``market_data_qa_api``; ``qa_quotes`` itself refuses to run outside QA mode.

qa_router = APIRouter()


class QaMarketBody(BaseModel):
    market: Literal["open", "closed"] | None = None
    prices: dict[str, float] | None = None
    fx: dict[str, float] | None = None
    opens_in_s: float | None = Field(None, ge=0, le=3600)


def _attest(x_onw_qa_nonce: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ONW_QA_NONCE", "")
    if not expected or not x_onw_qa_nonce or not hmac.compare_digest(x_onw_qa_nonce, expected):
        raise HTTPException(404, "Not Found")


@qa_router.post("/api/qa/paper/market", dependencies=[Depends(_attest)])
def set_qa_paper_market(body: QaMarketBody):
    return paper.qa_market(body.market, body.prices, body.fx, body.opens_in_s)

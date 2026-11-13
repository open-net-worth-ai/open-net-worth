"""
GET    /api/followed-portfolios                 — every follow with its model (information only)
GET    /api/followed-portfolios/directory       — ?q=: well-known portfolios, or a search by name
POST   /api/followed-portfolios                 — follow one ({kind, source_ref}); fetches its filing
DELETE /api/followed-portfolios/{id}            — unfollow
POST   /api/followed-portfolios/{id}/refresh    — re-read the source now
GET    /api/followed-portfolios/price-history   — ?symbol=: a year of closes for the trade chart
GET    /api/followed-portfolios/preview         — ?kind=&ref=&full=: a portfolio before following
                                                  it, as a card (default) or its whole page
GET    /api/followed-portfolios/preview/returns — ?kind=&ref=: every holding's own return and
                                                  exchange, asked for once the page is open
GET    /api/followed-portfolios/preview/compare — ?kind=&ref=&since=&band=: the page's "You vs."
                                                  section (overlap, spread, sectors, risk)
GET    /api/followed-portfolios/{id}/performance — its current holdings' price return against
                                                  the benchmark, by window (not the fund's own return)

Behind the ``followed_portfolios`` flag: every route 404s while it is off. Rules and writes live
in ``services.followed_portfolios.service.apply_followed_portfolio_change``.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services.feature_flags import enabled as flag_enabled
from backend.services.followed_portfolios import service


def _require_flag() -> None:
    if not flag_enabled("followed_portfolios"):
        raise HTTPException(404, "Not found.")


router = APIRouter(dependencies=[Depends(_require_flag)])


class FollowBody(BaseModel):
    kind: Literal["fund_13f", "house_member", "etf_daily"]
    source_ref: str


class TickerBody(BaseModel):
    key: str = Field(..., min_length=1, max_length=12)
    symbol: str = Field("", max_length=12)


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/followed-portfolios")
def list_followed_portfolios():
    return service.list_follows()


@router.get("/api/followed-portfolios/directory")
def followed_portfolio_directory(q: str = Query("", max_length=80)):
    return _run(service.directory, q)


@router.get("/api/followed-portfolios/price-history")
def followed_portfolio_price_history(symbol: str = Query(..., min_length=1, max_length=12)):
    return _run(service.price_history, symbol)


@router.get("/api/followed-portfolios/preview")
def followed_portfolio_preview(
    kind: Literal["fund_13f", "house_member", "etf_daily"],
    ref: str = Query(..., min_length=1, max_length=80),
    full: bool = False,
):
    return _run(service.preview_page if full else service.preview_summary, kind, ref)


@router.get("/api/followed-portfolios/preview/returns")
def followed_portfolio_preview_returns(
    kind: Literal["fund_13f", "house_member", "etf_daily"],
    ref: str = Query(..., min_length=1, max_length=80),
):
    return _run(service.preview_returns, kind, ref)


@router.get("/api/followed-portfolios/preview/compare")
def followed_portfolio_preview_compare(
    kind: Literal["fund_13f", "house_member", "etf_daily"],
    ref: str = Query(..., min_length=1, max_length=80),
    since: date | None = None,
    band: Literal["equity", "crypto", "commodity", "all_assets"] | None = None,
):
    return _run(
        service.preview_compare,
        kind,
        ref,
        since=since.isoformat() if since else None,
        band=band,
    )


@router.post("/api/followed-portfolios")
def follow_portfolio(body: FollowBody):
    return _run(
        service.apply_followed_portfolio_change,
        {"op": "follow", "kind": body.kind, "source_ref": body.source_ref},
    )


@router.delete("/api/followed-portfolios/{follow_id}")
def unfollow_portfolio(follow_id: str):
    return _run(service.apply_followed_portfolio_change, {"op": "unfollow", "id": follow_id})


@router.post("/api/followed-portfolios/{follow_id}/refresh")
def refresh_followed_portfolio(follow_id: str):
    return _run(service.apply_followed_portfolio_change, {"op": "refresh", "id": follow_id})


@router.post("/api/followed-portfolios/{follow_id}/ticker")
def set_followed_portfolio_ticker(follow_id: str, body: TickerBody):
    return _run(
        service.apply_followed_portfolio_change,
        {"op": "set_ticker", "id": follow_id, "key": body.key, "symbol": body.symbol},
    )


@router.get("/api/followed-portfolios/{follow_id}/performance")
def followed_portfolio_performance(follow_id: str):
    return _run(service.follow_performance, follow_id)

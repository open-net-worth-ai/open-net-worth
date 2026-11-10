"""
GET    /api/followed-portfolios                 — every follow with its model (information only)
GET    /api/followed-portfolios/directory       — ?q=: well-known portfolios, or a search by name
POST   /api/followed-portfolios                 — follow one ({kind, source_ref}); fetches its filing
DELETE /api/followed-portfolios/{id}            — unfollow
POST   /api/followed-portfolios/{id}/refresh    — re-read the source now

Behind the ``followed_portfolios`` flag: every route 404s while it is off. Rules and writes live
in ``services.followed_portfolios.service.apply_followed_portfolio_change``.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services.feature_flags import enabled as flag_enabled
from backend.services.followed_portfolios import service


def _require_flag() -> None:
    if not flag_enabled("followed_portfolios"):
        raise HTTPException(404, "Not found.")


router = APIRouter(dependencies=[Depends(_require_flag)])


class FollowBody(BaseModel):
    kind: Literal["fund_13f", "house_member"]
    source_ref: str


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

"""Bank sync without AI — REST over ``services.bank_data_sync`` (ADR 2026-11-13).

  * ``GET  /api/bank-sync``                           every bank connector that can sync without AI
  * ``GET  /api/bank-sync/{provider}``                its mode, accounts (+ Sync), last/next run
  * ``PUT  /api/bank-sync/{provider}/mode``           ``{mode: off|data|lucius}``
  * ``PUT  /api/bank-sync/{provider}/accounts/{id}``  ``{sync: bool}``
  * ``PUT  /api/bank-sync/{provider}/followup``       ``{enabled: bool}``
  * ``PUT  /api/bank-sync/{provider}/schedule``       ``{interval_hours: int}``
  * ``POST /api/bank-sync/{provider}/import``         bring in the history (setup step 2)
  * ``POST /api/bank-sync/{provider}/run``            Sync now (409 with the reason when refused)
  * ``GET  /api/bank-sync/{provider}/runs``           its run history
  * ``GET  /api/bank-sync/reviews``                   open review items (``?provider=``)
  * ``POST /api/bank-sync/reviews/{id}/accept|dismiss|book``

Auth is the app-wide ``require_auth``; on the public demo every write here is locked
(``demo_lockdown``). With the ``bank_data_sync`` flag off every route answers 404 — the service
is the seam, this router never branches on it. Nothing here returns a credential."""

from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services import bank_data_sync

router = APIRouter()


class ModeBody(BaseModel):
    mode: Literal["off", "data", "lucius"]


class AccountSyncBody(BaseModel):
    sync: bool


class FollowupBody(BaseModel):
    enabled: bool


class ScheduleBody(BaseModel):
    interval_hours: int


class DecisionBody(BaseModel):
    # The item's updated_at as the user saw it; a newer version of the item is refused (409).
    updated_at: Optional[str] = None


async def _call(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/bank-sync")
async def bank_sync_overview():
    return {"connectors": await _call(bank_data_sync.overview)}


@router.get("/api/bank-sync/reviews")
async def bank_sync_reviews(provider: Optional[str] = None):
    return {"items": await _call(bank_data_sync.list_reviews, provider)}


@router.post("/api/bank-sync/reviews/{item_id}/accept")
async def bank_sync_review_accept(item_id: str, body: Optional[DecisionBody] = None):
    return await _call(bank_data_sync.accept_review, item_id, body.updated_at if body else None)


@router.post("/api/bank-sync/reviews/{item_id}/dismiss")
async def bank_sync_review_dismiss(item_id: str, body: Optional[DecisionBody] = None):
    return await _call(bank_data_sync.dismiss_review, item_id, body.updated_at if body else None)


@router.post("/api/bank-sync/reviews/{item_id}/book")
async def bank_sync_review_book(item_id: str, body: Optional[DecisionBody] = None):
    return await _call(bank_data_sync.book_review, item_id, body.updated_at if body else None)


@router.get("/api/bank-sync/{provider}")
async def bank_sync_status(provider: str):
    return await _call(bank_data_sync.status, provider)


@router.put("/api/bank-sync/{provider}/mode")
async def bank_sync_mode(provider: str, body: ModeBody):
    return await _call(bank_data_sync.set_mode, provider, body.mode)


@router.put("/api/bank-sync/{provider}/accounts/{account_id}")
async def bank_sync_account(provider: str, account_id: str, body: AccountSyncBody):
    return await _call(bank_data_sync.set_account_sync, provider, account_id, body.sync)


@router.put("/api/bank-sync/{provider}/schedule")
async def bank_sync_schedule(provider: str, body: ScheduleBody):
    return await _call(bank_data_sync.set_interval, provider, body.interval_hours)


@router.put("/api/bank-sync/{provider}/followup")
async def bank_sync_followup(provider: str, body: FollowupBody):
    return await _call(bank_data_sync.set_followup, provider, body.enabled)


@router.post("/api/bank-sync/{provider}/run")
async def bank_sync_run(provider: str):
    return await _call(bank_data_sync.run_now, provider)


@router.post("/api/bank-sync/{provider}/import")
async def bank_sync_import(provider: str):
    return await _call(bank_data_sync.import_history, provider)


@router.get("/api/bank-sync/{provider}/runs")
async def bank_sync_runs(provider: str):
    return {"runs": await _call(bank_data_sync.list_runs, provider)}

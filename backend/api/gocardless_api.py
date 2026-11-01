"""GoCardless Bank Account Data connector REST — credentials and the bank link.

Thin router over ``db.GoCardlessConfigStore`` + ``services.aggregation.gocardless``:
  * ``GET  /api/gocardless/status``        — the public (non-secret) config + status.
  * ``GET  /api/gocardless/institutions``  — the banks for a country (picker feed).
  * ``PUT  /api/gocardless/config``        — secret_id + write-only secret_key, enable/disable.
    A blank secret_key keeps the saved one.
  * ``POST /api/gocardless/link``          — create a requisition → the bank ``link`` to open.
  * ``POST /api/gocardless/check-link``    — re-read the requisition after consent.

The secret key, access token and requisition id are never returned. Fetching rows happens only
inside a Lucius turn (``gocardless_fetch_transactions`` + ``add_transactions``), per RFC
``connectors-as-data-sources``.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.db.gocardless_config_store import GoCardlessConfigStore
from backend.services.aggregation import gocardless as gocardless_service

router = APIRouter()


class GoCardlessConfigUpdate(BaseModel):
    secret_id: Optional[str] = None
    # Write-only — blank/omitted keeps the saved secret_key.
    secret_key: Optional[str] = None
    enabled: Optional[bool] = None


class GoCardlessLinkRequest(BaseModel):
    institution_id: str
    country: str
    # Where the bank sends the user back after consent — this app's own address.
    redirect: str


async def _call(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/gocardless/status")
def gocardless_status():
    return GoCardlessConfigStore().public()


@router.get("/api/gocardless/institutions")
async def gocardless_institutions(country: str):
    return {"institutions": await _call(gocardless_service.list_institutions, country)}


@router.put("/api/gocardless/config")
def update_gocardless_config(body: GoCardlessConfigUpdate):
    store = GoCardlessConfigStore()
    before = (store.secret_id(), store.secret_key())
    store.update(body.model_dump(exclude_none=True))
    if (store.secret_id(), store.secret_key()) != before:
        store.clear_token()  # a token minted from the old pair must not outlive it
    return store.public()


@router.post("/api/gocardless/link")
async def gocardless_link(body: GoCardlessLinkRequest):
    return await _call(
        gocardless_service.start_link, body.institution_id, body.country, body.redirect
    )


@router.post("/api/gocardless/check-link")
async def gocardless_check_link():
    return await _call(gocardless_service.check_link)

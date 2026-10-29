"""Plaid connector REST — US/Canada bank aggregation control (P3-CONN aggregation).

Thin router over ``db.PlaidConfigStore`` + ``services.aggregation.plaid``:
  * ``GET  /api/plaid/status``    — the public (non-secret) config + stats.
  * ``PUT  /api/plaid/config``    — set the API credentials (client_id + write-only secret),
    the environment (sandbox|production), enable/disable, change the poll interval. A blank
    secret leaves the saved one unchanged (write-only-secret discipline).
  * ``POST /api/plaid/link``      — start the consent flow → returns the Plaid Hosted Link
    ``link`` the user opens (no frontend Link-SDK needed).
  * ``POST /api/plaid/link/complete`` — after the user finishes Hosted Link, read the session's
    public_token back from Plaid and exchange it (``pending`` until they have finished).
  * ``POST /api/plaid/exchange``  — exchange the public_token (delivered after Hosted Link)
    for an access token + item_id (persisted write-only).

Auth is the app-wide ``require_auth`` dependency (this is under ``/api``). The secret /
access token are SECRETS — accepted (write-only) but NEVER returned (the status echoes only
``has_secret`` / ``has_access_token``). Fetching + booking rows is no longer a scheduled
poll or a REST action — it happens only inside a Lucius turn (``plaid_fetch_transactions``
+ ``add_transactions``), per RFC ``connectors-as-data-sources``.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.db.plaid_config_store import PlaidConfigStore
from backend.services.aggregation import plaid as plaid_service

router = APIRouter()


class PlaidConfigUpdate(BaseModel):
    client_id: Optional[str] = None
    # Write-only — blank/omitted keeps the saved secret.
    secret: Optional[str] = None
    environment: Optional[str] = None  # sandbox | production
    enabled: Optional[bool] = None


class PlaidLinkRequest(BaseModel):
    # The URL Plaid redirects back to after Hosted Link consent (defaults applied
    # service-side; optional for the sandbox flow).
    redirect: Optional[str] = None


class PlaidExchangeRequest(BaseModel):
    public_token: str


@router.get("/api/plaid/status")
def plaid_status():
    """Public config + sync stats (never the secret / access token — only ``has_*`` flags)."""
    return PlaidConfigStore().public()


@router.put("/api/plaid/config")
def update_plaid_config(body: PlaidConfigUpdate):
    """Configure the connector — credentials, environment, enable/disable. A blank
    ``secret`` keeps the saved one (write-only). Returns the public status."""
    store = PlaidConfigStore()
    changes: dict = {}
    if body.client_id is not None:
        changes["client_id"] = body.client_id
    if body.secret is not None:
        changes["secret"] = body.secret
    if body.environment is not None:
        changes["environment"] = body.environment
    if body.enabled is not None:
        changes["enabled"] = bool(body.enabled)

    store.update(changes)
    return store.public()


@router.post("/api/plaid/link")
async def plaid_link(body: PlaidLinkRequest):
    """Start the consent flow → returns the Plaid Hosted Link ``link`` the user opens to
    authenticate at their bank (no frontend Link-SDK). Requires credentials. Bad config /
    auth → 422. Blocking httpx off the loop."""
    try:
        result = await asyncio.to_thread(plaid_service.create_link_token, body.redirect or "")
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return result


@router.post("/api/plaid/link/complete")
async def plaid_link_complete():
    """Finish the Hosted Link session ``/api/plaid/link`` started → ``{status: "pending"}``
    until the user is done at their bank, then ``{status: "linked", item_id, ...}``. No link
    in flight / Plaid refusal → 422. Blocking httpx off the loop."""
    try:
        return await asyncio.to_thread(plaid_service.complete_hosted_link)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/plaid/exchange")
async def plaid_exchange(body: PlaidExchangeRequest):
    """Exchange the ``public_token`` (delivered after the user finishes Hosted Link) for an
    access token + item_id (persisted write-only). Bad/used token / auth → 422. Hot-reloads
    the poll. Blocking httpx off the loop."""
    try:
        result = await asyncio.to_thread(plaid_service.exchange_public_token, body.public_token)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return result

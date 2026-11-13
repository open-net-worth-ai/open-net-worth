"""Enable Banking connector REST — EU PSD2 open-banking aggregation control (P3-LINK §4.1).

Thin router over ``db.EnableBankingConfigStore`` + ``services.aggregation.enable_banking``:
  * ``GET  /api/enable-banking/status``        — the public (non-secret) config + stats.
  * ``GET  /api/enable-banking/institutions``  — list the banks for a country (picker feed).
  * ``PUT  /api/enable-banking/config``        — set the API credentials (application_id +
    write-only RSA private_key), enable/disable. A blank private_key leaves the saved one
    unchanged (write-only-secret discipline).
  * ``POST /api/enable-banking/link``          — start the consent flow for a bank → returns
    the bank-auth ``url`` the user opens.
  * ``POST /api/enable-banking/complete``      — exchange the post-consent auth code for a
    session: one more linked bank (or a renewal of one already listed).
  * ``POST /api/enable-banking/banks/{key}/remove`` — unlink one bank (revoked at Enable
    Banking first). ``key`` is the bank's public handle from the status, never its session.

Auth is the app-wide ``require_auth`` dependency (this is under ``/api``). The private_key /
session are SECRETS — accepted (write-only) but NEVER returned (the status echoes only
``has_credentials`` / ``has_session``). Fetching + booking rows is no longer a scheduled
poll or a REST action — it happens only inside a Lucius turn
(``enable_banking_fetch_transactions`` + ``add_transactions``), per RFC
``connectors-as-data-sources``.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, RedirectNotRegisteredError, to_http_status
from backend.db.enable_banking_config_store import EnableBankingConfigStore
from backend.services.aggregation import enable_banking as enable_banking_service

router = APIRouter()


class EnableBankingConfigUpdate(BaseModel):
    application_id: Optional[str] = None
    # Write-only — blank/omitted keeps the saved private_key.
    private_key: Optional[str] = None
    enabled: Optional[bool] = None


class EnableBankingLinkRequest(BaseModel):
    aspsp: str  # the bank name (from /institutions)
    country: Optional[str] = None
    # The URL the bank redirects back to after consent (defaults applied service-side).
    redirect: Optional[str] = None


class EnableBankingCompleteRequest(BaseModel):
    code: str  # the post-consent authorization code returned to the redirect URI
    # The `state` the bank returned with the code (automatic return); blank for a pasted code.
    state: Optional[str] = None


@router.get("/api/enable-banking/status")
def enable_banking_status():
    """Public config + sync stats (never the private_key / session — only ``has_*`` flags)."""
    return EnableBankingConfigStore().public()


@router.get("/api/enable-banking/institutions")
async def enable_banking_institutions(country: str):
    """List the banks Enable Banking supports in ``country`` (ISO 3166 alpha-2). Requires
    credentials (signs a JWT). Bad country / auth → 422. Blocking httpx off the loop."""
    try:
        return {
            "institutions": await asyncio.to_thread(
                enable_banking_service.list_institutions, country
            )
        }
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.put("/api/enable-banking/config")
def update_enable_banking_config(body: EnableBankingConfigUpdate):
    """Configure the connector — credentials, enable/disable. A blank ``private_key`` keeps
    the saved one (write-only). Returns the public status."""
    store = EnableBankingConfigStore()
    changes: dict = {}
    if body.application_id is not None:
        changes["application_id"] = body.application_id
    if body.private_key is not None:
        changes["private_key"] = body.private_key
    if body.enabled is not None:
        changes["enabled"] = bool(body.enabled)

    store.update(changes)
    return store.public()


@router.post("/api/enable-banking/link")
async def enable_banking_link(body: EnableBankingLinkRequest):
    """Start the bank-consent flow for a bank → returns the auth ``url`` the user opens to
    authenticate at their bank (+ an authorization handle). Requires credentials. Bad bank /
    auth → 422. Blocking httpx off the loop."""
    try:
        result = await asyncio.to_thread(
            enable_banking_service.start_authorization,
            body.aspsp,
            body.country or "",
            body.redirect or "",
        )
    except RedirectNotRegisteredError as exc:
        # The form shows the address to add (with Copy) instead of only a sentence.
        detail = {
            "code": exc.code,
            "message": exc.message,
            "redirect_url": exc.context.get("redirect_url", ""),
        }
        raise HTTPException(to_http_status(exc), detail) from exc
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return result


@router.post("/api/enable-banking/complete")
async def enable_banking_complete(body: EnableBankingCompleteRequest):
    """Exchange the post-consent authorization code for a session (the per-bank consent
    handle, persisted). Requires credentials. Bad code / auth → 422. Blocking httpx off the
    loop."""
    try:
        await asyncio.to_thread(enable_banking_service.create_session, body.code, body.state or "")
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    # Don't leak the session id / raw accounts to the client — return the public status.
    return EnableBankingConfigStore().public()


@router.post("/api/enable-banking/banks/{key}/remove")
async def enable_banking_remove_bank(key: str):
    """Unlink one bank: its consent is revoked at Enable Banking, then forgotten here. Its
    imported transactions and linked accounts stay. Unknown bank → 422."""
    try:
        result = await asyncio.to_thread(enable_banking_service.remove_bank, key)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return {**result, "status": EnableBankingConfigStore().public()}

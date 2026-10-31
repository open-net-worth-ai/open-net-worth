"""Connectors REST — the unified connector API.

Thin router: validate → call ``services/connectors`` → shape the response. NO connector logic
here (it lives in the service). ``GET`` lists the connectors table joined to the catalog;
``PATCH`` switches one on or off (``enabled`` only); ``DELETE`` is a REAL, credential-clearing
removal — see ``services.connectors.remove_connector``. An unknown connector is 404. Imports
only the service (api → services).

A connector that is not ``pausable`` (Yahoo Finance, CoinGecko) has no "enabled" concept, so
``PATCH`` 422s for it, and ``DELETE`` 422s too — Yahoo Finance holds no credential of its own
and CoinGecko keeps running unconditionally, so Remove must not read as "this connector is
gone". Both refusals are raised by the SERVICE, not this router, and the existing
``OpenNetWorthError`` → HTTP translation below surfaces them.

Both ``PATCH`` and ``DELETE`` are ``async def`` for two reasons:

  1. The sync service call is wrapped in ``run_in_threadpool`` rather than called directly
     on the event loop — ``remove_connector`` can run an MCP OAuth revoke (a blocking HTTP
     call, server-metadata-driven, up to ~60s worst case across redirects) or a Lunch Flow
     revoke; running that inline would stall every OTHER request this process is serving
     (every SSE stream, every in-flight turn) for the duration.
  2. Telegram gets extra, ORDER-SENSITIVE handling. ``PATCH`` best-effort hot-reloads the
     live bot afterward (mirrors ``PUT /api/telegram/config``), reporting whether the
     reload actually completed. ``DELETE`` inverts that order for Telegram specifically:
     it STOPS the live bot FIRST and only proceeds to erase its credentials once that stop
     is CONFIRMED — never the other way around. A reset-before-stop ordering would open a
     real window (permanent, if the stop then silently failed) where a still-live process
     is reachable by anyone: removing the row cannot un-erase the fact that a live process
     was started with the OLD token, so the only thing that actually closes that window is
     confirming the transport is down before the credential write lands. (An absent Telegram
     row also reads as ``access_policy="disabled"``, as a second, independent layer — but that
     is belt-and-suspenders, not a reason to skip the ordering fix.)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from backend.ai.permissions import engine
from backend.core.errors import OpenNetWorthError, to_http_status
from backend.models.connector import ConnectorPatch
from backend.services import connectors as connectors_service

router = APIRouter()


async def _reload_telegram_if_needed(connector_id: str) -> bool:
    """Best-effort live-bot reload after a write that touched Telegram — enable or disable
    (``PATCH``) land here. Returns whether the reload actually completed; ``True`` (a
    trivial "nothing needed reloading") for every other connector, so a caller can thread
    the result into the response uniformly without a second branch."""
    if connector_id != connectors_service.TELEGRAM_ID:
        return True
    from backend.connectors.telegram import bot as telegram_bot

    return await telegram_bot.reload_best_effort()


@router.get("/api/connectors")
def list_connectors():
    """The connector list. An empty table still returns every built-in connector as
    ``not_configured`` — never 500."""
    ladder_v2 = engine().ladder_v2
    return {
        "connectors": [
            v.model_dump() for v in connectors_service.list_connectors(ladder_v2=ladder_v2)
        ]
    }


@router.get("/api/connectors/{connector_id:path}/accounts")
def get_connector_accounts(connector_id: str):
    """Per-provider account list (RFC connectors-as-data-sources §E) — the Connectors tab's
    link picker and the Add Account "Connect a bank or broker" flow's target. Registered
    BEFORE the single-segment ``GET /{connector_id}`` below so this literal ``/accounts``
    suffix wins the match (``:path`` would otherwise happily swallow it into
    ``connector_id="plaid/accounts"`` first — the same ordering fix ``api/ledger.py``'s
    ``get_tracking_health`` documents for its own ``{account_id}`` sibling). 404 for an
    unknown connector (same validation as ``GET /api/connectors/{id}``).

    ``services.connector_fetch.provider_accounts`` (RFC §A) calls the provider and reports
    a live ``currency``/``balance`` per account, always merged with the LINKED view built
    from ``Account.external_ids`` (C-B2) so an account linked before the provider went
    quiet still shows up rather than vanishing from the picker.
    """
    try:
        connectors_service.get_connector(connector_id)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    from backend.services.connector_fetch import provider_accounts

    try:
        return provider_accounts(connector_id)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/connectors/{connector_id:path}")
def get_connector(connector_id: str):
    """One connector by id. 404 if unknown. (``:path`` so an ``mcp:<id>`` id round-trips.)"""
    try:
        return connectors_service.get_connector(
            connector_id, ladder_v2=engine().ladder_v2
        ).model_dump()
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.patch("/api/connectors/{connector_id:path}")
async def patch_connector(connector_id: str, body: ConnectorPatch):
    """Enable or disable a connector. Delegates to the connectors table. 404 unknown
    connector; 422 for one that is not pausable. Returns the updated view, plus a ``reloaded``
    bool when ``connector_id`` is Telegram (whether the live bot actually picked up the change
    — mirrors ``PUT /api/telegram/config``'s own ``reloaded`` field; the frontend surfaces a
    warning if this comes back ``False``).
    """
    if body.enabled is None:
        raise HTTPException(422, "enabled is required")
    try:
        view = await run_in_threadpool(
            connectors_service.set_connector_enabled, connector_id, body.enabled
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    result = view.model_dump()
    if connector_id == connectors_service.TELEGRAM_ID:
        result["reloaded"] = await _reload_telegram_if_needed(connector_id)
    return result


@router.delete("/api/connectors/{connector_id:path}")
async def remove_connector(connector_id: str):
    """Remove a connector FOR REAL (the Remove-connector kebab item) — deletes its row and
    every stored credential, unlike ``PATCH``'s enable/disable toggle, which leaves them in
    place. 404 unknown connector. An MCP server is routed to the SAME revoke-then-delete
    path ``DELETE /api/mcp-clients/{id}`` already runs; the frontend still calls that route
    directly for MCP entries, so this branch exists for a caller that addresses an MCP server
    through the generic route.

    For Telegram specifically: the live bot is STOPPED FIRST, and its result checked —
    the credential reset only proceeds once the stop is confirmed. If the stop could not
    be confirmed, this refuses (502) rather than erasing credentials out from under a
    process that might still be live and reachable; the operator can retry, or restart the
    app to force a clean stop. See the module docstring for the full reasoning.
    """
    result: dict = {"deleted": connector_id}
    if connector_id == connectors_service.TELEGRAM_ID:
        from backend.connectors.telegram import bot as telegram_bot

        stopped = await telegram_bot.stop()
        result["reloaded"] = stopped
        if not stopped:
            raise HTTPException(
                502,
                "Could not confirm the Telegram bot stopped, so its credentials were NOT "
                "cleared — removing them first would risk a still-live bot losing its "
                "allowlist while it keeps running. Try again, or restart the app to force "
                "a clean stop.",
            )
    try:
        await run_in_threadpool(connectors_service.remove_connector, connector_id)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return result

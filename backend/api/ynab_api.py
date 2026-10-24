"""YNAB connector REST — transaction aggregation control.

Thin router over ``db.YnabConfigStore`` + ``services.aggregation.ynab``:
  * ``GET  /api/ynab/status``  — the public (non-secret) config + stats.
  * ``GET  /api/ynab/budgets`` — list the budgets ("plans", in YNAB's own current API
    vocabulary — see the service module's docstring) this token can see, for the config
    form's picker. Requires a saved token.
  * ``GET  /api/ynab/migration`` / ``POST /api/ynab/migration/{preview,start,verify}`` — the
    one-shot plan migration, a server-owned background job (``services.ynab_migration``): poll
    the GET for the newest run (each run is a persisted record — see ``api/import_runs_api``); a
    second POST while one runs is a 409.
  * ``PUT  /api/ynab/config``  — set the YNAB **personal access token**, pick a budget,
    enable/disable. A blank access token leaves the saved one unchanged (write-only-secret
    discipline); picking a budget is VALIDATED live against the token (mirrors SimpleFIN's
    ``claim_setup_token`` — a typo'd/wrong id surfaces immediately, not on the next fetch).

Auth is the app-wide ``require_auth`` dependency (this is under ``/api``). The access token
is a SECRET — accepted (write-only) but NEVER returned by this router (the status echoes
only ``has_access_token``; the operator reads it back through the separate
``GET /api/connectors/ynab/secret/access_token`` reveal path). Fetching + booking rows is
no longer a scheduled poll or a REST action — it happens only inside a Lucius turn
(``ynab_fetch_transactions`` + ``add_transactions``), per RFC ``connectors-as-data-sources``.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.db.ynab_config_store import YnabConfigStore
from backend.services import ynab_migration
from backend.services.aggregation import ynab as ynab_service

router = APIRouter()


class YnabConfigUpdate(BaseModel):
    # The YNAB personal access token. Blank/omitted → keep the saved token.
    access_token: Optional[str] = None
    # The budget ('plan') to sync. Blank string clears the pick (falls back to
    # auto-detect-if-there-is-only-one on the next sync); omitted → leave unchanged.
    budget_id: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/api/ynab/status")
def ynab_status():
    """Public config + sync stats (never the access token — only ``has_access_token``)."""
    return YnabConfigStore().public()


@router.get("/api/ynab/budgets")
async def ynab_budgets():
    """List the budgets this token can see, for the config form's picker. Requires a saved
    token (a blank one → 422, mirroring every other write-then-validate call here). Blocking
    httpx call runs off the event loop.

    ``list_budgets`` itself already converts a malformed YNAB response (non-JSON body, an
    unexpected root shape) into a typed ``ExternalServiceError`` — but this route has no
    safety net under it the way a fetch tool call does (``services.connector_fetch.fetch``'s
    own classify-and-record-status wrapping), so the second, broader ``except`` is defense
    in depth: anything that still isn't an ``OpenNetWorthError`` degrades to a clean,
    sanitized 502 instead of an unhandled 500."""
    store = YnabConfigStore()
    try:
        budgets = await asyncio.to_thread(ynab_service.list_budgets, store)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"YNAB request failed ({type(exc).__name__})") from exc
    return {"budgets": budgets}


@router.put("/api/ynab/config")
async def update_ynab_config(body: YnabConfigUpdate):
    """Configure the connector. A non-blank ``access_token`` is saved as-is (no live
    validation call — an invalid token surfaces on the next sync as ``auth_error``, same as
    Plaid's ``secret`` field); it is persisted FIRST so a budget pick arriving in the same
    request is validated against the token that request just set, not a stale saved one. A
    non-blank ``budget_id`` is validated live (``resolve_budget``) before it is saved; a
    blank one explicitly clears the pick. ``enabled`` is a plain config patch. Returns the
    public status."""
    store = YnabConfigStore()
    changes: dict = {}

    token = (body.access_token or "").strip()
    if token:
        changes["access_token"] = token
        # A freshly-pasted token means the user wants it on (mirrors every sibling
        # connector's "a fresh credential enables by default" convention).
        if body.enabled is None:
            changes["enabled"] = True

    if body.enabled is not None:
        changes["enabled"] = bool(body.enabled)

    if changes:
        store.update(changes)

    if body.budget_id is not None:
        chosen = body.budget_id.strip()
        if chosen:
            try:
                budget = await asyncio.to_thread(ynab_service.resolve_budget, store, chosen)
            except OpenNetWorthError as exc:
                raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
            store.update(
                {
                    "budget_id": budget["id"],
                    "budget_name": budget["name"],
                    "budget_currency": budget.get("currency"),
                }
            )
        else:
            store.update({"budget_id": ""})

    return store.public()


class MigrationRequest(BaseModel):
    # The plan is always an explicit pick — a token can see several, and importing the wrong one
    # writes someone else's money into the book.
    plan_id: str
    # The book already holds accounts that did not come from a YNAB migration.
    confirm_nonempty: bool = False


def _migration(kind: str, body: MigrationRequest) -> dict:
    try:
        return ynab_migration.start(kind, body.plan_id, body.confirm_nonempty)
    except ynab_migration.ConfirmationRequired as exc:
        raise HTTPException(
            409, detail={"error": "confirm_nonempty", "message": exc.message}
        ) from exc
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/ynab/migration")
def ynab_migration_status():
    """The newest migration run and whether a token is saved. Never the token."""
    return ynab_migration.status()


@router.post("/api/ynab/migration/preview", status_code=202)
def ynab_migration_preview(body: MigrationRequest):
    """Read the plan and map it; writes nothing. The result lands on the run's ``preview``."""
    return _migration("preview", body)


@router.post("/api/ynab/migration/start", status_code=202)
def ynab_migration_start(body: MigrationRequest):
    """Import the plan, then verify it. Needs ``confirm_nonempty`` when the book already has
    accounts that did not come from YNAB."""
    return _migration("import", body)


@router.post("/api/ynab/migration/verify", status_code=202)
def ynab_migration_verify(body: MigrationRequest):
    """Re-check an earlier import against YNAB's current figures; writes nothing."""
    return _migration("verify", body)

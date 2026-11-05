"""Actual Budget connector REST.

Thin router over ``db.ActualBudgetConfigStore`` and ``services.actual_sync``, all behind the
``actual_budget_sync`` flag (off: 404):
  * ``GET /api/actual-budget/status``  - the public config. Never a secret: only ``has_<secret>``
    flags and the server's host with any userinfo stripped; ``available`` is false behind the
    gateway.
  * ``PUT /api/actual-budget/config``  - set the server address and sign-in, pick a budget,
    enable or disable. A blank secret leaves the saved one (write-only discipline); the operator
    reads a secret back through ``GET /api/connectors/actual_budget/secret/<name>``. A budget
    pick is VALIDATED live against the sign-in's budget list (the way ``/api/ynab/config``
    validates one), and its name and currency are cached; a blank pick clears it.
  * ``GET /api/actual-budget/budgets`` - the budgets the saved sign-in can see, for a picker.
  * ``GET /api/actual-budget/migration`` / ``POST /api/actual-budget/migration/{preview,start,verify}``
    - the one-time import, the same server-owned job YNAB's is (``services.actual_sync.app``).
  * ``GET /api/actual-budget/sync`` / ``PUT .../sync/settings`` / ``POST .../sync/run`` /
    ``POST .../sync/conflicts/{id}/resolve`` - the scheduled sync, the same shape as YNAB's.

Refused behind the gateway (``core.config.gateway_active``): a hosted instance must not reach
its own network.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services import actual_flag, actual_sync
from backend.services.actual_sync import app as actual_app
from backend.services.ynab_migration import ConfirmationRequired


def _flag_on() -> None:
    if not actual_flag.on():
        raise HTTPException(404, "Actual import and sync are turned off.")


router = APIRouter(dependencies=[Depends(_flag_on)])


class ActualBudgetConfigUpdate(BaseModel):
    # A blank or omitted secret leaves the saved one unchanged.
    server_url: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    encryption_password: Optional[str] = None
    # The budget's cloud file id. Blank clears the pick; omitted leaves it.
    budget_id: Optional[str] = None
    enabled: Optional[bool] = None


def _refuse(exc: OpenNetWorthError) -> HTTPException:
    return HTTPException(to_http_status(exc), exc.message or str(exc))


@router.get("/api/actual-budget/status")
def actual_budget_status():
    return ActualBudgetConfigStore().public()


@router.get("/api/actual-budget/budgets")
async def actual_budget_budgets():
    try:
        ActualBudgetConfigStore().require_available()
        budgets = await asyncio.to_thread(actual_sync.list_budgets)
    except OpenNetWorthError as exc:
        raise _refuse(exc) from exc
    return {"budgets": budgets}


@router.put("/api/actual-budget/config")
async def update_actual_budget_config(body: ActualBudgetConfigUpdate):
    """A fresh sign-in that makes the connector complete enables it unless ``enabled`` says
    otherwise. The sign-in is saved FIRST, so a budget pick in the same request is checked against
    it. Returns the public status."""
    store = ActualBudgetConfigStore()
    changes = body.model_dump(exclude={"enabled", "budget_id"})
    if body.enabled is not None:
        changes["enabled"] = body.enabled
    try:
        was_configured = store.is_configured()
        store.update(changes)
        if body.enabled is None and not was_configured and store.is_configured():
            store.update({"enabled": True})
        if body.budget_id is not None:
            await _pick_budget(store, body.budget_id.strip())
    except OpenNetWorthError as exc:
        raise _refuse(exc) from exc
    return store.public()


async def _pick_budget(store: ActualBudgetConfigStore, chosen: str) -> None:
    if not chosen:
        store.update({"budget_id": ""})
        return
    budget = await asyncio.to_thread(actual_sync.resolve_budget, chosen, store)
    store.update(
        {
            "budget_id": budget["id"],
            "budget_name": budget["name"],
            "budget_currency": budget.get("currency"),
        }
    )


class MigrationRequest(BaseModel):
    # The budget is always an explicit pick: the wrong one writes someone else's money here.
    plan_id: str
    confirm_nonempty: bool = False
    # An import only: turn the sync on in this direction once it succeeds.
    sync_direction: Optional[str] = None
    account_links: Optional[dict[str, str]] = None
    same_as: Optional[dict[str, str]] = None
    category_links: Optional[dict[str, str]] = None
    recheck_of: Optional[str] = None
    # Actual account id -> the type it becomes here. Every new account needs one to import.
    account_types: Optional[dict[str, str]] = None
    # For a budget that states no currency.
    currency: Optional[str] = None


def _migration(kind: str, body: MigrationRequest) -> dict:
    try:
        return actual_app.start_migration(
            kind,
            body.plan_id,
            confirm_nonempty=body.confirm_nonempty,
            sync_direction=(body.sync_direction or None) if kind == "import" else None,
            account_links=body.account_links,
            same_as=body.same_as,
            category_links=body.category_links,
            recheck_of=body.recheck_of if kind == "preview" else None,
            account_types=body.account_types,
            currency=body.currency,
        )
    except ConfirmationRequired as exc:
        raise HTTPException(
            409, detail={"error": "confirm_nonempty", "message": exc.message}
        ) from exc
    except OpenNetWorthError as exc:
        raise _refuse(exc) from exc


@router.get("/api/actual-budget/migration")
def actual_budget_migration_status(plan_id: Optional[str] = None):
    """The newest import run, whether a sign-in is saved, and what an import of ``plan_id`` would
    be refused with right now. Never a secret."""
    try:
        return actual_app.migration_status(plan_id)
    except OpenNetWorthError as exc:
        raise _refuse(exc) from exc


@router.post("/api/actual-budget/migration/preview", status_code=202)
def actual_budget_migration_preview(body: MigrationRequest):
    """Read the budget and map it; writes nothing. The result lands on the run's ``preview``."""
    return _migration("preview", body)


@router.post("/api/actual-budget/migration/start", status_code=202)
def actual_budget_migration_start(body: MigrationRequest):
    """Import the budget, then verify it."""
    return _migration("import", body)


@router.post("/api/actual-budget/migration/verify", status_code=202)
def actual_budget_migration_verify(body: MigrationRequest):
    """Re-check an earlier import against Actual's current figures; writes nothing."""
    return _migration("verify", body)


class SyncSettings(BaseModel):
    enabled: Optional[bool] = None
    interval: Optional[str] = None
    direction: Optional[str] = None  # "both" | "from_ynab" | "to_ynab"
    # The imported budget to keep in sync; changing it forgets the previous one's sync state.
    plan_id: Optional[str] = None


class SyncResolution(BaseModel):
    keep: str  # "onw" | "ynab"
    # A category Actual has that matches several things here: which to use ("cat:<id>"/"env:<id>").
    choice: Optional[str] = None


def _sync(call, *args) -> dict:
    try:
        return call(*args)
    except OpenNetWorthError as exc:
        raise _refuse(exc) from exc


@router.get("/api/actual-budget/sync")
def actual_budget_sync_status():
    """Schedule, last and next run, the review list and notes. Never a secret or the address."""
    return _sync(actual_app.sync_status)


@router.put("/api/actual-budget/sync/settings")
def actual_budget_sync_settings(body: SyncSettings):
    return _sync(
        actual_app.sync_settings, body.enabled, body.interval, body.plan_id, body.direction
    )


@router.post("/api/actual-budget/sync/run", status_code=202)
def actual_budget_sync_run():
    """Sync now: starts a run in the background (409 while one is running); poll the status."""
    return _sync(actual_app.start_sync)


@router.post("/api/actual-budget/sync/conflicts/{conflict_id}/resolve")
def actual_budget_sync_resolve(conflict_id: str, body: SyncResolution):
    return _sync(actual_app.resolve_sync_item, conflict_id, body.keep, body.choice)

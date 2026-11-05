"""Actual Budget connector REST.

Thin router over ``db.ActualBudgetConfigStore`` and ``services.actual_sync``, all behind the
``actual_budget_sync`` flag (off: 404):
  * ``GET /api/actual-budget/status``  - the public config. Never a secret: only ``has_<secret>``
    flags and the server's host with any userinfo stripped; ``available`` is false behind the
    gateway.
  * ``PUT /api/actual-budget/config``  - set the server address and sign-in, enable or disable.
    A blank secret leaves the saved one (write-only discipline); the operator reads a secret back
    through ``GET /api/connectors/actual_budget/secret/<name>``.
  * ``GET /api/actual-budget/budgets`` - the budgets the saved sign-in can see, for a picker.

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


def _flag_on() -> None:
    if not actual_flag.on():
        raise HTTPException(404, "Actual Budget import and sync are turned off.")


router = APIRouter(dependencies=[Depends(_flag_on)])


class ActualBudgetConfigUpdate(BaseModel):
    # A blank or omitted secret leaves the saved one unchanged.
    server_url: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    encryption_password: Optional[str] = None
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
def update_actual_budget_config(body: ActualBudgetConfigUpdate):
    """A fresh sign-in that makes the connector complete enables it unless ``enabled`` says
    otherwise. Returns the public status."""
    store = ActualBudgetConfigStore()
    changes = body.model_dump(exclude={"enabled"})
    if body.enabled is not None:
        changes["enabled"] = body.enabled
    try:
        was_configured = store.is_configured()
        store.update(changes)
        if body.enabled is None and not was_configured and store.is_configured():
            store.update({"enabled": True})
    except OpenNetWorthError as exc:
        raise _refuse(exc) from exc
    return store.public()

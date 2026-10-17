"""Unified Automation CRUD, execution, and run-history API."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_store import AutomationStore
from backend.services.automations.activity import automation_activity
from backend.services.automations.options import automation_options
from backend.services.automations.runs import get_run, list_runs
from backend.services.automations.service import apply_automation_change, run_automation

router = APIRouter()


class AutomationCreate(BaseModel):
    name: str
    description: str = ""
    instructions: str
    trigger: dict[str, Any]
    permission_mode: str = "manual"
    always_allowed: list[dict[str, Any]] = Field(default_factory=list)
    fetch_hosts: list[str] = Field(default_factory=list)
    model: str = ""
    effort: str = "medium"
    status: str = "active"
    max_runs: Optional[int] = None


class AutomationPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    instructions: Optional[str] = None
    trigger: Optional[dict[str, Any]] = None
    permission_mode: Optional[str] = None
    always_allowed: Optional[list[dict[str, Any]]] = None
    fetch_hosts: Optional[list[str]] = None
    model: Optional[str] = None
    effort: Optional[str] = None
    status: Optional[str] = None
    max_runs: Optional[int] = None


class ActiveActivityItem(BaseModel):
    automation_id: str
    automation_name: str
    run_id: str
    session_id: Optional[str] = None
    status: str
    started_at: str
    finished_at: Optional[str] = None


class ScheduledActivityItem(BaseModel):
    automation_id: str
    automation_name: str
    next_run_at: str


class FailedActivityItem(ActiveActivityItem):
    error: Optional[str] = None


class AutomationActivity(BaseModel):
    active: list[ActiveActivityItem]
    scheduled: list[ScheduledActivityItem]
    failed: list[FailedActivityItem] = []


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, OpenNetWorthError):
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc)) from exc
    raise exc


@router.get("/api/automations")
def list_automations(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    rows = list(reversed(AutomationStore().all()))
    total = len(rows)
    page = rows[offset : offset + limit]
    result = {"automations": page, "showing": len(page), "total": total}
    if len(page) < total:
        result["note"] = f"Showing {len(page)} of {total} Automations; increase limit or offset."
    return result


@router.get("/api/automations/options")
def get_automation_options():
    return automation_options()


@router.get("/api/automations/activity", response_model=AutomationActivity)
def get_automation_activity():
    """The Lucius landing screen's Activity panel — active runs + upcoming schedules
    across every automation. One call, one service (``automation_activity``); the shaping
    into the two named sections happens there, not here."""
    return automation_activity()


@router.post("/api/automations", status_code=status.HTTP_201_CREATED)
def create_automation(body: AutomationCreate):
    try:
        automation = body.model_dump(exclude_none=True)
        automation["execution"] = {"kind": "lucius"}
        return apply_automation_change({"op": "create", "automation": automation})
    except Exception as exc:
        _raise_http(exc)


@router.patch("/api/automations/{automation_id}")
def update_automation(automation_id: str, body: AutomationPatch):
    try:
        return apply_automation_change(
            {
                "op": "update",
                "id": automation_id,
                "changes": body.model_dump(exclude_unset=True),
            }
        )
    except Exception as exc:
        _raise_http(exc)


@router.delete("/api/automations/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_automation(automation_id: str):
    try:
        apply_automation_change({"op": "delete", "id": automation_id})
    except Exception as exc:
        _raise_http(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/automations/{automation_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_automation_now(automation_id: str):
    try:
        return {"run": await run_automation(automation_id, trigger="manual")}
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/automations/{automation_id}/stop")
def stop_automation_now(automation_id: str):
    """Stop whichever run of this Automation is live, resolved SERVER-SIDE.

    The Stop button used to post the run's ``session_id`` from the client's cached runs list,
    which made it wrong in two different ways at once (live incident 2026-08-10): the cache
    went stale, so one tap cancelled a run that had already finished five seconds earlier, and
    when the live run had no ``session_id`` in that cache the handler hit a silent
    ``if (!run.session_id) return`` — no request, no error, a dead button. A client should
    never need to know a session id to stop a run it can see, so the id is resolved here from
    the runtime claim, which is the only authority on what is actually executing.
    """
    from backend.ai import turns
    from backend.db.automation_run_store import AutomationRunStore
    from backend.db.automation_runtime_store import AutomationRuntimeStore

    claim = AutomationRuntimeStore().get_for_automation(automation_id) or {}
    run_id = claim.get("running_run_id")
    if not run_id:
        return {"ok": True, "stopped": False, "reason": "not_running"}
    run = AutomationRunStore().find_by_id(str(run_id))
    session_id = (run or {}).get("session_id")
    if not session_id:
        return {"ok": True, "stopped": False, "reason": "no_session", "run_id": run_id}
    result = turns.cancel(str(session_id), reason="user_stop")
    return {
        "ok": True,
        "stopped": bool(result.get("stopped")),
        "reason": result.get("reason"),
        "run_id": run_id,
    }


@router.get("/api/automations/{automation_id}/runs")
def list_automation_runs(
    automation_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    if AutomationStore().find_by_id(automation_id) is None:
        raise HTTPException(404, "automation not found")
    store = AutomationRunStore()
    total = store.count_for_automation(automation_id)
    runs = list_runs(automation_id, limit=limit, offset=offset)
    result = {"runs": runs, "showing": len(runs), "total": total}
    if len(runs) < total:
        result["note"] = f"Showing {len(runs)} of {total} runs; increase limit or offset."
    return result


@router.get("/api/automations/{automation_id}/runs/{run_id}")
def get_automation_run(automation_id: str, run_id: str):
    run = get_run(automation_id, run_id)
    if run is None:
        raise HTTPException(404, "automation run not found")
    return {"run": run}

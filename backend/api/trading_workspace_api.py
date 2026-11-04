"""Delegation review and persistent workspace control surfaces."""

from fastapi import APIRouter, Query

from backend.api.trading_api import TradingRoute
from backend.models.trading import (
    DelegationActivation,
    DelegationCreate,
    MandateAmendment,
    RunAction,
)
from backend.services.trading import delegation
from backend.services.trading.work_records import get_work_record, workspace_files

router = APIRouter(prefix="/api/trading", tags=["Trading"], route_class=TradingRoute)


@router.post("/delegations/review")
def review(body: DelegationCreate):
    return delegation.review_delegation(body.model_dump(mode="json"))


@router.post("/delegations", status_code=201)
async def create(body: DelegationCreate):
    return await delegation.create_delegation(body.model_dump(mode="json"))


@router.post("/workspaces/{workspace_id}/activate", status_code=201)
async def activate(workspace_id: str, body: DelegationActivation):
    return await delegation.activate_delegation(workspace_id, body.model_dump(mode="json"))


@router.get("/workspaces/{workspace_id}/overview")
def overview(workspace_id: str):
    return delegation.workspace_overview(workspace_id)


@router.post("/workspaces/{workspace_id}/amendments")
def amend(workspace_id: str, body: MandateAmendment):
    return delegation.amend_mandate(workspace_id, body.model_dump(mode="json"))


@router.post("/workspaces/{workspace_id}/actions")
async def action(workspace_id: str, body: RunAction):
    return await delegation.workspace_action(workspace_id, body.model_dump(mode="json"))


@router.get("/runs/{run_id}/files")
def files(run_id: str, name: str | None = None, version: int | None = Query(None, ge=1)):
    return workspace_files(run_id, name=name, version=version)


@router.get("/runs/{run_id}/work/{record_id}")
def work_record(run_id: str, record_id: str):
    return get_work_record(run_id, record_id)

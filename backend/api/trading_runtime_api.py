"""Human control-plane operations for trading participants and scoped grants."""

from decimal import Decimal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.core.trading_runtime import run_trading_agent
from backend.services.trading_access import issue_access, revoke_access
from backend.services.trading_scripts import execute_strategy
from backend.services.trading_usage import reconcile_usage

router = APIRouter()


class AgentCycle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None
    effort: str = "medium"


class StrategySource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=64000)


class AccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expires_in_seconds: int = Field(default=3600, ge=60, le=86400)


class UsageReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    receipt_id: str = Field(min_length=1, max_length=120)
    turn_id: str | None = None
    additional_cost_usd: Decimal = Field(ge=0)


@router.post("/api/trading/runs/{run_id}/usage-reconciliation")
def reconcile_billing(run_id: str, body: UsageReceipt):
    return reconcile_usage(run_id, **body.model_dump())


@router.post("/api/trading/runs/{run_id}/agent")
async def agent_cycle(run_id: str, body: AgentCycle):
    return await run_trading_agent(run_id, model=body.model, effort=body.effort)


@router.post("/api/trading/runs/{run_id}/script")
async def strategy_cycle(run_id: str, body: StrategySource):
    return await execute_strategy(run_id, body.source)


@router.post("/api/trading/runs/{run_id}/access")
def grant_access(run_id: str, body: AccessRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return issue_access(run_id, body.expires_in_seconds)


@router.delete("/api/trading/runs/{run_id}/access")
def remove_access(run_id: str):
    return revoke_access(run_id)

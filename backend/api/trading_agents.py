"""TradingAgents — multi-agent stock research as a plugin of Lucius (flag ``trading_agents``).

GET    /api/trading-agents/status                 — availability, install, models, caps, spend, queue
GET    /api/trading-agents/config                 — the settings and which data-vendor keys are saved
PUT    /api/trading-agents/config                 — PATCH-shaped; blank key keeps a secret, null clears it
POST   /api/trading-agents/install                — 202; progress through status + data events
POST   /api/trading-agents/disclaimer             — accept the research-only notice
POST   /api/trading-agents/estimate               — expected cost of a run, no cap check, no write
GET    /api/trading-agents/context-preview        — exactly what a run would send, plus the disclosure
POST   /api/trading-agents/runs                   — 202 {run_id}
GET    /api/trading-agents/runs                   — ?ticker=&status=&limit=&offset=
GET    /api/trading-agents/runs/{id}              — the full run, reports uncapped
POST   /api/trading-agents/runs/{id}/cancel
DELETE /api/trading-agents/runs/{id}              — 204, a finished run only

Spend is ``GET /api/cost/trading-agents`` (``api/cost.py``). A run is research, never an order.
Each route validates, calls one service or the runner, and shapes the response. The service raises
the typed error while the flag is off (a router never branches on it); a typed error is answered
with its HTTP status and ``{code, message, context}``.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, TypeVar

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.ai.trading_agents import runner
from backend.ai.trading_agents.rates import rate_for_ref
from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services.trading_agents import queries

router = APIRouter(prefix="/api/trading-agents")

T = TypeVar("T")


def _refusal(exc: OpenNetWorthError) -> HTTPException:
    return HTTPException(
        to_http_status(exc),
        {"code": exc.code, "message": exc.message or str(exc), "context": exc.context},
    )


def _run(call: Callable[..., T], *args, **kwargs) -> T:
    try:
        return call(*args, **kwargs)
    except OpenNetWorthError as exc:
        raise _refusal(exc) from exc


async def _run_async(call: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
    try:
        return await call(*args, **kwargs)
    except OpenNetWorthError as exc:
        raise _refusal(exc) from exc


class DisclaimerBody(BaseModel):
    accepted: bool


class EstimateBody(BaseModel):
    ticker: str = Field(..., max_length=32)
    analysts: Optional[list[str]] = None


class RunBody(BaseModel):
    ticker: str = Field(..., max_length=32)
    trade_date: Optional[str] = Field(None, max_length=10)
    analysts: Optional[list[str]] = None
    context_level: Optional[str] = Field(None, max_length=16)


class ConfigBody(BaseModel):
    """Every field optional and untyped: absent keeps, ``null`` resets a setting or clears a key,
    and the service's validators (not pydantic's coercion) judge each value."""

    model_config = ConfigDict(extra="forbid")

    deep_model: Any = None
    quick_model: Any = None
    analysts: Any = None
    max_debate_rounds: Any = None
    max_risk_rounds: Any = None
    max_tool_rounds: Any = None
    max_llm_calls: Any = None
    bridge_concurrency: Any = None
    per_run_cap_usd: Any = None
    monthly_cap_usd: Any = None
    context_level: Any = None
    output_language: Any = None
    sec_edgar_user_agent: Any = None
    alpha_vantage_api_key: Any = None
    fred_api_key: Any = None


@router.get("/status")
def get_status():
    return queries.status(runner.pending_count())


@router.get("/config")
def get_config():
    return _run(queries.get_config)


@router.put("/config")
def put_config(body: ConfigBody):
    return _run(queries.update_config, body.model_dump(exclude_unset=True))


@router.post("/install", status_code=202)
async def install_environment():
    # async: the install is a task of the server's event loop, so it needs the running loop.
    _run(queries.start_install)
    return queries.status(runner.pending_count())


@router.post("/disclaimer")
def accept_disclaimer(body: DisclaimerBody):
    if not body.accepted:
        raise HTTPException(422, "accepted must be true to record the notice.")
    return _run(queries.accept_disclaimer)


@router.post("/estimate")
def estimate_run(body: EstimateBody):
    return _run(queries.estimate, body.ticker, body.analysts, rate_for_ref)


@router.get("/context-preview")
def context_preview(
    ticker: str = Query(..., max_length=32),
    context_level: Optional[str] = Query(None, max_length=16),
):
    return _run(queries.context_preview, ticker, context_level)


@router.post("/runs", status_code=202)
async def start_run(body: RunBody):
    run_id = await _run_async(
        runner.submit,
        body.ticker,
        trade_date=body.trade_date,
        analysts=body.analysts,
        context_level=body.context_level,
    )
    return {"run_id": run_id}


@router.get("/runs")
def list_runs(
    ticker: Optional[str] = Query(None, max_length=32),
    status: Optional[str] = Query(None, max_length=32),
    limit: int = Query(20, ge=1, le=queries.MAX_PAGE),
    offset: int = Query(0, ge=0),
):
    return _run(
        queries.list_runs, ticker_symbol=ticker, status_filter=status, limit=limit, offset=offset
    )


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    return _run(queries.get_run, run_id)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    return await _run_async(runner.cancel, run_id)


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: str):
    _run(queries.delete_run, run_id)
    return Response(status_code=204)

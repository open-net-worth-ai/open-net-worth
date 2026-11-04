"""Human read-only market, statement and linked-evidence views."""

from typing import Literal

from fastapi import APIRouter, Query

from backend.api.trading_api import TradingRoute
from backend.services.trading.evidence import get_evidence
from backend.services.trading.market import get_market_series
from backend.services.trading.statements import get_statement

router = APIRouter(prefix="/api/trading", tags=["Trading"], route_class=TradingRoute)


@router.get("/runs/{run_id}/market-series")
def market_series(
    run_id: str,
    instrument_key: str | None = None,
    limit: int = Query(500, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return get_market_series(run_id, key=instrument_key, limit=limit, offset=offset)


@router.get("/runs/{run_id}/statement")
def statement(run_id: str):
    return get_statement(run_id)


@router.get("/runs/{run_id}/evidence/{kind}/{record_id}")
def evidence(run_id: str, kind: Literal["order", "fill", "event"], record_id: str):
    return get_evidence(run_id, kind, record_id)

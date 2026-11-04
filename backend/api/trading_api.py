"""Human control plane and read projections for autonomous trading workspaces."""

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.routing import APIRoute

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.models.trading import (
    ComparisonCreate,
    Decision,
    OrderIntent,
    RunAction,
    RunCreate,
    WorkspaceCreate,
)
from backend.services import trading


class TradingRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def mapped(request):
            try:
                return await handler(request)
            except OpenNetWorthError as exc:
                raise HTTPException(
                    to_http_status(exc), {"code": exc.code, "message": exc.message}
                ) from exc

        return mapped


router = APIRouter(prefix="/api/trading", tags=["Trading"], route_class=TradingRoute)


@router.get("/workspaces")
def workspaces(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return trading.list_workspaces(limit=limit, offset=offset)


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceCreate):
    return trading.create_workspace(body.model_dump(mode="json"))


@router.get("/workspaces/{workspace_id}")
def workspace(workspace_id: str):
    return trading.get_workspace(workspace_id)


@router.patch("/workspaces/{workspace_id}")
def update_workspace(workspace_id: str, body: dict = Body(...)):
    return trading.update_workspace(workspace_id, body)


@router.get("/workspaces/{workspace_id}/runs")
def workspace_runs(
    workspace_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)
):
    return trading.list_runs(workspace_id, limit=limit, offset=offset)


@router.post("/workspaces/{workspace_id}/runs", status_code=201)
def create_run(workspace_id: str, body: RunCreate):
    return trading.create_run(workspace_id, body.model_dump(mode="json"))


@router.get("/runs")
def runs(
    workspace_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return trading.list_runs(workspace_id, limit=limit, offset=offset)


@router.get("/runs/{run_id}")
def run(run_id: str):
    return trading.get_run(run_id)


@router.post("/runs/{run_id}/actions")
async def action(run_id: str, body: RunAction):
    return await trading.apply_run_action(run_id, body.model_dump(mode="json"))


@router.get("/runs/{run_id}/environment")
def environment(run_id: str):
    return trading.get_environment(run_id)


@router.get("/runs/{run_id}/accounts")
def accounts(run_id: str):
    return trading.get_account(run_id)


@router.get("/runs/{run_id}/market-data")
def market_data(run_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return trading.get_market_data(run_id, limit=limit, offset=offset)


@router.post("/runs/{run_id}/observations")
def observations(run_id: str, body: list[dict] = Body(...)):
    return trading.ingest_observations(run_id, body)


@router.get("/runs/{run_id}/orders")
def orders(run_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return trading.list_orders(run_id, limit=limit, offset=offset)


@router.post("/runs/{run_id}/orders", status_code=201)
async def submit_order(run_id: str, body: OrderIntent):
    return await trading.submit_order(run_id, body.model_dump(mode="json"))


@router.delete("/runs/{run_id}/orders/{order_id}")
async def cancel_order(run_id: str, order_id: str):
    return await trading.cancel_order(run_id, order_id)


@router.get("/runs/{run_id}/fills")
def fills(run_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return trading.list_fills(run_id, limit=limit, offset=offset)


@router.get("/runs/{run_id}/events")
def events(
    run_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    after: int | None = Query(None, ge=0),
):
    return trading.list_events(run_id, limit=limit, offset=offset, after=after)


@router.post("/runs/{run_id}/decisions", status_code=201)
def decision(run_id: str, body: Decision):
    return trading.record_decision(run_id, body.model_dump(mode="json"))


@router.get("/runs/{run_id}/performance")
def performance(run_id: str):
    return trading.get_performance(run_id)


@router.get("/comparisons")
def comparisons(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return trading.list_comparisons(limit=limit, offset=offset)


@router.post("/comparisons", status_code=201)
def create_comparison(body: ComparisonCreate):
    return trading.create_comparison(body.model_dump(mode="json"))


@router.get("/comparisons/{comparison_id}")
def comparison(comparison_id: str):
    return trading.get_comparison(comparison_id)

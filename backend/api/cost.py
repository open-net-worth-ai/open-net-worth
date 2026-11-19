"""Cost analytics API — token usage, spend summary, and monthly budget.

Endpoints:
  GET  /api/cost/summary         — aggregate token usage (auth-gated by app-wide dep)
  DELETE /api/cost/log           — truncate the cost log
  GET  /api/cost/budget          — read the monthly AI-spend budget
  PUT  /api/cost/budget          — set (or clear) the monthly AI-spend budget
  GET  /api/cost/budget/status   — current-month spend vs budget
  GET  /api/cost/trading-agents  — TradingAgents spend, runs and caps (flag ``trading_agents``)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.db.app_config_store import AppConfigStore
from backend.services import cost as cost_service
from backend.services.trading_agents import costs as trading_agents_costs

router = APIRouter()


@router.get("/api/cost/summary")
def cost_summary(
    days: int = 30,
    range_: Optional[str] = Query(None, alias="range"),
    tz: Optional[str] = Query(None),
):
    """Aggregate AI token usage and cost.

    Query params:
        days  — trailing window in calendar days (default 30; 0 = all time).
        range — fixed-day preset (``today`` | ``yesterday``). When set it defines an
                inclusive date window (in ``tz``) and overrides ``days``.
        tz    — viewer's IANA timezone (e.g. ``Asia/Singapore``) for day boundaries
                and per-day buckets; absent/invalid → UTC.
    """
    start, end = cost_service.resolve_preset(range_, tz)
    return cost_service.cost_summary(days=days, start=start, end=end, tz=tz)


@router.delete("/api/cost/log")
def clear_cost_log():
    """Truncate the cost log. Irreversible."""
    cost_service.clear_cost_log()
    return {"ok": True}


# ── Budget endpoints ────────────────────────────────────────────────────────


class BudgetBody(BaseModel):
    budget_usd: Optional[float] = None


@router.get("/api/cost/budget")
def get_cost_budget():
    """Return the monthly AI-spend budget.  ``budget_usd`` is ``null`` when
    no budget has been configured."""
    return {"budget_usd": AppConfigStore().get_budget()}


@router.put("/api/cost/budget")
def put_cost_budget(body: BudgetBody):
    """Set (or clear) the monthly AI-spend budget.  Pass ``budget_usd: null``
    or omit to clear.  A value ≤ 0 is rejected with HTTP 422."""
    val = body.budget_usd
    if val is not None and val <= 0:
        raise HTTPException(
            status_code=422,
            detail="budget_usd must be a positive number, or null to clear",
        )
    stored = AppConfigStore().set_budget(val)
    return {"budget_usd": stored}


@router.get("/api/cost/budget/status")
def get_budget_status(tz: Optional[str] = Query(None)):
    """Month-to-date spend vs the configured monthly budget.

    ``tz`` is the viewer's IANA timezone so the "this month" boundary matches the rest
    of the panel; absent/invalid → UTC. Returns ``pct_used``, ``approaching`` (≥ 80%),
    and ``over_budget`` (≥ 100%). All three fields are ``null`` when no budget is set.
    """
    budget = AppConfigStore().get_budget()
    return cost_service.cost_budget_status(budget_usd=budget, tz=tz)


@router.get("/api/cost/trading-agents")
def trading_agents_cost(
    days: int = 30,
    range_: Optional[str] = Query(None, alias="range"),
    start: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    tz: Optional[str] = Query(None),
):
    """TradingAgents spend from the run rows: totals, month vs cap, by role / model / ticker /
    day, recent runs. ``days`` / ``range`` / ``tz`` are those of ``/api/cost/summary``; an explicit
    inclusive ``start`` / ``end`` (``YYYY-MM-DD``, in ``tz``) takes precedence over both."""
    if start is None and end is None:
        start, end = cost_service.resolve_preset(range_, tz)
    return trading_agents_costs.summary(days=days, start=start, end=end, tz=tz)

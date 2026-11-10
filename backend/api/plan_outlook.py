"""
The Outlook's retirement simulation — the ONE answer the screen and Lucius both read.

    POST /api/plan/outlook             the fan for these levers (+ Rich/Broke/Dead on request)
    POST /api/plan/outlook/insights    safe-to-retire age, "+X/mo lifts this to 90%", FIRE-number age
    GET  /api/plan/scenario-odds       live odds for every saved scenario

Thin: every body is the Outlook's lever vocabulary, every lever omitted is the saved plan's,
and the math is ``services.retirement_plan`` -> ``services.montecarlo``. Compute-only (a POST
only because the levers are a body), deterministic: identical inputs give identical numbers.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.api.data_change_transport import compute_only
from backend.core.errors import OpenNetWorthError, to_http_status
from backend.models.retirement import OutlookLevers
from backend.services import retirement_plan

router = APIRouter()


class OutlookRequest(BaseModel):
    levers: OutlookLevers = Field(default_factory=OutlookLevers)
    quick: bool = Field(
        default=False, description="Fewer paths (the first rows of the same ones) while dragging."
    )
    outcomes: bool = Field(default=False, description="Also return the Rich/Broke/Dead split.")


class InsightsRequest(BaseModel):
    levers: OutlookLevers = Field(default_factory=OutlookLevers)


def _call(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/plan/outlook")
@compute_only
def post_outlook(body: OutlookRequest):
    return _call(retirement_plan.outlook, body.levers, quick=body.quick, outcomes=body.outcomes)


@router.post("/api/plan/outlook/insights")
@compute_only
def post_insights(body: InsightsRequest):
    return _call(retirement_plan.insights, body.levers)


@router.get("/api/plan/scenario-odds")
def get_scenario_odds():
    return {"odds": _call(retirement_plan.scenario_odds)}

"""
GET    /api/holding-plans                   — every plan, with ticker / name / held joined in
PUT    /api/holding-plans/{instrument_id}   — replace one instrument's plan (omitted = blank)
DELETE /api/holding-plans/{instrument_id}   — delete it

Validation and the write itself live in ``services.holding_plans.apply_plan_change``.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.db.holding_plan_store import PLAN_FIELDS
from backend.services import holding_plans

router = APIRouter()


class HoldingPlanBody(BaseModel):
    role: Optional[Literal["core", "satellite", "speculative"]] = None
    thesis: Optional[str] = None
    exit_if: Optional[str] = None
    stop_below: Optional[float] = None
    buy_below: Optional[float] = None
    buy_above: Optional[float] = None
    fair_value: Optional[float] = None
    trim_above: Optional[float] = None


def _apply(change: dict) -> dict:
    try:
        return holding_plans.apply_plan_change(change)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/holding-plans")
def list_holding_plans():
    return {"plans": holding_plans.list_plans()}


@router.put("/api/holding-plans/{instrument_id}")
def put_holding_plan(instrument_id: str, body: HoldingPlanBody):
    fields = body.model_dump(exclude_none=True)
    clear = [k for k in PLAN_FIELDS if k not in fields]
    return _apply({"op": "set", "instrument_id": instrument_id, "fields": fields, "clear": clear})


@router.delete("/api/holding-plans/{instrument_id}")
def delete_holding_plan(instrument_id: str):
    return _apply({"op": "delete", "instrument_id": instrument_id})

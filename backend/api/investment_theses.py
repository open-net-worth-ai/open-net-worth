"""
GET    /api/investment-theses                   — every thesis, with ticker / name / held joined in
PUT    /api/investment-theses/{instrument_id}   — replace one instrument's thesis (omitted = blank)
DELETE /api/investment-theses/{instrument_id}   — delete it

Validation and the write itself live in ``services.investment_theses.apply_thesis_change``.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.db.investment_thesis_store import THESIS_FIELDS
from backend.services import investment_theses

router = APIRouter()


class InvestmentThesisBody(BaseModel):
    role: Optional[Literal["core", "satellite", "speculative"]] = None
    reason: Optional[str] = None
    breaks_if: Optional[str] = None
    stop_below: Optional[float] = None
    buy_below: Optional[float] = None
    buy_above: Optional[float] = None
    fair_value: Optional[float] = None
    trim_above: Optional[float] = None


def _apply(change: dict) -> dict:
    try:
        return investment_theses.apply_thesis_change(change)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/investment-theses")
def list_investment_theses():
    return {"theses": investment_theses.list_theses()}


@router.put("/api/investment-theses/{instrument_id}")
def put_investment_thesis(instrument_id: str, body: InvestmentThesisBody):
    fields = body.model_dump(exclude_none=True)
    clear = [k for k in THESIS_FIELDS if k not in fields]
    return _apply({"op": "set", "instrument_id": instrument_id, "fields": fields, "clear": clear})


@router.delete("/api/investment-theses/{instrument_id}")
def delete_investment_thesis(instrument_id: str):
    return _apply({"op": "delete", "instrument_id": instrument_id})

"""
Income (dividend / interest / distribution) API (P2-INV2).

A THIN router over services.income — it marshals input and shapes the response; every
income figure, yield, forecast, and base-currency conversion is computed in
services.income (the math home), never here. Respects the dependency rule (api → services).

Recording income is a money mutation that converges on the single
``income_service.apply_income_change`` choke point (the same entrypoint the AI apply tool
uses) — a cash dividend books ONE income txn through the ledger choke point (no
double-counting); a DRP acquires a lot and books no cash.

Routes:
    GET    /api/income                 — income summary (by-instrument/by-kind/trailing/yield)
    GET    /api/income/forecast        — annualized forward-income estimate
    GET    /api/positions/{id}/income  — income summary scoped to one position
    POST   /api/income                 — record a dividend (cash or DRP)
    DELETE /api/income/{id}            — delete an income event

See .claude/rfcs/phase2/inv2-dividends-income.md.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.models.income_event import IncomeEventCreate
from backend.services import dividend_calendar as dividend_calendar_service
from backend.services import income as income_service

router = APIRouter()


@router.get("/api/income")
def get_income(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    instrument_id: Optional[str] = None,
    account_id: Optional[str] = None,
):
    """Income over a date range / instrument / account. Base figures recomputed from the
    native leg + current FX (the stored snapshot is only a write-time cache)."""
    return income_service.income_summary(
        date_from=date_from,
        date_to=date_to,
        instrument_id=instrument_id,
        account_id=account_id,
        include_event_labels=True,
    )


@router.get("/api/income/forecast")
def get_income_forecast(horizon_months: int = 12):
    """Annualized forward-income estimate per holding + portfolio total + implied yield.
    Each row labels its rate source (declared/trailing) — an estimate, not a guarantee."""
    return income_service.forecast_income(horizon_months=horizon_months)


@router.get("/api/income/calendar")
def get_income_calendar(days: int = 90):
    """Upcoming dividend payments per holding within `days` (clamped 1–365 in the service).
    Each row is "scheduled" (a Yahoo-declared ex-date) or "estimated" (projected from the
    holding's dividend history); non-payers are excluded and amounts are estimates the client
    labels as such (ADR 0020). Reads cached instrument metadata — never hits the network."""
    return dividend_calendar_service.upcoming_payments(days=days)


@router.get("/api/positions/{position_id}/income")
def get_position_income(position_id: str, with_predecessors: bool = False):
    """Income summary scoped to one position (received income + yield-on-cost);
    with_predecessors adds a sold-out-then-rebought holding's earlier position."""
    return income_service.income_summary(
        position_id=position_id, include_event_labels=True, with_predecessors=with_predecessors
    )


@router.post("/api/income")
def record_income(body: IncomeEventCreate):
    """Record a dividend / interest / distribution. A cash dividend books ONE income txn
    through the ledger choke point (tagged "dividend"); a DRP acquires a lot and books no
    cash. The service validates (unknown position → 422) BEFORE any write."""
    change = {
        "type": "record",
        "position_id": body.position_id,
        "paid_at": body.paid_at,
        "income_kind": (
            body.income_kind.value if hasattr(body.income_kind, "value") else body.income_kind
        ),
        "quantity": body.quantity,
        "amount_per_unit": body.amount_per_unit,
        "gross_amount": body.gross_amount,
        "tax_withheld": body.tax_withheld,
        "currency": body.currency,
        "account_id": body.account_id,
        "is_drp": body.is_drp,
        "drp_quantity": body.drp_quantity,
        "drp_unit_cost": body.drp_unit_cost,
        "note": body.note,
        "source": "manual",
    }
    try:
        return income_service.apply_income_change(change)
    except OpenNetWorthError as exc:  # NotFoundError / ValidationError → 422
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.delete("/api/income/{event_id}")
def delete_income(event_id: str):
    """Delete an income event (the booked cash txn / DRP lot stay; undo via ledger/lots)."""
    try:
        return income_service.apply_income_change({"type": "delete", "event_id": event_id})
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/income/dividends")
def get_income_dividends():
    """Dividends received year-to-date, the next 90 days of payments, and the 12-month forward
    forecast bucketed by month. `GET /api/income`'s own summary exposes trailing-12m and
    all-time, never YTD — which is why this exists."""
    from backend.services import dividend_summary as dividend_summary_service

    return dividend_summary_service.income_aggregates()

"""
GET /api/cashflow

Returns a month-by-month cash flow table = past actuals + forward projection. The
projection math lives in ``backend.services.cashflow`` (RFC P1-A5 §3.2/§3.3); this
router only parses the request, overlays committed obligations, and calls
``build_projection(...)``.

Also GET /api/cashflow/flow?month=YYYY-MM&compact= — one month's money-flow (Sankey) graph,
built by ``backend.services.cash_flow_graph``.

Query params (/api/cashflow):
  months               int   (1–60, default 40)  — forward projection length
  history_months       int   (0–24, default 6)   — past actual months to prepend
  income_override      float override the rolling-average monthly income
  expense_override      float override the rolling-average monthly expenses
  include_fixed_deposits bool add fixed-deposit balances to the runway (default false)
  include_investments    bool add investment positions + settlement cash to the runway
                          (default false)

Response shape:
  {
    "months": [ ...MonthRow... ],
    "low_balance_threshold": 5000,
    "cash_balance_base":     float,
    "fixed_deposit_base":    float,
    "investments_base":      float,
    "include":               {"fixed_deposits": bool, "investments": bool},
    "liquid_balance_base":   float,
    "actual_income_avg":     float,
    "actual_expenses_avg":   float,
    "monthly_income_avg":    float,
    "monthly_expenses_avg":  float,
    "baseline_source":       "transactions" | "profile" | "mixed",
    "income_runway_months":  float | null,
    "simulation_active":     bool,
    "income_override":       float | null,
    "expense_override":      float | null
  }
"""

from typing import Optional

from fastapi import APIRouter, Query

from backend.services.cashflow import (
    DEFAULT_EXPENSE_GROWTH_PCT,
    DEFAULT_INCOME_GROWTH_PCT,
    HISTORY_MONTHS,
    PROJECTION_MONTHS,
    build_projection,
)

router = APIRouter()


@router.get("/api/cashflow")
def get_cashflow(
    months: int = PROJECTION_MONTHS,
    income_override: Optional[float] = None,
    expense_override: Optional[float] = None,
    include_fixed_deposits: bool = False,
    include_investments: bool = False,
    expense_growth_pct: float = DEFAULT_EXPENSE_GROWTH_PCT,
    income_growth_pct: float = DEFAULT_INCOME_GROWTH_PCT,
    history_months: int = HISTORY_MONTHS,
    include_commitments: bool = True,
):
    months = max(1, min(months, 60))
    expense_growth_pct = max(0.0, min(expense_growth_pct, 20.0))
    income_growth_pct = max(0.0, min(income_growth_pct, 20.0))
    history_months = max(0, min(history_months, 24))

    # Overlay committed installments (outflows) + funding draws (inflows) so lumpy obligations
    # show up in the cash-flow line. Best-effort — a malformed commitment never breaks cash flow.
    extra_events = None
    if include_commitments:
        try:
            from backend.services import commitments as commitments_service

            extra_events = commitments_service.projected_events()
        except Exception:
            extra_events = None

    return build_projection(
        months,
        income_override,
        expense_override,
        include_fixed_deposits=include_fixed_deposits,
        include_investments=include_investments,
        expense_growth_pct=expense_growth_pct,
        income_growth_pct=income_growth_pct,
        history_months=history_months,
        extra_events=extra_events,
    )


@router.get("/api/cashflow/projections")
def get_cashflow_projections():
    """Forward-looking figures: how much of the monthly bill passive income already covers, and
    the liquid-cash depletion curve point by point."""
    from backend.services import cashflow_projections as cashflow_projections_service

    return cashflow_projections_service.projections()


@router.get("/api/cashflow/flow")
def get_cashflow_flow(
    month: Optional[str] = None,
    compact: bool = False,
    period: Optional[str] = None,
    tag: Optional[list[str]] = Query(None),
):
    """The money-flow (Sankey) graph for one month — default the last full month — or for a
    whole `period` (this_month, last_month, 3m, 6m, 12m, ytd). `tag` (repeatable) keeps only
    transactions carrying one of them. `compact` returns the Dashboard card's three columns
    instead of the full five."""
    from fastapi import HTTPException

    from backend.core.errors import ValidationError
    from backend.services import cash_flow_graph as cash_flow_graph_service

    try:
        return cash_flow_graph_service.cash_flow_graph(
            month, compact=compact, period=period, tags=tag
        )
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

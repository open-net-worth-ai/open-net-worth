"""Forward-looking Overview figures: the passive-income crossover and the cash runway.

Both widgets were handed already-server-computed SCALARS (annual passive income, average
monthly expenses, liquid balance) and then did their own arithmetic on them: dividing the
annual figure by 12, taking a percentage of expenses, and projecting a month-by-month cash
depletion curve. Those are money operations, so they belong here — the widget plots the
points it is given.
"""

from __future__ import annotations

from backend.core.fx import base_currency
from backend.services import cashflow as cashflow_service
from backend.services import income as income_service

MONTHS_PER_YEAR = 12
# Enough runway to show the curve reaching zero plus a little headroom, capped so a book with
# effectively no burn doesn't project a 500-month chart.
RUNWAY_HORIZON_CAP = 60
RUNWAY_DEFAULT_HORIZON = 24


def _cashflow_snapshot() -> dict:
    """The same one-month, no-history projection the widgets used to fetch themselves."""
    return cashflow_service.build_projection(months=1, history_months=0)


def crossover() -> dict:
    """Passive income vs spending: how much of the monthly bill your holdings already cover."""
    snapshot = _cashflow_snapshot()
    expenses_monthly = float(snapshot.get("monthly_expenses_avg") or 0.0)
    annual_passive = float(
        (income_service.forecast_income(horizon_months=MONTHS_PER_YEAR)).get("annual_total_base")
        or 0.0
    )
    passive_monthly = round(annual_passive / MONTHS_PER_YEAR, 2)
    return {
        "passive_monthly": passive_monthly,
        "annual_passive": round(annual_passive, 2),
        "expenses_monthly": round(expenses_monthly, 2),
        "coverage_pct": (
            round(min(100.0, (passive_monthly / expenses_monthly) * 100), 2)
            if expenses_monthly > 0
            else 0.0
        ),
        "to_go_monthly": round(max(0.0, expenses_monthly - passive_monthly), 2),
        # False when there is no spending history to divide by — the widget shows an honest
        # empty state rather than a 0% bar that reads like a real measurement.
        "known": expenses_monthly > 0,
    }


def _trajectory(cash: float, burn: float, growth_pct: float, horizon: int) -> list[float]:
    """Cash depletion curve, compounding `burn` at `growth_pct`/yr like `_runway_with_growth`
    does for the headline `runway_months` — the two must cross zero on the same month."""
    points = [round(cash, 2)]
    remaining = cash
    for m in range(horizon):
        remaining = max(0.0, remaining - cashflow_service.expense_at_month(burn, growth_pct, m))
        points.append(round(remaining, 2))
    return points


def runway() -> dict:
    """Liquid cash drawn down at the average burn rate — the depletion curve, point by point."""
    record = cashflow_service.runway_from_projection(_cashflow_snapshot())
    cash = round(record["cash"], 2)
    burn = round(record["burn"], 2)
    months = record["months"]
    horizon = (
        min(RUNWAY_HORIZON_CAP, max(int(months) + 2, 2))
        if months is not None
        else RUNWAY_DEFAULT_HORIZON
    )
    return {
        "base_currency": base_currency(),
        "cash": cash,
        "burn": burn,
        "runway_months": months,
        "horizon_months": horizon,
        "trajectory": _trajectory(cash, burn, record["expense_growth_pct"], horizon),
        "known": burn > 0,
    }


def projections() -> dict:
    return {"crossover": crossover(), "runway": runway()}

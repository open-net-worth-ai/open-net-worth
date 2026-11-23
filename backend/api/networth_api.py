"""
GET /api/net-worth/history?period=1M|3M|6M|YTD|1Y|3Y|5Y|ALL

The net-worth-over-time series for the Overview line chart. Thin over
`ledger_service.net_worth_history()` (the canonical computed-not-stored, base-correct
monthly reconstruction) with a period window applied: the full series is filtered to the
points on/after the period's start date (ALL = the whole series, unfiltered).

Distinct from the existing `/api/networth/history` (the all-time stacked-band chart on
Overview) — this one is the period-selectable single-line view. Both read the SAME
service projection, so they never drift; this route only adds the window selector.

Empty / young book → `{ history: [], base_currency, period }` (never 500): the service
returns `[]` when there is nothing to reconstruct, and an unknown period falls back to 1Y.

Response: { history: [{date, classes, liabilities, accounts, net_worth, market_tracked}],
            base_currency: str, period: str }
"""

from fastapi import APIRouter, HTTPException

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services import ledger as ledger_service
from backend.services import returns as returns_service

router = APIRouter()

_PERIODS = ("1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "ALL")


@router.get("/api/net-worth/history")
def get_net_worth_history(period: str = "1Y"):
    period = (period or "1Y").upper()
    if period not in _PERIODS:
        period = "1Y"

    # Same fold, same refusal as GET /api/net-worth — an unclassifiable account row must not
    # reach the browser as a bodyless 500.
    try:
        history = ledger_service.net_worth_history()
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if period != "ALL" and history:
        start, _end = returns_service.resolve_period(period)
        history = [p for p in history if p.get("date", "") >= start]

    return {
        "history": history,
        "base_currency": ledger_service.base_currency(),
        "period": period,
    }


@router.get("/api/net-worth/stats")
def get_net_worth_stats():
    """The monthly net-worth series plus everything derived FROM it: per-range deltas with
    their saved-vs-market split, the month-to-date change, and which composition bands carry
    data. Differencing two points of a series is money math, so it happens server-side."""
    from backend.services import networth_stats as networth_stats_service

    try:
        return networth_stats_service.networth_stats()
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc

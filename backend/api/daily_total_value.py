"""
Daily total-value + sparkline read API (investments redesign — sys-daily-series lane).

Thin router over services.daily_total_value: validate enum → call ONE service → response.
Mirrors api/returns.py in structure (no business logic here).

Routes:
    GET /api/portfolio/total-value?period={7D|1M|3M|6M|YTD|1Y|3Y|5Y|ALL}  → total_value_series(...)
    GET /api/portfolio/sparkline?days=30                          → sparkline_series(...)

Bad period → 422 (FastAPI enum). Empty book → 200 with series:[], has_real_daily_history:false
(never 500). Computed-not-stored — pure read, no write choke point.
"""

from __future__ import annotations

from enum import Enum

from fastapi import APIRouter, Query

from backend.services import daily_total_value as dtv_service
from backend.services import returns as returns_service

router = APIRouter()


class Period(str, Enum):
    D7 = "7D"
    M1 = "1M"
    M3 = "3M"
    M6 = "6M"
    YTD = "YTD"
    Y1 = "1Y"
    Y3 = "3Y"
    Y5 = "5Y"
    ALL = "ALL"


@router.get("/api/portfolio/total-value")
def get_total_value(period: Period = Period.Y1):
    """Daily total-value + total-return series for the investment book over the window.

    Returns {base_currency, grain, start, end, series[], data_quality}. The series
    overlays reinvested+cash income on the price-only reconstructed value. Empty book or
    no price history → series:[], has_real_daily_history:false (never 500).
    """
    start, end = returns_service.resolve_period(period.value)
    return dtv_service.total_value_series(start, end)


@router.get("/api/portfolio/sparkline")
def get_sparkline(days: int = Query(default=30, ge=1, le=365)):
    """30D (or `days`) masthead sparkline: {date, value_base, income_cum_base} series.

    has_real_daily_history=false means no real daily price history exists — the caller
    MUST omit the chart (spec ban on synthetic series). Never 500 on empty data.
    """
    return dtv_service.sparkline_series(days=days)

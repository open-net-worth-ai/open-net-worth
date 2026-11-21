"""Window arithmetic for ``spending_breakdown`` — which dates a period, its predecessors and
its year-ago twin cover. A calendar month steps by calendar months; any other window steps by
its own length (a 7-day window steps by weeks)."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from backend.core.errors import ValidationError
from backend.services import recurring


def _clamp(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def parse(value: str, name: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValidationError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from None


def _is_calendar_month(start: date, end: date) -> bool:
    return (
        start.day == 1
        and (start.year, start.month) == (end.year, end.month)
        and end.day == monthrange(end.year, end.month)[1]
    )


def is_month_to_date(start: date, end: date) -> bool:
    """The 1st of a month through an earlier day of the SAME month — "this month so far"."""
    return (
        start.day == 1
        and (start.year, start.month) == (end.year, end.month)
        and not _is_calendar_month(start, end)
    )


def resolve_period(start: date, end: date, period: str | None) -> str:
    days = (end - start).days + 1
    is_month, is_week = _is_calendar_month(start, end), days == 7
    # Models ask for "this month" as the 1st..today with period='month' far more often than
    # not; refusing it cost a retry on every monthly report (2026-11-21). A month-to-date
    # window is compared with the SAME days of earlier months (see `shift`).
    if period == "month" and not (is_month or is_month_to_date(start, end)):
        raise ValidationError(
            "period='month' needs a window from the 1st to a day of the same month "
            "(a whole calendar month, or month-to-date)"
        )
    if period == "week" and not is_week:
        raise ValidationError("period='week' needs a window of exactly 7 days")
    if period:
        return period
    return "month" if is_month else "week" if is_week else "custom"


def shift(start: date, end: date, kind: str, periods_back: int) -> tuple[date, date]:
    """The window `periods_back` periods earlier — calendar months for a month, else N-day blocks."""
    if kind == "month":
        y, m = recurring._add_months(start.year, start.month, -periods_back)
        if is_month_to_date(start, end):
            return date(y, m, 1), _clamp(y, m, end.day)
        return date(y, m, 1), date(y, m, monthrange(y, m)[1])
    step = timedelta(days=((end - start).days + 1) * periods_back)
    return start - step, end - step


def year_ago(start: date, end: date, kind: str) -> tuple[date, date]:
    if kind == "month":
        return date(start.year - 1, start.month, 1), _clamp(end.year - 1, end.month, end.day)
    return (
        _clamp(start.year - 1, start.month, start.day),
        _clamp(end.year - 1, end.month, end.day),
    )


def basis(kind: str, n: int, through_day: int | None = None) -> str:
    if kind == "month" and through_day:
        months = f"{n} month{'' if n == 1 else 's'}"
        return f"mean of days 1-{through_day} of the {months} before this one"
    unit = {"month": "calendar month", "week": "week"}.get(kind, "same-length period")
    return f"mean of the {n} {unit}{'' if n == 1 else 's'} before this one"

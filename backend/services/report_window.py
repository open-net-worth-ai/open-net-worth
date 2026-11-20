"""The period a report covers, and one month-summary built from several.

A report widget can describe this month, last month, the last 3/6/12 months or this year.
Every figure stays month-granular (the budget's own unit): a period is a run of months, and
its summary is the sum of their `reports_rollup.month_summary` rows, so a three-month Money
flow is exactly the three single months added up.
"""

from __future__ import annotations

import re
from datetime import date

from backend.core.errors import ValidationError
from backend.core.user_time import user_today

PERIODS = ("this_month", "last_month", "3m", "6m", "12m", "ytd")
_TRAILING = {"3m": 3, "6m": 6, "12m": 12}
_EPSILON = 0.005
# A custom range, "YYYY-MM:YYYY-MM", both months included.
_RANGE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2]):(\d{4})-(0[1-9]|1[0-2])$")


def _ym(index: int) -> str:
    return f"{index // 12}-{index % 12 + 1:02d}"


def _custom_months(period: str) -> list[str] | None:
    match = _RANGE.match(period)
    if not match:
        return None
    start = int(match[1]) * 12 + int(match[2]) - 1
    end = int(match[3]) * 12 + int(match[4]) - 1
    if end < start:
        raise ValidationError(f"period {period!r} ends before it starts")
    return [_ym(i) for i in range(start, end + 1)]


def period_months(period: str, today: date | None = None) -> list[str]:
    """The months (`YYYY-MM`, oldest first) `period` covers. Trailing periods end with this
    month, so they include the month in progress — the same window the reports always used.
    A custom `YYYY-MM:YYYY-MM` covers its two months and every month between."""
    custom = _custom_months(period)
    if custom is not None:
        return custom
    if period not in PERIODS:
        raise ValidationError(
            f"period must be one of {', '.join(PERIODS)} or YYYY-MM:YYYY-MM, got {period!r}"
        )
    day = today or user_today()
    now = day.year * 12 + (day.month - 1)
    if period == "this_month":
        return [_ym(now)]
    if period == "last_month":
        return [_ym(now - 1)]
    if period == "ytd":
        return [_ym(i) for i in range(day.year * 12, now + 1)]
    count = _TRAILING[period]
    return [_ym(i) for i in range(now - count + 1, now + 1)]


def _sum_rows(points: list[dict], side: str) -> list[dict]:
    merged: dict[object, dict] = {}
    for point in points:
        for row in point[side]:
            entry = merged.get(row["key"])
            if entry is None:
                merged[row["key"]] = {**row, "amount": row["amount"]}
            else:
                entry["amount"] += row["amount"]
                # The newest month's label and group win: a renamed category reads as it is now.
                entry.update({k: v for k, v in row.items() if k not in ("amount", "key")})
    rows = [{**r, "amount": round(r["amount"], 2)} for r in merged.values()]
    rows = [r for r in rows if r["amount"] > _EPSILON]
    rows.sort(key=lambda r: r["amount"], reverse=True)
    return rows


def combine(points: list[dict]) -> dict:
    """One `month_summary`-shaped summary for a run of months: each category's rows added up."""
    expense_rows = _sum_rows(points, "expense_rows")
    income_rows = _sum_rows(points, "income_rows")
    yms = [p["ym"] for p in points]
    return {
        "ym": yms[-1] if yms else "",
        "months": yms,
        "expense_rows": expense_rows,
        "expense_total": round(sum(r["amount"] for r in expense_rows), 2),
        "income_rows": income_rows,
        "income_total": round(sum(r["amount"] for r in income_rows), 2),
    }

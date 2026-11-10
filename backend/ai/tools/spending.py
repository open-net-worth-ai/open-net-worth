"""Spending breakdown tool — a window's expenses split into recurring bills and day-to-day
spend, against the previous period and the user's usual period.

The report skills may not total rows themselves, and a lump "this week vs last week" is
dominated by whichever fixed monthly bill landed in it. The split, the usual-period mean and
every percentage are computed in ``services/spending_breakdown`` so the model only quotes them.
"""

from __future__ import annotations

import asyncio
import json
from typing import Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.services import spending_breakdown as breakdown_service


class GetSpendingBreakdownInput(ToolInput):
    date_from: str = Field(description="Window start, ISO date (YYYY-MM-DD), inclusive.")
    date_to: str = Field(description="Window end, ISO date (YYYY-MM-DD), inclusive.")
    period: Optional[Literal["week", "month"]] = Field(
        default=None,
        description=(
            "How the window is compared. Omit to infer: a window of exactly one calendar "
            "month is 'month', a 7-day window is 'week', anything else is a generic "
            "N-day window compared against N-day periods."
        ),
    )
    baseline_periods: Optional[int] = Field(
        default=None,
        description=(
            "How many periods before the window make up 'usual' (1-26). Default 8 for "
            "weeks and generic windows, 6 for months."
        ),
    )
    include_year_ago: Optional[bool] = Field(
        default=None,
        description=(
            "Also return the same window one year earlier (the same calendar month last "
            "year for a month). Default false."
        ),
    )


@tool(
    "get_spending_breakdown",
    description=(
        "Spending for a date window split into RECURRING BILLS (rent, subscriptions, loan "
        "repayments — rows of a detected recurring stream, or any row in a category that hosts "
        "one, so rent to a new landlord is a bill at once) and DAY-TO-DAY "
        "spending (everything else), each set against the previous period, the user's USUAL "
        "period (the mean of the periods before it) and, for a 7-day window, the SAME WEEK "
        "LAST MONTH (`month_ago`) — per category too. `income` splits what came in into money "
        "EARNED (income-kind categories) and money PAID BACK (an income row under an expense "
        "category: a reimbursement, a loan repaid to you). Spending is NET of money paid back, "
        "per category and in every comparison window — a refunded purchase is not spending; "
        "`spending.gross` / `spending.paid_back` show both sides. Bill and largest items carry "
        "`description` and `memo` — say what was bought. Use it for "
        "any weekly/monthly spending report or 'am I spending more than usual': a fixed bill "
        "landing in the window dominates every comparison. Figures are base-currency, "
        "computed server-side with the same rules as get_transactions' "
        "summary.total_expenses_base (= `spending.gross`; transfers and adjustments excluded) "
        "— quote them verbatim, never re-add. `change` carries the percentages (null when the "
        "base is 0), `bills_due_not_seen` lists bills expected in the window that never "
        "showed, and `notes` states any caveat (short history, bills older than "
        "detection reaches) — pass those on rather than overstate certainty."
    ),
    input_schema=schema_of(GetSpendingBreakdownInput, tool_name="get_spending_breakdown"),
)
async def get_spending_breakdown(input_data: dict):
    # Dispatch can't enforce ``required``, and a KeyError reached the model as just "'date_from'",
    # which told it nothing to correct (live call, 2026-11-07).
    missing = [key for key in ("date_from", "date_to") if not input_data.get(key)]
    if missing:
        needed = " and ".join(missing)
        return json.dumps({"error": f"{needed} required: ISO dates (YYYY-MM-DD)."}), None
    # OFF THE LOOP: the service scans the book and runs recurring detection, all synchronous.
    result = await asyncio.to_thread(
        breakdown_service.spending_breakdown,
        input_data["date_from"],
        input_data["date_to"],
        period=input_data.get("period") or None,
        baseline_periods=input_data.get("baseline_periods"),
        include_year_ago=bool(input_data.get("include_year_ago")),
    )
    return json.dumps(result, ensure_ascii=False), None

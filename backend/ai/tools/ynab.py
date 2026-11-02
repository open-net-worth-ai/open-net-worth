"""YNAB reads for Lucius: which plans the token sees, and one plan's accounts and budget.

The YNAB connector holds only the token. Which plan, which accounts and which month to read is
Lucius's call each time (``plan_id`` on these and on ``ynab_fetch_transactions`` /
``ynab_list_accounts``), never a setting chosen in advance. Pure reads, live from YNAB
(``services.aggregation.ynab_plan``); nothing is written here or in YNAB.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, explicit_null, schema_of


class _NoArgs(ToolInput):
    """No arguments."""


class YnabGetPlanInput(ToolInput):
    plan_id: Optional[str] = Field(
        default=None,
        json_schema_extra=explicit_null("string"),
        description="The plan's id from ynab_list_plans. Omit when the token sees one plan.",
    )
    month: Optional[str] = Field(
        default=None,
        json_schema_extra=explicit_null("string"),
        description="YYYY-MM. Omit for YNAB's current month.",
    )
    include_hidden: bool = Field(default=False, description="Also list hidden categories.")


@tool(
    "ynab_list_plans",
    description=(
        "List the YNAB plans (budgets) the saved token can see: id, name and currency. Use an "
        "id as plan_id in ynab_get_plan, ynab_list_accounts and ynab_fetch_transactions."
    ),
    input_schema=schema_of(_NoArgs, tool_name="ynab_list_plans"),
    connector="ynab",
)
async def ynab_list_plans(input_data: dict):
    from backend.services.aggregation import ynab_plan

    plans = await asyncio.to_thread(ynab_plan.list_plans)
    return json.dumps({"plans": plans}, ensure_ascii=False), None


@tool(
    "ynab_get_plan",
    description=(
        "Read one YNAB plan live: its accounts (type, on/off budget, closed, balance, cleared "
        "balance) and one month's budget — Ready to Assign, income, assigned and activity, "
        "then each category's assigned, activity and available, with its group. Amounts are in "
        "the plan's currency. Read-only."
    ),
    input_schema=schema_of(YnabGetPlanInput, tool_name="ynab_get_plan"),
    connector="ynab",
)
async def ynab_get_plan(input_data: dict):
    from backend.services.aggregation import ynab_plan

    result = await asyncio.to_thread(
        ynab_plan.detail,
        input_data.get("plan_id"),
        input_data.get("month"),
        bool(input_data.get("include_hidden") or False),
    )
    return json.dumps(result, ensure_ascii=False), None

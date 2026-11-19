"""Spending advice before a payment — "Ask Lucius before buying".

The tool head only validates and shapes; the advice is ``services/spending_advice``, which reads
the Budget, Commitments and card-coverage authorities rather than recomputing them.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import OpenNetWorthError
from backend.services import spending_advice as advice_service
from backend.services.feature_flags import enabled as flag_enabled

_NAME = "get_spending_advice_before_payment"
_OFF = (
    "Spending advice is switched off on this install (Settings → Developer → Experimental). "
    "Answer from simulate_cashflow, get_commitments and get_budget_month instead."
)


class GetSpendingAdviceInput(ToolInput):
    amount: float = Field(description="What the purchase costs, in `currency`. Positive.")
    currency: str = Field(description="ISO currency of `amount` (e.g. 'USD', 'SGD').")
    merchant: Optional[str] = Field(
        default=None,
        description="Where it is bought, as the user or the receipt names it ('ACME Store').",
    )
    category: Optional[str] = Field(
        default=None,
        description=(
            "Budget category id or label. Omit to use the merchant's usual category; an "
            "unknown one is an error that lists the closest real labels."
        ),
    )
    pay_with: Optional[str] = Field(
        default=None,
        description="Account name or id it would be paid from (a card or a cash account). "
        "Omit for cash.",
    )
    date: Optional[str] = Field(
        default=None, description="When it would be paid, YYYY-MM-DD. Defaults to today."
    )
    essential: Optional[bool] = Field(
        default=None,
        description="True for a need (medicine, rent, groceries): it never gets a 'wait'.",
    )


@tool(
    _NAME,
    description=(
        "Call BEFORE the user pays for anything — a purchase, a booking, a subscription — "
        "including 'can I afford X', 'should I buy this', 'is it OK to spend Y at Z'. Returns "
        "Lucius's advice in the user's own numbers: `verdict` is fits / fits_with_cover (the "
        "budget category is short but Ready to Assign or another envelope can cover it) / "
        "wait (cash would not cover the bills due before the next expected income — "
        "`options` carries that date) / over. `reasons` are ready-made sentences; `options` "
        "always include buy_anyway — say them, never decide for the user, never shame. "
        "`envelope`, `cover`, `bills_before_income` and `card` are the Budget, Commitments "
        "and card-coverage figures as their screens show them: quote, never re-add. A card "
        "`warning` means its payment is already short. Read-only: covering a shortfall is a "
        "separate move_money/assign_budget the user approves. For a big one-off or 'how long "
        "would my money last', the can-i-afford-it skill also reads the runway."
    ),
    input_schema=schema_of(GetSpendingAdviceInput, tool_name=_NAME),
)
async def get_spending_advice_before_payment(input_data: dict):
    if not flag_enabled("spending_advice"):
        return json.dumps({"error": _OFF}), None
    missing = [key for key in ("amount", "currency") if input_data.get(key) in (None, "")]
    if missing:
        return json.dumps({"error": f"{' and '.join(missing)} required."}), None
    try:
        on = date.fromisoformat(input_data["date"]) if input_data.get("date") else None
        advice = await asyncio.to_thread(
            advice_service.spending_advice,
            float(input_data["amount"]),
            str(input_data["currency"]),
            merchant=input_data.get("merchant"),
            category=input_data.get("category"),
            pay_with=input_data.get("pay_with"),
            on=on,
            essential=bool(input_data.get("essential")),
        )
    except ValueError:
        return json.dumps({"error": "date must be YYYY-MM-DD; amount a number."}), None
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}), None
    return json.dumps(advice, ensure_ascii=False), None

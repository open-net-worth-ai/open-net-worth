"""Rebalancing recommendation tool (P4-RB1).

  * ``get_rebalance_plan`` — read/compute (proposed_change = None). Compares the live
    investment book against the profile's ``target_allocation`` and returns the suggested
    buy/sell amounts (in base) to move toward targets, with an optional ``new_cash``
    contribution and a ``mode`` (``sell_to_target`` full rebalance | ``cash_only`` buys-only).

This tool ONLY SUGGESTS — it never places an order and never writes a position. If the user
wants to ACT on a suggestion, that flows through the existing position propose→confirm→apply
tools (``resolve_instrument`` + ``propose_position_*``), each keeping its own confirm tier.

All allocation/trade math lives in services/rebalance.py; this module only marshals I/O.
Respects the dependency rule (ai → services → db → core).
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.services import rebalance as rebalance_service


class GetRebalancePlanInput(ToolInput):
    """Schema-of-record for ``get_rebalance_plan`` (see ``schema_from_model``)."""

    new_cash: Optional[float] = Field(
        default=None, description="Fresh capital to deploy, in the base currency (optional, >= 0)."
    )
    mode: Optional[Literal["sell_to_target", "cash_only"]] = Field(
        default=None,
        description="sell_to_target = full rebalance; cash_only = buys only, never sell.",
    )


@tool(
    "get_rebalance_plan",
    description=(
        "Rebalancing suggestions: compares current vs target asset-class weights and returns "
        "suggested buy/sell amounts in base currency. SUGGESTION-ONLY — never places orders. "
        "To act, use resolve_instruments, then add_positions / update_positions / "
        "remove_positions "
        "mode: 'sell_to_target' (full rebalance, may sell) or 'cash_only' (buys only). "
        "Returns current/target weights, drift, and trade suggestions. "
        "No target_allocation set → status='no_targets'. untracked_note: invested money "
        "left out (holdings not entered)."
    ),
    input_schema=schema_of(GetRebalancePlanInput, tool_name="get_rebalance_plan"),
)
async def get_rebalance_plan(input_data: dict):
    new_cash = input_data.get("new_cash") or 0.0
    mode = input_data.get("mode") or "sell_to_target"
    plan = rebalance_service.rebalance_plan(new_cash=new_cash, mode=mode)
    return json.dumps(plan, ensure_ascii=False), None

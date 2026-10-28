"""Holding plans — why the user owns (or would buy) a ticker, and the prices that matter to them.

One plan per instrument (``services.holding_plans``), shown on the asset page's "Your Plan"
card. The watch rules read its thesis / buy zone / fair value; role, sell condition, stop and
trim are the held-side fields. Every value is the user's call: tools propose, the user confirms.
"""

from __future__ import annotations

import asyncio
import json
from typing import List, Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import NestedInput, ToolInput, schema_of
from backend.core.errors import OpenNetWorthError
from backend.db.holding_plan_store import PLAN_FIELDS
from backend.services import holding_plans as _plans

_ASK_FIRST = "These are the user's own views — propose values and ask before saving."


class GetHoldingPlansInput(ToolInput):
    """Schema-of-record for ``get_holding_plans`` (see ``schema_from_model``)."""

    tickers: Optional[List[str]] = Field(
        default=None, description="Only these tickers, e.g. ['AAPL']. Omit for every plan."
    )


class HoldingPlanUpdate(NestedInput):
    """One ``set_holding_plans.updates`` entry."""

    ticker: str = Field(description="The ticker whose plan to write, e.g. 'AAPL'.")
    role: Optional[Literal["core", "satellite", "speculative"]] = Field(
        default=None,
        description=(
            "core = long-term base of the portfolio; satellite = an active bet around it; "
            "speculative = a small, high-risk bet."
        ),
    )
    thesis: Optional[str] = Field(
        default=None, description="Why they own it, or would buy it. At most 280 characters."
    )
    exit_if: Optional[str] = Field(
        default=None,
        description="The business condition that would make them sell. At most 280 characters.",
    )
    stop_below: Optional[float] = Field(default=None, description="Sell-review price floor.")
    buy_below: Optional[float] = Field(default=None, description="Top of the buy zone.")
    buy_above: Optional[float] = Field(
        default=None, description="Optional floor of the buy zone (needs buy_below)."
    )
    fair_value: Optional[float] = Field(default=None, description="What they think it's worth.")
    trim_above: Optional[float] = Field(default=None, description="Price to trim or sell at.")
    clear: Optional[List[str]] = Field(
        default=None, description="Field names to blank, e.g. ['stop_below']."
    )


class SetHoldingPlansInput(ToolInput):
    """Schema-of-record for ``set_holding_plans`` (see ``schema_from_model``)."""

    updates: List[HoldingPlanUpdate] = Field(
        description=(
            "One entry per ticker. Fields you omit keep their current value; `clear` blanks "
            "fields. Prices are in the instrument's quote currency."
        )
    )


class DeleteHoldingPlansInput(ToolInput):
    """Schema-of-record for ``delete_holding_plans`` (see ``schema_from_model``)."""

    tickers: List[str] = Field(description="Tickers whose plans to delete.")


def _with_ticker(plan: dict | None, ticker: str) -> dict | None:
    return {**plan, "ticker": ticker} if plan else None


async def _instrument_id(ticker: str) -> str:
    inst = await asyncio.to_thread(_plans.instrument_for_ticker, ticker, resolve=True)
    if not inst:
        raise OpenNetWorthError(f"Couldn't find a market instrument for {ticker!r}.")
    return inst["id"]


@tool(
    "get_holding_plans",
    description=(
        "The user's holding plans: per ticker, why they own (or would buy) it, what would make "
        "them sell, its role (core / satellite / speculative), and their price levels — stop, "
        "buy zone, fair value, trim — with whether they hold it now."
    ),
    input_schema=schema_of(GetHoldingPlansInput, tool_name="get_holding_plans"),
)
async def get_holding_plans(input_data: dict):
    plans = await asyncio.to_thread(_plans.list_plans, input_data.get("tickers"))
    return json.dumps({"plans": plans, "count": len(plans)}, ensure_ascii=False), None


@tool(
    "set_holding_plans",
    description=(
        "Write the user's plan for one or more tickers: thesis, sell condition (exit_if), role "
        "and price levels. Creates the plan if there is none. " + _ASK_FIRST
    ),
    input_schema=schema_of(SetHoldingPlansInput, tool_name="set_holding_plans"),
    mutates=("portfolio", "watchlist"),
)
async def set_holding_plans(input_data: dict):
    results = []
    for entry in input_data.get("updates") or []:
        ticker = (entry.get("ticker") or "").strip()
        fields = {k: entry[k] for k in PLAN_FIELDS if entry.get(k) is not None}
        try:
            iid = await _instrument_id(ticker)
            written = await asyncio.to_thread(
                _plans.apply_plan_change,
                {"op": "set", "instrument_id": iid, "fields": fields, "clear": entry.get("clear")},
            )
        except OpenNetWorthError as exc:
            results.append({"ticker": ticker, "ok": False, "error": exc.message or str(exc)})
            continue
        results.append(
            {"ticker": ticker, "ok": True, "plan": _with_ticker(written["plan"], ticker)}
        )
    return json.dumps({"results": results}, ensure_ascii=False), None


@tool(
    "delete_holding_plans",
    description="Delete the user's plan for one or more tickers. " + _ASK_FIRST,
    input_schema=schema_of(DeleteHoldingPlansInput, tool_name="delete_holding_plans"),
    mutates=("portfolio", "watchlist"),
)
async def delete_holding_plans(input_data: dict):
    results = []
    for ticker in input_data.get("tickers") or []:
        inst = await asyncio.to_thread(_plans.instrument_for_ticker, ticker)
        if not inst:
            results.append({"ticker": ticker, "ok": False, "error": "no plan for that ticker"})
            continue
        await asyncio.to_thread(
            _plans.apply_plan_change, {"op": "delete", "instrument_id": inst["id"]}
        )
        results.append({"ticker": ticker, "ok": True})
    return json.dumps({"results": results}, ensure_ascii=False), None

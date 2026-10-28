"""Investment theses — why the user owns (or would buy) a ticker, and the prices that matter to them.

One thesis per instrument (``services.investment_theses``), shown on the asset page's "Your Thesis"
card. The watch rules read its reason / buy zone / fair value; role, breaks_if, stop and trim are
the held-side fields.
"""

from __future__ import annotations

import asyncio
import json
from typing import List, Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import NestedInput, ToolInput, schema_of
from backend.core.errors import OpenNetWorthError
from backend.db.investment_thesis_store import THESIS_FIELDS
from backend.services import investment_theses as _theses

_USERS_OWN = "Every value is the user's own view — record what they say, never your estimate."


class GetInvestmentThesesInput(ToolInput):
    """Schema-of-record for ``get_investment_theses`` (see ``schema_from_model``)."""

    tickers: Optional[List[str]] = Field(
        default=None, description="Only these tickers, e.g. ['AAPL']. Omit for every thesis."
    )


class InvestmentThesisUpdate(NestedInput):
    """One ``set_investment_theses.updates`` entry."""

    ticker: str = Field(description="The ticker whose thesis to write, e.g. 'AAPL'.")
    role: Optional[Literal["core", "satellite", "speculative"]] = Field(
        default=None,
        description=(
            "core = long-term base of the portfolio; satellite = an active bet around it; "
            "speculative = a small, high-risk bet."
        ),
    )
    reason: Optional[str] = Field(
        default=None,
        description=(
            "Why they own it, or would buy it, and how long they plan to hold if they said, "
            "e.g. 'holding to 2029'. At most 280 characters."
        ),
    )
    breaks_if: Optional[str] = Field(
        default=None,
        description=(
            "The business fact that would break the thesis and make them sell. "
            "At most 280 characters."
        ),
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


class SetInvestmentThesesInput(ToolInput):
    """Schema-of-record for ``set_investment_theses`` (see ``schema_from_model``)."""

    updates: List[InvestmentThesisUpdate] = Field(
        description=(
            "One entry per ticker. Fields you omit keep their current value; `clear` blanks "
            "fields. Prices are in the instrument's quote currency."
        )
    )


class DeleteInvestmentThesesInput(ToolInput):
    """Schema-of-record for ``delete_investment_theses`` (see ``schema_from_model``)."""

    tickers: List[str] = Field(description="Tickers whose theses to delete.")


def _with_ticker(thesis: dict | None, ticker: str) -> dict | None:
    return {**thesis, "ticker": ticker} if thesis else None


async def _instrument_id(ticker: str) -> str:
    inst = await asyncio.to_thread(_theses.instrument_for_ticker, ticker, resolve=True)
    if not inst:
        raise OpenNetWorthError(f"Couldn't find a market instrument for {ticker!r}.")
    return inst["id"]


@tool(
    "get_investment_theses",
    description=(
        "The user's investment theses: per ticker, why they own (or would buy) it, what would "
        "break the thesis, its role (core / satellite / speculative), and their price levels — "
        "stop, buy zone, fair value, trim — with whether they hold it now."
    ),
    input_schema=schema_of(GetInvestmentThesesInput, tool_name="get_investment_theses"),
)
async def get_investment_theses(input_data: dict):
    theses = await asyncio.to_thread(_theses.list_theses, input_data.get("tickers"))
    return json.dumps({"theses": theses, "count": len(theses)}, ensure_ascii=False), None


@tool(
    "set_investment_theses",
    description=(
        "Write the user's investment thesis for one or more tickers: reason, what would break it "
        "(breaks_if), role and price levels. Creates the thesis if there is none. " + _USERS_OWN
    ),
    input_schema=schema_of(SetInvestmentThesesInput, tool_name="set_investment_theses"),
    mutates=("portfolio", "watchlist"),
)
async def set_investment_theses(input_data: dict):
    results = []
    for entry in input_data.get("updates") or []:
        ticker = (entry.get("ticker") or "").strip()
        fields = {k: entry[k] for k in THESIS_FIELDS if entry.get(k) is not None}
        try:
            iid = await _instrument_id(ticker)
            written = await asyncio.to_thread(
                _theses.apply_thesis_change,
                {"op": "set", "instrument_id": iid, "fields": fields, "clear": entry.get("clear")},
            )
        except OpenNetWorthError as exc:
            results.append({"ticker": ticker, "ok": False, "error": exc.message or str(exc)})
            continue
        results.append(
            {"ticker": ticker, "ok": True, "thesis": _with_ticker(written["thesis"], ticker)}
        )
    return json.dumps({"results": results}, ensure_ascii=False), None


@tool(
    "delete_investment_theses",
    description="Delete the user's investment thesis for one or more tickers.",
    input_schema=schema_of(DeleteInvestmentThesesInput, tool_name="delete_investment_theses"),
    mutates=("portfolio", "watchlist"),
)
async def delete_investment_theses(input_data: dict):
    results = []
    for ticker in input_data.get("tickers") or []:
        inst = await asyncio.to_thread(_theses.instrument_for_ticker, ticker)
        if not inst:
            results.append({"ticker": ticker, "ok": False, "error": "no thesis for that ticker"})
            continue
        await asyncio.to_thread(
            _theses.apply_thesis_change, {"op": "delete", "instrument_id": inst["id"]}
        )
        results.append({"ticker": ticker, "ok": True})
    return json.dumps({"results": results}, ensure_ascii=False), None

"""ETF look-through (X-ray) tools (P2-INV5).

Lucius can read the portfolio's look-through exposure and SEED a fund's constituents map:

  * ``get_portfolio_xray`` — read (proposed_change = None). Explodes each holding across its
    declared constituents and aggregates portfolio-wide by sector + region with an honest
    "unmapped %". Distinct from RiskPanel's whole-position sector view.
  * ``set_instrument_constituents`` — a DIRECT config write (NOT approve-tier): it sets a
    fund's exposure breakdown, which moves no money and is fully reversible via a turn
    checkpoint, so it is written directly (mirrors create_rule / update_monitoring_config),
    not propose→confirm. Validates weights via services.xray.validate_constituents.

All exposure/value math lives in services/xray.py (the math home) — this module only marshals
I/O. Respects the dependency rule (ai → services → db → core).
"""

from __future__ import annotations

import json
from typing import List, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import NestedInput, ToolInput, schema_of
from backend.core.errors import OpenNetWorthError
from backend.services import xray as xray_service


class GetPortfolioXrayInput(ToolInput):
    """Schema-of-record for ``get_portfolio_xray`` (see ``schema_from_model``)."""


@tool(
    "get_portfolio_xray",
    description=(
        "Look-THROUGH portfolio exposure (the 'X-ray'): each holding is exploded across its "
        "constituents map — an ETF spreads across its declared sector/region weights; a plain "
        "stock/coin counts 100% to its own sector — then aggregated portfolio-wide by sector "
        "and by region, in the base currency, with an honest 'unmapped %' (the share of the "
        "book we can't see through). This is DIFFERENT from the by-holding sector view (which "
        "groups whole positions by their single sector). Use it to answer 'how concentrated am "
        "I really, once you look inside the funds?'. To improve coverage, seed a fund's "
        "breakdown with set_instrument_constituents."
    ),
    input_schema=schema_of(GetPortfolioXrayInput, tool_name="get_portfolio_xray"),
)
async def get_portfolio_xray(input_data: dict):
    return json.dumps(xray_service.portfolio_xray(), ensure_ascii=False), None


class Constituent(NestedInput):
    weight: float = Field(description="Fraction 0–1")
    sector: Optional[str] = Field(default=None)
    region: Optional[str] = Field(default=None)
    label: Optional[str] = Field(default=None)


class SetInstrumentConstituentsInput(ToolInput):
    instrument_id: str = Field(description="instrument_id from get_portfolio / resolve_instruments")
    constituents: List[Constituent] = Field(description="Look-through slices; [] clears the map")


@tool(
    "set_instrument_constituents",
    description=(
        "Set (or clear) a fund's look-through breakdown so the X-ray can see inside it. This is "
        "a DIRECT config write, NOT a money action — it moves no funds and is reversible via a "
        "turn checkpoint, so it is saved immediately, like creating a "
        "categorization rule. Identify the instrument by instrument_id (from get_portfolio or "
        "resolve_instruments). Pass constituents as a list of {weight (fraction 0–1), sector, "
        "region, label?}; weights should sum to ≤ 1 (any shortfall is reported as unmapped). "
        "Pass an empty list to clear the map. Example: VOO ≈ [{weight:0.30,sector:'Technology',"
        "region:'US'}, {weight:0.13,sector:'Financials',region:'US'}, …]."
    ),
    input_schema=schema_of(SetInstrumentConstituentsInput, tool_name="set_instrument_constituents"),
    mutates=("portfolio",),
)
async def set_instrument_constituents(input_data: dict):
    if not xray_service.get_instrument(input_data["instrument_id"]):
        return json.dumps(
            {"error": "unknown instrument_id — call get_portfolio / resolve_instruments first"}
        ), None
    try:
        clean = xray_service.validate_constituents(input_data.get("constituents") or [])
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}), None
    inst = xray_service.set_instrument_constituents(input_data["instrument_id"], clean)
    return json.dumps(
        {
            "ok": True,
            "instrument_id": input_data["instrument_id"],
            "symbol": (inst or {}).get("symbol", ""),
            "constituents": (inst or {}).get("constituents", []),
        },
        ensure_ascii=False,
    ), None

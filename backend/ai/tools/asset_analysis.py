"""``get_asset_technicals`` / ``get_asset_fundamentals`` — the ticker Analysis tab's own rows.

Both project the snapshots ``GET /api/intel/assets/{id}/technicals|fundamentals`` return, so
Lucius quotes the figures on the screen rather than a second calculation of them. Only the
``compute_*`` half is called: the tab's ``ensure_*_narrative`` can spend a model call, and the
model writing this answer needs no second model's paragraph.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from pydantic import Field

from backend.ai.tools.asset_context import _clean, _resolve_asset_ref
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.services.asset_fundamentals import compute_fundamentals
from backend.services.asset_technicals import compute_technicals


class _AssetRefInput(ToolInput):
    ticker: Optional[str] = Field(default=None, description="Ticker, or pass instrument_id.")
    instrument_id: Optional[str] = Field(default=None, description="Instrument id, or pass ticker.")


class GetAssetTechnicalsInput(_AssetRefInput):
    """Schema-of-record for ``get_asset_technicals`` (see ``schema_from_model``)."""


class GetAssetFundamentalsInput(_AssetRefInput):
    """Schema-of-record for ``get_asset_fundamentals`` (see ``schema_from_model``)."""


def _resolve(input_data: dict) -> dict | str:
    ticker = (input_data.get("ticker") or "").strip() or None
    instrument_id = (input_data.get("instrument_id") or "").strip() or None
    if not ticker and not instrument_id:
        return "pass ticker or instrument_id"
    return _resolve_asset_ref(ticker, instrument_id)


def _technical_row(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "label": row.get("label"),
        "value": row.get("raw_display"),
        "score": row.get("score"),
    }


def _fundamental_row(row: dict) -> dict:
    sector = None
    if row.get("sector_mean") is not None:
        sector = {
            "low": row.get("sector_low_display"),
            "mean": row.get("sector_mean_display"),
            "high": row.get("sector_high_display"),
            "peer_count": row.get("peer_count"),
        }
    return {
        "id": row.get("id"),
        "label": row.get("label"),
        "value": row.get("ticker_display"),
        "higher_is_better": row.get("higher_is_better"),
        "sector": sector,
    }


@tool(
    "get_asset_technicals",
    description=(
        "Technical indicators for ONE instrument: the Analysis tab's own rows, so quote these "
        "rather than computing or searching for them. Price vs SMA 50 and SMA 200, SMA 50/200, "
        "MACD, RSI (14), Bollinger %B from a year of daily closes, each scored -1 (sell end) to "
        "+1 (buy end), plus overall `lean` and `as_of` (last bar). A lean is one mechanical "
        "input, not advice: for buy/sell/hold also call get_asset_context. An unknown ticker is "
        "refused; call resolve_instruments first."
    ),
    input_schema=schema_of(GetAssetTechnicalsInput, tool_name="get_asset_technicals"),
)
async def get_asset_technicals(input_data: dict):
    resolved = _resolve(input_data)
    if isinstance(resolved, str):
        return json.dumps({"error": resolved}), None

    # Off the event loop — the series is a blocking market-data fetch on a cache miss.
    snap = await asyncio.to_thread(compute_technicals, resolved["id"])
    rows = snap.get("rows") or []
    out = {
        "instrument_id": resolved["id"],
        "symbol": resolved.get("symbol"),
        "available": bool(rows),
        "as_of": None,
        "lean": None,
        "rows": [_technical_row(r) for r in rows],
    }
    if rows:
        out["lean"] = snap.get("lean")
        out["as_of"] = (snap.get("points") or [{}])[-1].get("date")
    else:
        out["reason"] = "not enough price history to compute indicators"
    return json.dumps(out), None


@tool(
    "get_asset_fundamentals",
    description=(
        "Fundamentals for ONE stock: the Analysis tab's own rows, so quote these rather than "
        "computing or searching for them. `valuation` (P/E, forward P/E, PEG, P/B, EV/EBITDA, "
        "yield) and `growth` (EPS, EPS growth, revenue growth, margin, ROE, debt/equity), each "
        "with `higher_is_better` and `sector` low/mean/high across peers, or null. For "
        "buy/sell/hold also call get_asset_context. available=false for crypto, funds and no "
        "coverage. An unknown ticker is refused; call resolve_instruments first."
    ),
    input_schema=schema_of(GetAssetFundamentalsInput, tool_name="get_asset_fundamentals"),
)
async def get_asset_fundamentals(input_data: dict):
    resolved = _resolve(input_data)
    if isinstance(resolved, str):
        return json.dumps({"error": resolved}), None

    # Off the event loop — quote stats and the sector aggregate are blocking Yahoo calls.
    snap = await asyncio.to_thread(compute_fundamentals, resolved["id"])
    valuation = snap.get("valuation") or []
    growth = snap.get("growth") or []
    sector = snap.get("sector")
    out = {
        "instrument_id": resolved["id"],
        "symbol": resolved.get("symbol"),
        "available": bool(valuation or growth),
        "currency": snap.get("currency"),
        # Yahoo's text, or the instrument's — not ours, so it is sanitized like any feed string.
        "sector": _clean(sector) if sector else None,
        "valuation": [_fundamental_row(r) for r in valuation],
        "growth": [_fundamental_row(r) for r in growth],
    }
    if not out["available"]:
        out["reason"] = "no fundamentals for this instrument (crypto, fund, or no coverage)"
    return json.dumps(out), None

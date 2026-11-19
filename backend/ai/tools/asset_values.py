"""Owned-asset value tools — record, correct and remove one dated valuation (ADR
2026-09-26-owned-asset-purchase-point-is-derived, decision 4: recording appends, correcting
replaces, removing deletes; the purchase point is derived, so none of them touches it).

Every handler calls ``services.owned_assets`` ONLY, like the rest of the Assets tools
(``assets.py``).
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import Field

from backend.ai.tools._asset_projection import ASSET_ID_DESC, project_asset
from backend.ai.tools._shared import no_op_write_result
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import OpenNetWorthError
from backend.services import owned_assets as _oa

# ── record_asset_value (formerly add_asset_mark — renamed 2026-09-07, see
# backend/scripts/tool_renames.py) ──────────────────────────────────────────────────────


class RecordAssetValueInput(ToolInput):
    """Schema-of-record for ``record_asset_value`` (see ``schema_from_model``)."""

    asset_id: str = Field(description=ASSET_ID_DESC)
    value: float = Field(description="The new value observation, base currency, greater than 0.")
    note: Optional[str] = Field(
        default=None, description="Where this value came from, e.g. 'Zillow estimate', 'Chrono24'."
    )
    date: Optional[str] = Field(
        default=None,
        description=(
            "ISO date of the observation — not in the future, not before the purchase date. "
            "Defaults to today."
        ),
    )


@tool(
    "record_asset_value",
    description=(
        "Record a new value observation for an EXISTING owned asset — a re-appraisal, a "
        "market estimate, a statement figure. ADDITIVE: it adds a dated valuation and never "
        "overwrites history; the newest valuation (by date) is the asset's current value. A "
        "figure entered wrong is fixed with correct_asset_value instead. Not for a brand-new "
        "asset — create_asset's own `value` is already its first valuation."
    ),
    input_schema=schema_of(RecordAssetValueInput, tool_name="record_asset_value"),
    mutates=("assets",),
)
async def record_asset_value(input_data: dict):
    try:
        asset = _oa.apply_owned_asset_change(
            {
                "op": "add_mark",
                "id": input_data["asset_id"],
                "value": input_data["value"],
                "note": input_data.get("note"),
                "date": input_data.get("date"),
            }
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps({"ok": True, "asset": project_asset(asset)}, ensure_ascii=False), None


# ── correct_asset_value / remove_asset_value ───────────────────────────────────────────

_MARK_ID_DESC = "The value's id — marks[].id from get_asset (kind 'valuation')."


class CorrectAssetValueInput(ToolInput):
    """Schema-of-record for ``correct_asset_value`` (see ``schema_from_model``)."""

    asset_id: str = Field(description=ASSET_ID_DESC)
    mark_id: str = Field(description=_MARK_ID_DESC)
    value: Optional[float] = Field(default=None, description="Corrected value, base currency, > 0.")
    date: Optional[str] = Field(
        default=None,
        description="Corrected ISO YYYY-MM-DD — not in the future, not before the purchase date.",
    )
    note: Optional[str] = Field(default=None, description="Corrected note.")


@tool(
    "correct_asset_value",
    description=(
        "Fix a value observation that was entered WRONG — amount typo, wrong date, or note — "
        "in place. This rewrites the value history (the old figure is gone; the entry is "
        "marked edited). A new appraisal or market move is record_asset_value, never this. "
        "The purchase point (mark_id 'purchase') can't be corrected here — change "
        "cost/cost_date with update_asset."
    ),
    input_schema=schema_of(CorrectAssetValueInput, tool_name="correct_asset_value"),
    mutates=("assets",),
)
async def correct_asset_value(input_data: dict):
    fields = {k: input_data[k] for k in ("value", "date", "note") if input_data.get(k) is not None}
    try:
        before = _oa.get_asset(input_data["asset_id"])
        # Through the choke even with nothing to change: it refuses the purchase point and a
        # value that doesn't exist, and answers an identical correction with the asset as is.
        asset = _oa.apply_owned_asset_change(
            {
                "op": "correct_mark",
                "id": input_data["asset_id"],
                "mark_id": input_data["mark_id"],
                **fields,
            }
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    if asset == before:
        return json.dumps(
            no_op_write_result({"asset": project_asset(asset)}), ensure_ascii=False
        ), None
    return json.dumps({"ok": True, "asset": project_asset(asset)}, ensure_ascii=False), None


class RemoveAssetValueInput(ToolInput):
    """Schema-of-record for ``remove_asset_value`` (see ``schema_from_model``)."""

    asset_id: str = Field(description=ASSET_ID_DESC)
    mark_id: str = Field(description=_MARK_ID_DESC)


@tool(
    "remove_asset_value",
    description=(
        "Delete one value observation that should never have been recorded (mark_id from "
        "get_asset's marks). Refused for the purchase point (clear cost/cost_date with "
        "update_asset) and for the last remaining value (record a new value first, or "
        "delete_asset). Changes net-worth history."
    ),
    input_schema=schema_of(RemoveAssetValueInput, tool_name="remove_asset_value"),
    mutates=("assets",),
)
async def remove_asset_value(input_data: dict):
    try:
        asset = _oa.apply_owned_asset_change(
            {"op": "remove_mark", "id": input_data["asset_id"], "mark_id": input_data["mark_id"]}
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps({"ok": True, "asset": project_asset(asset)}, ensure_ascii=False), None

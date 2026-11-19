"""Owned-assets tools — the Assets tab domain (ADR 2026-09-06-assets-tab-faithful-port,
decision 11): things you own valued by a dated manual mark (property, vehicles, watches &
jewellery, art, precious metals, cash-value policies, user-created categories).

Every handler calls ``services.owned_assets`` ONLY — never the store — mirroring the
router (``api/assets_api.py``). Money math (the newest-mark valuation, share-adjustment,
disposal's ledger write) lives entirely in that service; this module validates shape and
projects the result. The value tools (record, correct, remove) live in ``asset_values.py``.
"""

from __future__ import annotations

import json
from typing import List, Optional

from pydantic import Field

from backend.ai.tools import _bounded as _b
from backend.ai.tools._asset_projection import ASSET_ID_DESC, project_asset
from backend.ai.tools._shared import no_op_write_result
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import OpenNetWorthError
from backend.services import owned_assets as _oa

# The store's own identity-edit whitelist (owned_assets_store.OwnedAssetsStore._EDITABLE)
# duplicated here (not imported) so this module's dependency stays ai -> services only.
# This allow-list is the REAL gate — the
# registry's own dispatch validation does not enforce `additionalProperties` (by design,
# for the propose→apply replay paths; see registry.py's S2 note), so removing "automate"
# from UpdateAssetInput's schema alone would not stop an "automate" key that reaches this
# handler by some other route. See CreateAssetInput/UpdateAssetInput below.
_UPDATE_FIELDS = (
    "name",
    "category",
    "description",
    "notes",
    "cost",
    "cost_date",
    "share_pct",
    "share_note",
    "loan_account_id",
    "insured",
    "maturity_date",
    "facts",
)


# An explicit null CLEARS these; on every other field null means "not given".
_NULL_CLEARS = frozenset({"cost", "cost_date", "maturity_date", "loan_account_id"})


def _doc_summary(docs: list[dict] | None) -> list[dict]:
    """Doc rows for a LIST context: name + lucius_readable only — never the ref_id (a
    content pointer) or added_on. Full doc detail (incl. ref_id) is reserved for
    get_asset, where the caller is already focused on one asset."""
    return [
        {"name": d.get("name") or "", "lucius_readable": bool(d.get("lucius_readable"))}
        for d in docs or []
    ]


def _list_row(asset: dict) -> dict:
    return {
        "id": asset["id"],
        "name": asset.get("name") or "",
        "category": asset.get("category"),
        "value": asset.get("value"),
        "currency": asset.get("currency"),
        "valued_on": asset.get("valued_on"),
        "insured": asset.get("insured"),
        "maturity_date": asset.get("maturity_date"),
        "share_pct": asset.get("share_pct"),
        "share_value": asset.get("share_value"),
        "cost": asset.get("cost"),
        "loan_account_id": asset.get("loan_account_id"),
        "archived": asset.get("archived"),
        "disposed_on": asset.get("disposed_on"),
        "docs": _doc_summary(asset.get("docs")),
    }


# ── list_assets / get_asset (read) ──────────────────────────────────────────────────────


class ListAssetsInput(ToolInput):
    """Schema-of-record for ``list_assets`` (see ``schema_from_model``)."""

    include_archived: Optional[bool] = Field(
        default=None,
        description="Set true to include sold/disposed assets. Default false.",
    )
    category: Optional[str] = Field(
        default=None,
        description="Filter to one category key, e.g. 'property', 'vehicle', 'metal'.",
    )


@tool(
    "list_assets",
    description=(
        "List the user's owned assets — things they own valued by a dated manual valuation, "
        "not a ledger balance: property, vehicles, watches & jewellery, art, precious metals, "
        "cash-value insurance/endowment policies, and any user-created category. Each row's "
        "`value` is the WHOLE asset's newest valuation and `share_value` the user's share of "
        "it (value × share_pct), both in the asset's own currency. Archived (sold) assets are "
        "hidden by default. Documents are summarized to name + whether Lucius may read them — "
        "call get_asset for the full doc list with fetchable refs."
    ),
    input_schema=schema_of(ListAssetsInput, tool_name="list_assets"),
)
async def list_assets(input_data: dict):
    include_archived = bool(input_data.get("include_archived", False))
    category = input_data.get("category") or None
    rows = _oa.list_assets(include_archived=include_archived, category=category)
    page, total = _b.cap(rows, _b.DEFAULT_ASSET_LIMIT)
    out = {"assets": [_list_row(a) for a in page], "showing": len(page), "total": total}
    if len(page) < total:
        out["note"] = (
            f"Showing {len(page)} of {total} assets — filter with category= or narrow "
            "further; this is not the full list."
        )
    return json.dumps(out, ensure_ascii=False), None


class GetAssetInput(ToolInput):
    """Schema-of-record for ``get_asset`` (see ``schema_from_model``)."""

    asset_id: str = Field(description=ASSET_ID_DESC)


@tool(
    "get_asset",
    description=(
        "Get one owned asset's full record — its value history (`marks`, newest first: each "
        "`kind` 'valuation', plus the 'purchase' point derived from cost/cost_date), facts, "
        "notes, cost basis, share, linked loan, and its documents (with fetchable refs, "
        "unlike list_assets' summary). A valuation's id is the mark_id "
        "correct_asset_value/remove_asset_value take. Call this before recording, correcting "
        "or removing a value."
    ),
    input_schema=schema_of(GetAssetInput, tool_name="get_asset"),
)
async def get_asset(input_data: dict):
    try:
        asset = _oa.get_asset(input_data["asset_id"])
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps({"ok": True, "asset": project_asset(asset)}, ensure_ascii=False), None


# ── create_asset ─────────────────────────────────────────────────────────────────────────


class CreateAssetInput(ToolInput):
    """Schema-of-record for ``create_asset`` (see ``schema_from_model``)."""

    name: str = Field(
        description="A short, specific name, e.g. '88 Marina Rise' or 'Rolex Submariner'."
    )
    category: str = Field(
        description=(
            "A builtin category key (property, vehicle, watch, art, metal, policy, other) or "
            "a custom category's key from create_asset_category."
        )
    )
    value: float = Field(
        description="Its first valuation, dated today — BOOK'S BASE CURRENCY, greater than 0."
    )
    description: str = Field(default="", description="Free-text description.")
    facts: Optional[List[List[str]]] = Field(
        default=None, description="Free-form [label, value] pairs, e.g. [['Bedrooms', '3']]."
    )
    notes: str = Field(default="", description="Free-text notes.")
    cost: Optional[float] = Field(
        default=None,
        description=(
            "Purchase price, base currency, > 0. With cost_date it becomes the purchase point; "
            "for a policy it is premiums paid so far, never a purchase point."
        ),
    )
    cost_date: Optional[str] = Field(
        default=None,
        description="ISO date it was bought, not in the future. For a policy, the start date.",
    )
    share_pct: float = Field(
        default=100,
        description="The user's share, 0-100 — anything else is refused. Default 100.",
    )
    share_note: str = Field(default="", description="e.g. 'Split with spouse'.")
    loan_account_id: Optional[str] = Field(
        default=None,
        description="The loan account it is financed against. A loan secures one live asset.",
    )
    insured: bool = Field(default=False, description="Whether the asset is insured.")
    maturity_date: Optional[str] = Field(
        default=None, description="Policy maturity date as ISO YYYY-MM-DD."
    )
    note: Optional[str] = Field(
        default=None, description="Where the value came from, e.g. 'Zillow estimate', 'appraisal'."
    )


# The service `create` op's own field set, minus `automate` (security review C1) — an
# explicit allow-list, not `**input_data`, so an "automate" key that reaches this handler
# by any means OTHER than the declared schema (the registry's own dispatch validation
# deliberately does not enforce `additionalProperties`, by design — see
# `registry.py`'s S2 note — so schema removal alone does not stop an extra key from
# arriving in `input_data`) still can never reach `apply_owned_asset_change` and mint an
# automation. Kept in sync with `CreateAssetInput`'s fields above.
_CREATE_FIELDS = (
    "name",
    "category",
    "value",
    "description",
    "facts",
    "notes",
    "cost",
    "cost_date",
    "share_pct",
    "share_note",
    "loan_account_id",
    "insured",
    "maturity_date",
    "note",
)


@tool(
    "create_asset",
    description=(
        "Add a new owned asset — property, vehicle, watch/jewellery, art, precious metal, "
        "cash-value policy, or a user-created category. Only name, category and value are "
        "required; `value` (BOOK'S BASE CURRENCY) becomes its first valuation — do NOT follow "
        "this with record_asset_value, which is only for a LATER re-appraisal. It immediately counts toward net worth. Unknown category raises; use "
        "create_asset_category first for a genuinely new kind of thing."
    ),
    input_schema=schema_of(CreateAssetInput, tool_name="create_asset"),
    mutates=("assets",),
)
async def create_asset(input_data: dict):
    retired = {"cadence_months", "automation_id", "months_old", "automation", "automate"}
    if invalid := retired.intersection(input_data):
        return json.dumps({"ok": False, "error": f"retired asset fields: {sorted(invalid)}"}), None
    payload = {k: v for k, v in input_data.items() if k in _CREATE_FIELDS}
    try:
        asset = _oa.apply_owned_asset_change({"op": "create", **payload})
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps({"ok": True, "asset": project_asset(asset)}, ensure_ascii=False), None


# ── update_asset (identity only — never value) ──────────────────────────────────────────


class UpdateAssetInput(ToolInput):
    """Schema-of-record for ``update_asset`` (see ``schema_from_model``)."""

    asset_id: str = Field(description=ASSET_ID_DESC)
    name: Optional[str] = Field(default=None, description="Rename the asset.")
    category: Optional[str] = Field(default=None, description="Recategorize it.")
    description: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    cost: Optional[float] = Field(
        default=None,
        description="Purchase price, base currency, > 0 — moves the purchase point; null clears it.",
    )
    cost_date: Optional[str] = Field(
        default=None,
        description="ISO date, not after the earliest value or the sale; null clears it.",
    )
    share_pct: Optional[float] = Field(default=None, description="0-100; anything else is refused.")
    share_note: Optional[str] = Field(default=None)
    loan_account_id: Optional[str] = Field(
        default=None, description="Refused if the loan already secures another asset."
    )
    insured: Optional[bool] = Field(default=None, description="Whether the asset is insured.")
    maturity_date: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD.")
    facts: Optional[List[List[str]]] = Field(default=None)


@tool(
    "update_asset",
    description=(
        "Patch an owned asset's IDENTITY fields — name, category, description, notes, cost, "
        "share, insurance, maturity date, or linked loan. It NEVER changes the current value: "
        "a new value is record_asset_value, fixing a wrong one is correct_asset_value. Pass "
        "only the fields to change."
    ),
    input_schema=schema_of(UpdateAssetInput, tool_name="update_asset"),
    mutates=("assets",),
)
async def update_asset(input_data: dict):
    asset_id = input_data["asset_id"]
    retired = {"cadence_months", "automation_id", "months_old", "automation", "automate"}
    if invalid := retired.intersection(input_data):
        return json.dumps({"ok": False, "error": f"retired asset fields: {sorted(invalid)}"}), None
    changes = {
        k: v
        for k, v in input_data.items()
        if k in _UPDATE_FIELDS and (v is not None or k in _NULL_CLEARS)
    }
    if not changes:
        try:
            existing = _oa.get_asset(asset_id)
        except OpenNetWorthError as exc:
            return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
        return (
            json.dumps(no_op_write_result({"asset": project_asset(existing)}), ensure_ascii=False),
            None,
        )
    try:
        asset = _oa.apply_owned_asset_change({"op": "update", "id": asset_id, "changes": changes})
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps({"ok": True, "asset": project_asset(asset)}, ensure_ascii=False), None


# ── dispose_asset ────────────────────────────────────────────────────────────────────────


class DisposeAssetInput(ToolInput):
    """Schema-of-record for ``dispose_asset`` (see ``schema_from_model``)."""

    asset_id: str = Field(description=ASSET_ID_DESC)
    proceeds: float = Field(
        description="What the user received, in the destination account's currency — for a "
        "shared asset (share_pct below 100) their share of the sale price, never the whole "
        "price. 0 for a write-off."
    )
    account_id: str = Field(
        description="The cash account the proceeds land in (from list_accounts) — must be a "
        "non-archived bank/e-wallet-type account."
    )
    date: Optional[str] = Field(
        default=None, description="ISO date of the sale. Defaults to today."
    )


@tool(
    "dispose_asset",
    description=(
        "Sell or otherwise dispose of an owned asset. THIS POSTS A REAL LEDGER TRANSACTION "
        "for the proceeds into the given cash account (skipped when proceeds=0), then "
        "archives the asset — it stops counting toward net worth but its history is kept. "
        "This is not reversible the way an ordinary transaction edit is. A sale whose proceeds "
        "row was deleted can be disposed again."
    ),
    input_schema=schema_of(DisposeAssetInput, tool_name="dispose_asset"),
    mutates=("assets", "ledger"),
)
async def dispose_asset(input_data: dict):
    try:
        asset = _oa.apply_owned_asset_change(
            {
                "op": "dispose",
                "id": input_data["asset_id"],
                "proceeds": input_data["proceeds"],
                "account_id": input_data["account_id"],
                "date": input_data.get("date"),
            }
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps({"ok": True, "asset": project_asset(asset)}, ensure_ascii=False), None


# ── delete_asset ─────────────────────────────────────────────────────────────────────────


class DeleteAssetInput(ToolInput):
    """Schema-of-record for ``delete_asset`` (see ``schema_from_model``)."""

    asset_id: str = Field(description=ASSET_ID_DESC)


@tool(
    "delete_asset",
    description=(
        "Permanently remove an owned asset that should never have been added — a hard "
        "delete with no undo, unlike dispose_asset (which keeps history and posts real "
        "sale proceeds)."
    ),
    input_schema=schema_of(DeleteAssetInput, tool_name="delete_asset"),
    mutates=("assets",),
)
async def delete_asset(input_data: dict):
    try:
        result = _oa.apply_owned_asset_change({"op": "delete", "id": input_data["asset_id"]})
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


# ── create_asset_category ────────────────────────────────────────────────────────────────


class CreateAssetCategoryInput(ToolInput):
    """Schema-of-record for ``create_asset_category`` (see ``schema_from_model``)."""

    label: str = Field(description="The new category's display label, e.g. 'Vinyl & hi-fi'.")


@tool(
    "create_asset_category",
    description=(
        "Create a user-defined asset category for a kind of owned thing that doesn't fit "
        "the builtins (property, vehicle, watch, art, metal, policy, other). Idempotent — "
        "re-adding an existing label (case-insensitive) returns that category unchanged."
    ),
    input_schema=schema_of(CreateAssetCategoryInput, tool_name="create_asset_category"),
    mutates=("assets",),
)
async def create_asset_category(input_data: dict):
    try:
        category = _oa.create_category(input_data["label"])
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps({"ok": True, "category": category}, ensure_ascii=False), None

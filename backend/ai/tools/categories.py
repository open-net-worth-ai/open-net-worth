"""Category tools — the list, create/rename/delete, colour and visibility of categories."""

from __future__ import annotations

import json
import logging
from typing import List, Literal, Optional, Union

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import (
    ToolInput,
    require_nonempty,
    schema_of,
    schema_of_batch_only,
)
from backend.core.errors import OpenNetWorthError
from backend.services import budget_catalog

logger = logging.getLogger(__name__)


class ListCategoriesInput(ToolInput):
    """Schema-of-record for ``list_categories`` (see ``schema_from_model``)."""

    include_hidden: Optional[bool] = Field(
        default=None,
        description="Include categories hidden by set_categories_hidden.",
    )
    kind: Optional[str] = Field(
        default=None, description="expense|income|transfer|adjustment|investment"
    )
    linked: Optional[bool] = Field(
        default=None,
        description="true = linked to an envelope; false = in none.",
    )
    envelope: Optional[str] = Field(
        default=None, description="Only this envelope's categories (id or name)."
    )
    search: Optional[str] = Field(default=None, description="Substring of the label.")


@tool(
    "list_categories",
    description=(
        "List transaction categories (seeded and user-made alike — all renamable/deletable). "
        "Row: OPAQUE id (cat_…; never guess one — pass back an id or label read here), label, "
        "kind, color, envelope {id, name} (null = spending in no envelope). Filters combine. "
        "Check here before create_categories. Hidden ones only with include_hidden=true."
    ),
    input_schema=schema_of(ListCategoriesInput, tool_name="list_categories"),
)
async def list_categories(input_data: dict):
    try:
        cats = budget_catalog.describe_categories(
            include_hidden=bool(input_data.get("include_hidden", False)),
            kind=input_data.get("kind"),
            linked=input_data.get("linked"),
            envelope=input_data.get("envelope"),
            search=input_data.get("search"),
        )
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}), None
    return json.dumps({"categories": cats, "count": len(cats)}, ensure_ascii=False), None


_CATEGORY_KINDS = ("expense", "income", "transfer", "adjustment", "investment")


_HEX_COLOR_PATTERN = r"^(#[0-9a-fA-F]{6})?$"


class CategoryItem(ToolInput):
    """One category to create — the per-item shape inside ``create_categories``'s ``categories``
    batch list. Terser mirror of ``CreateCategoryInput``'s single-item fields (same meaning —
    not restated at length here to avoid doubling the prompt cost of every description)."""

    label: str = Field(description="Display name, e.g. 'Startup Costs'.")
    kind: Optional[Literal["expense", "income", "transfer", "adjustment", "investment"]] = Field(
        default=None, description="Kind. Defaults to 'expense'."
    )
    color: Optional[str] = Field(
        default=None, pattern=_HEX_COLOR_PATTERN, description="Hex color, e.g. '#e67e22'."
    )


class CreateCategoryInput(ToolInput):
    """Schema-of-record for ``create_categories`` (see ``schema_from_model``)."""

    label: Optional[str] = Field(
        default=None,
        description="Human-readable display name shown in the UI (e.g. 'Startup Costs', 'Side Project').",
    )
    kind: Optional[Literal["expense", "income", "transfer", "adjustment", "investment"]] = Field(
        default=None, description="Transaction kind this category maps to. Defaults to 'expense'."
    )
    color: Optional[str] = Field(
        default=None,
        pattern=_HEX_COLOR_PATTERN,
        description="Optional hex color for the UI chip (e.g. '#e67e22'). Leave blank to auto-assign.",
    )
    categories: Optional[List[CategoryItem]] = Field(
        default=None,
        description=(
            "Batch form of the above — each item is {label, kind?, color?}; independent per item, check `results`."
        ),
    )


@tool(
    "create_categories",
    description=(
        "Create a custom transaction category (e.g. 'Startup Costs', 'Freelance') for a label that doesn't exist yet. Call list_categories first to confirm it doesn't already exist. An opaque id is minted server-side (returned as `id`) — you don't choose one. A new category is a pure label: it belongs to no group and has NO budget row (groups hold envelopes, not categories) — link it to an envelope (link_categories) to make it budgetable; until then its spending shows as 'Unbudgeted' in Plan. `categories` batches several creates."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        CreateCategoryInput, array="categories", tool_name="create_categories"
    ),
    mutates=("categories",),
)
async def create_categories(input_data: dict):
    from backend.core.errors import OpenNetWorthError
    from backend.services import categories as cat_svc

    items = input_data.get("categories") or []
    if items:
        results = []
        for i, item in enumerate(items):
            kind = item.get("kind") or "expense"
            if kind not in _CATEGORY_KINDS:
                kind = "expense"
            try:
                result = cat_svc.create_category(
                    label=item.get("label"),
                    kind=kind,
                    color=item.get("color") or "",
                )
                results.append({"ok": True, **result})
            except OpenNetWorthError as exc:
                results.append(
                    {
                        "ok": False,
                        "index": i,
                        "label": item.get("label"),
                        "error": exc.message or str(exc),
                    }
                )
        failed = sum(1 for r in results if not r["ok"])
        logger.info(
            "tool create_categories: batch created=%d failed=%d", len(results) - failed, failed
        )
        return (
            json.dumps(
                {"ok": failed == 0, "count": len(results) - failed, "results": results},
                ensure_ascii=False,
            ),
            None,
        )

    kind = input_data.get("kind", "expense")
    if kind not in _CATEGORY_KINDS:
        kind = "expense"
    try:
        result = cat_svc.create_category(
            label=input_data.get("label"),
            kind=kind,
            color=input_data.get("color") or "",
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info("tool create_categories: id=%s", result["id"])
    return json.dumps(result, ensure_ascii=False), None


class RenameCategoryItem(ToolInput):
    """One rename/merge — the per-item shape inside ``rename_categories``'s ``renames`` batch
    list. Terser mirror of ``RenameCategoryInput``'s single-item fields (same meaning)."""

    category_id: str = Field(description="Category id to rename or merge.")
    new_label: Optional[str] = Field(default=None, description="New label. Omit when merging.")
    merge_into: Optional[str] = Field(
        default=None,
        description="Existing, different category id to merge into (deletes category_id).",
    )


class RenameCategoryInput(ToolInput):
    """Schema-of-record for ``rename_categories`` (see ``schema_from_model``)."""

    category_id: Optional[str] = Field(
        default=None, description="The category id to rename (from list_categories)."
    )
    new_label: Optional[str] = Field(
        default=None, description="New display name. Omit when merging."
    )
    merge_into: Optional[str] = Field(
        default=None,
        description="An existing, DIFFERENT category id — folds 'category_id' into it and deletes 'category_id'.",
    )
    renames: Optional[List[RenameCategoryItem]] = Field(
        default=None,
        description=(
            "Batch form of the above — each item is {category_id, new_label?, merge_into?}, applied IN ORDER and freshly validated (an item merging into one an earlier item just merged away fails clearly, not silently); independent per item, check `results`."
        ),
    )


@tool(
    "rename_categories",
    description=(
        "Relabel a category, or merge it into another. Pass new_label to just rename it (figures untouched — a pure relabel, the id never changes). Pass merge_into (an existing, DIFFERENT category's id) to fold category_id's transactions/legacy budget cap/rules/envelope assignment into it and delete category_id — the destination's own label/target/budget win, nothing is silently overwritten. Checkpoint-covered (undoable). The structural category 'split' can never be renamed or merged. `renames` batches several renames/merges."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        RenameCategoryInput, array="renames", tool_name="rename_categories"
    ),
    mutates=("categories",),
)
async def rename_categories(input_data: dict):
    from backend.core.errors import OpenNetWorthError
    from backend.services import categories as cat_svc

    items = input_data.get("renames") or []
    if items:
        results = []
        for item in items:
            cid = (item.get("category_id") or "").strip()
            try:
                result = cat_svc.rename_category(
                    category_id=cid,
                    new_label=item.get("new_label"),
                    merge_into=item.get("merge_into"),
                )
                results.append({"ok": True, **result})
            except OpenNetWorthError as exc:
                results.append({"ok": False, "category_id": cid, "error": exc.message or str(exc)})
        failed = sum(1 for r in results if not r["ok"])
        logger.info(
            "tool rename_categories: batch count=%d failed=%d", len(results) - failed, failed
        )
        return (
            json.dumps(
                {"ok": failed == 0, "count": len(results) - failed, "results": results},
                ensure_ascii=False,
            ),
            None,
        )

    try:
        result = cat_svc.rename_category(
            category_id=input_data.get("category_id"),
            new_label=input_data.get("new_label"),
            merge_into=input_data.get("merge_into"),
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info("tool rename_categories: %s", result)
    return json.dumps(result, ensure_ascii=False), None


class DeleteCategoryItem(ToolInput):
    """One deletion — the per-item shape inside ``delete_categories``'s ``deletions`` batch
    list. Terser mirror of ``DeleteCategoryInput``'s single-item fields (same meaning)."""

    category_id: str = Field(description="Category id to delete.")
    reassign: Optional[bool] = Field(
        default=None, description="Move in-use transactions to reassign_to, then delete."
    )
    reassign_to: Optional[str] = Field(
        default=None, description="Target for in-use transactions (default: unclassified)."
    )


class DeleteCategoryInput(ToolInput):
    """Schema-of-record for ``delete_categories`` (see ``schema_from_model``)."""

    category_id: Optional[str] = Field(
        default=None, description="The category id to delete (from list_categories)."
    )
    reassign: Optional[bool] = Field(
        default=None, description="Move any transactions using it to reassign_to, then delete."
    )
    reassign_to: Optional[str] = Field(
        default=None,
        description=(
            "The category in-use transactions move to (must be an existing category; defaults to None/unclassified if omitted)."
        ),
    )
    deletions: Optional[List[DeleteCategoryItem]] = Field(
        default=None,
        description=(
            "Batch form of the above — each item is {category_id, reassign?, reassign_to?}; independent per item, check `results`."
        ),
    )


@tool(
    "delete_categories",
    description=(
        "Delete categories. The STRUCTURAL category 'split' cannot be deleted. Every other category — including built-in ones like Food & Dining, Transport, Tax — is user-deletable (pass its id from list_categories). If transactions still use it the deletion is REFUSED unless reassign=true — then those transactions are cleared to None (unclassified/Uncategorized) by default, or re-pointed to reassign_to if given. Also scrubs the category from budgets / Planning targets / envelope assignments / rules. Checkpoint-covered (undoable). `deletions` batches several."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        DeleteCategoryInput, array="deletions", tool_name="delete_categories"
    ),
    mutates=("categories",),
)
async def delete_categories(input_data: dict):
    from backend.core.errors import OpenNetWorthError
    from backend.services import categories as cat_svc

    items = input_data.get("deletions") or []
    if items:
        results = []
        for item in items:
            cid = (item.get("category_id") or "").strip()
            try:
                result = cat_svc.delete_category(
                    category_id=cid,
                    reassign=bool(item.get("reassign", False)),
                    reassign_to=item.get("reassign_to"),
                )
                results.append({"ok": True, **result})
            except OpenNetWorthError as exc:
                results.append({"ok": False, "category_id": cid, "error": exc.message or str(exc)})
        failed = sum(1 for r in results if not r["ok"])
        logger.info(
            "tool delete_categories: batch count=%d failed=%d", len(results) - failed, failed
        )
        return (
            json.dumps(
                {"ok": failed == 0, "count": len(results) - failed, "results": results},
                ensure_ascii=False,
            ),
            None,
        )

    try:
        result = cat_svc.delete_category(
            category_id=input_data.get("category_id", ""),
            reassign=bool(input_data.get("reassign", False)),
            reassign_to=input_data.get("reassign_to"),
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info("tool delete_categories: category_id=%s", result["category_id"])
    return json.dumps(result, ensure_ascii=False), None


class SetCategoryColorItem(ToolInput):
    """One colour update — the per-item shape inside ``set_categories_color``'s ``updates``
    batch list."""

    category_id: str = Field(description="Category id.")
    color: str = Field(description="Hex color, e.g. '#e67e22'.")


class SetCategoryColorInput(ToolInput):
    """Schema-of-record for ``set_categories_color`` (see ``schema_from_model``)."""

    category_id: Optional[str] = Field(
        default=None, description="The category id (from list_categories)."
    )
    color: Optional[str] = Field(
        default=None, description="6-digit hex color starting with '#', e.g. '#e67e22'."
    )
    updates: Optional[List[SetCategoryColorItem]] = Field(
        default=None,
        description="Batch form of the above — each item is {category_id, color}; independent per item, check `results`.",
    )


@tool(
    "set_categories_color",
    description=(
        "Set the swatch colour for a category — shown on transaction chips and budget rows. Works for both custom and built-in categories. Checkpoint-covered (undoable). Direct config write. `updates` batches several."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        SetCategoryColorInput, array="updates", tool_name="set_categories_color"
    ),
    mutates=("categories",),
)
async def set_categories_color(input_data: dict):
    from backend.core.errors import OpenNetWorthError
    from backend.services import categories as cat_svc

    items = input_data.get("updates") or []
    if items:
        results = []
        for item in items:
            cid = (item.get("category_id") or "").strip()
            try:
                result = cat_svc.set_category_color(
                    category_id=cid,
                    color=item.get("color", ""),
                )
                results.append({"ok": True, **result})
            except OpenNetWorthError as exc:
                results.append({"ok": False, "category_id": cid, "error": exc.message or str(exc)})
        failed = sum(1 for r in results if not r["ok"])
        logger.info(
            "tool set_categories_color: batch count=%d failed=%d", len(results) - failed, failed
        )
        return (
            json.dumps(
                {"ok": failed == 0, "count": len(results) - failed, "results": results},
                ensure_ascii=False,
            ),
            None,
        )

    try:
        result = cat_svc.set_category_color(
            category_id=input_data.get("category_id", ""),
            color=input_data.get("color", ""),
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info(
        "tool set_categories_color: category_id=%s color=%s", result["category_id"], result["color"]
    )
    return json.dumps(result, ensure_ascii=False), None


class SetCategoriesHiddenInput(ToolInput):
    """Schema-of-record for ``set_categories_hidden`` (see ``schema_from_model``)."""

    category_ids: Union[str, List[str]] = Field(
        min_length=1,
        json_schema_extra=require_nonempty,
        description=(
            "The category id to hide/unhide, or a LIST of several to hide/unhide together (one shared `hidden` value; e.g. 'hide these 5 categories')."
        ),
    )
    hidden: bool = Field(description="true to hide, false to unhide (restore to default views).")


@tool(
    "set_categories_hidden",
    description=(
        "Hide or unhide one or more categories from default pickable / budget views. Hiding is PRESENTATIONAL ONLY — the category and all its transactions, assignments, and history stay completely intact; it is simply removed from the default category list and budget views (pass include_hidden=true to list_categories to see hidden ones). Ledger math, net worth, and spending folds are UNAFFECTED — a hidden category with activity still folds into totals correctly. Only the structural 'split' sentinel cannot be hidden; every other category, former built-ins included, is hideable. Checkpoint-covered (undoable). Direct config write. Pass category_ids as a LIST to batch several; call twice for a mixed hide+unhide batch."
    ),
    input_schema=schema_of(SetCategoriesHiddenInput, tool_name="set_categories_hidden"),
    mutates=("categories",),
)
async def set_categories_hidden(input_data: dict):
    from backend.core.errors import OpenNetWorthError
    from backend.services import categories as cat_svc

    raw = input_data.get("category_ids")
    hidden = bool(input_data.get("hidden", True))

    if isinstance(raw, list):
        results = []
        for cid in raw:
            try:
                result = cat_svc.set_category_hidden(category_id=cid, hidden=hidden)
                results.append({"ok": True, **result})
            except OpenNetWorthError as exc:
                results.append({"ok": False, "category_id": cid, "error": exc.message or str(exc)})
        failed = sum(1 for r in results if not r["ok"])
        logger.info(
            "tool set_categories_hidden: batch count=%d failed=%d", len(results) - failed, failed
        )
        return (
            json.dumps(
                {"ok": failed == 0, "count": len(results) - failed, "results": results},
                ensure_ascii=False,
            ),
            None,
        )

    try:
        result = cat_svc.set_category_hidden(
            category_id=input_data.get("category_ids", ""),
            hidden=hidden,
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info(
        "tool set_categories_hidden: category_id=%s hidden=%s",
        result["category_id"],
        result["hidden"],
    )
    return json.dumps(result, ensure_ascii=False), None

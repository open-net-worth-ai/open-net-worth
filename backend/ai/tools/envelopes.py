"""Envelope lifecycle tools (PLAN) — the envelope ENTITY, distinct from planning.py's
per-row MONEY ops (assign/move/target).

THE MODEL (envelope ⊥ category — see CLAUDE.md's Planning section +
``.claude/rfcs/envelope-category-full-split.md``): an ENVELOPE is the entity that HOLDS
money (Assigned amount, a target, a colour, a group placement) and links 0..N CATEGORIES,
which are pure transaction labels with no money of their own. A budget row IS an envelope.
Every ``envelope_id``/``category_id`` ref here (and every ``category``/``from_category``/
``to_category`` ref in ``planning.py``) resolves as EITHER a category key currently LINKED
to a live envelope, OR — for a zero-category envelope, which has no category to name — that
envelope's own id (``env_...``).

  * ``list_envelopes`` — a read (proposed_change = None), like ``get_budget_month``; on the
    headless allowlist.
  * ``create_envelope`` / ``update_envelopes`` / ``merge_envelopes`` / ``link_categories``
    (envelope_add_category) / ``unlink_categories`` (envelope_split_category) /
    ``delete_envelope`` — APPROVE-TIER: each returns a ``proposed_change`` for the user to
    confirm; writes NOTHING. After the user confirms, the confirm card calls
    ``planning.apply_budget_change`` (the single write path) with the matching ``envelope_*``
    type. ``delete_envelope`` hard-deletes for real (ADR
    2026-09-26-deleting-an-envelope-deletes-it) — there is no ``restore_envelope`` any more; a
    delete made just now is undoable by the human's own Undo pill in the UI (server-held, not a
    Lucius tool), and an OLDER Lucius write is undoable via ``restore_checkpoint``.

All logic lives in ``services.envelopes``; these handlers only marshal in/out + build the
confirm-card display, mirroring the propose/apply split in ``planning.py``. Money writes are
NOT on the headless allowlist (no unattended envelope restructuring) — only the two reads
are.
"""

from __future__ import annotations

import json
from typing import List, Optional

from pydantic import Field

from backend.ai.tools._budget_confirm import NAMED_ARGS_PREVIEW
from backend.ai.tools._budget_confirm import proposed as _proposed
from backend.ai.tools._budget_confirm import proposed_batch as _proposed_batch
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import OpenNetWorthError
from backend.services import budget_catalog

# ── Read ─────────────────────────────────────────────────────────────────────────


class ListEnvelopesInput(ToolInput):
    """Schema-of-record for ``list_envelopes`` (see ``schema_from_model``)."""

    envelope_ids: Optional[List[str]] = Field(
        default=None, description="Only these envelopes (env_…) — e.g. to read one in full."
    )
    group: Optional[str] = Field(
        default=None, description="Only envelopes in this group (id or name)."
    )
    category: Optional[str] = Field(
        default=None, description="Only the envelope this category (id or label) is linked to."
    )
    search: Optional[str] = Field(
        default=None, description="Case-insensitive substring of the envelope's name."
    )
    has_target: Optional[bool] = Field(
        default=None, description="true = only envelopes with a Zero-Based target; false = without."
    )
    include_archived: Optional[bool] = Field(
        default=None, description="Include archived (deleted/merged-away) envelopes. Default false."
    )


@tool(
    "list_envelopes",
    description=(
        "The book's envelopes, each in full: id, name, group {id, name}, linked_categories "
        "[{id, label}], zero_based_target, note, color, managed_by_app (a Commitments or card "
        "payment envelope the app keeps — don't edit it), and — once 50/30/20 is set up — "
        "fifty_thirty_twenty {cap} (its group is the Zero-Based group above). "
        "Filters combine: envelope_ids (read specific envelopes), group, category, search, "
        "has_target. This month's money (assigned, activity, available) is get_budget_month's; "
        "archived envelopes are excluded unless include_archived=true (a deleted envelope is "
        "gone for real — its own delete-confirm names what happened to its money)."
    ),
    input_schema=schema_of(ListEnvelopesInput, tool_name="list_envelopes"),
)
async def list_envelopes(input_data: dict):
    try:
        rows = budget_catalog.describe_envelopes(
            envelope_ids=input_data.get("envelope_ids"),
            group=input_data.get("group"),
            category=input_data.get("category"),
            search=input_data.get("search"),
            has_target=input_data.get("has_target"),
            include_archived=bool(input_data.get("include_archived")),
        )
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}), None
    return json.dumps({"envelopes": rows, "count": len(rows)}, ensure_ascii=False), None


# ── Propose (approve-tier: stage a confirm card; the approval writes through the choke) ─


class CreateEnvelopeInput(ToolInput):
    """Schema-of-record for ``create_envelope`` (see ``schema_from_model``)."""

    name: str = Field(description="Envelope display name")
    group_id: Optional[str] = Field(
        default=None, description="Planning group id to place it in (omit for 'Others')"
    )
    color: Optional[str] = Field(
        default=None, description="Optional 6-digit hex display color, e.g. #3b86dd"
    )


@tool(
    "create_envelope",
    description=(
        "Create a standalone envelope — a named pot with ZERO categories linked "
        "(mints no category). Link a category afterward with "
        "link_categories. group_id places it in a group (omit for 'Others'); color is a "
        "6-digit hex swatch. Nothing else to call to apply it."
    ),
    input_schema=schema_of(CreateEnvelopeInput, tool_name="create_envelope"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def create_envelope(input_data: dict):
    change = {
        "type": "envelope_create",
        "name": (input_data.get("name") or "").strip(),
        "group_id": (input_data.get("group_id") or "").strip() or None,
        "color": (input_data.get("color") or "").strip() or None,
    }
    return _proposed(
        change,
        {"op": "Create envelope", "name": change["name"], "group_id": change["group_id"]},
    )


class EnvelopeUpdate(ToolInput):
    """One envelope's edits inside ``update_envelopes``' ``updates`` — absent = unchanged."""

    envelope_id: str = Field(description="The envelope (env_… from list_envelopes)")
    name: Optional[str] = Field(default=None, description="New name (can't be blank)")
    color: Optional[str] = Field(default=None, description="New 6-digit hex colour")
    note: Optional[str] = Field(default=None, description="New note; '' clears it")


class UpdateEnvelopesInput(ToolInput):
    """Schema-of-record for ``update_envelopes`` (see ``schema_from_model``)."""

    updates: List[EnvelopeUpdate] = Field(min_length=1, description="One entry per envelope")


def _envelope_changes(item: dict) -> list[dict]:
    envelope_id = (item.get("envelope_id") or "").strip()
    changes = []
    if item.get("name") is not None or item.get("color") is not None:
        changes.append(
            {
                "type": "envelope_rename",
                "envelope_id": envelope_id,
                "name": item.get("name"),
                "color": item.get("color"),
            }
        )
    if item.get("note") is not None:
        changes.append({"type": "envelope_note", "envelope_id": envelope_id, "note": item["note"]})
    return changes


@tool(
    "update_envelopes",
    description=(
        "Edit envelopes — each update sets any of name, color and note (note '' clears it; "
        "absent fields are unchanged). A one-category envelope is usually renamed through its "
        "category (rename_categories) so the two stay in step; rename here for a merged or "
        "zero-category envelope. Several envelopes are reported per item. Nothing else to call to apply it."
    ),
    input_schema=schema_of(UpdateEnvelopesInput, tool_name="update_envelopes"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def update_envelopes(input_data: dict):
    updates = [u for u in input_data.get("updates") or [] if isinstance(u, dict)]
    items = [c for u in updates for c in _envelope_changes(u)]
    if not items:
        return json.dumps(
            {"ok": False, "error": "Each update needs envelope_id and a name, color or note."}
        ), None
    display = {"op": "Update envelopes", "updates": updates[:12]}
    if len(items) == 1:
        return _proposed(items[0], display)
    return _proposed_batch("envelope_update_batch", items, display)


class MergeEnvelopesInput(ToolInput):
    """Schema-of-record for ``merge_envelopes`` (see ``schema_from_model``)."""

    source_envelope_id: str = Field(description="Envelope to fold away (archived after the merge)")
    target_envelope_id: str = Field(
        description="Envelope that absorbs the source's categories + money"
    )


@tool(
    "merge_envelopes",
    description=(
        "Fold source's categories + assigned money (all months) into target, "
        "then archiving source (restorable). Source's own "
        "target/note drop. Refused if either side is archived or Commitments-synced. Nothing else to call to apply it."
    ),
    input_schema=schema_of(MergeEnvelopesInput, tool_name="merge_envelopes"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def merge_envelopes(input_data: dict):
    change = {
        "type": "envelope_merge",
        "source_envelope_id": (input_data.get("source_envelope_id") or "").strip(),
        "target_envelope_id": (input_data.get("target_envelope_id") or "").strip(),
    }
    return _proposed(
        change,
        {
            "op": "Merge envelopes",
            "source_envelope_id": change["source_envelope_id"],
            "target_envelope_id": change["target_envelope_id"],
        },
    )


class LinkCategoriesInput(ToolInput):
    """Schema-of-record for ``link_categories`` (see ``schema_from_model``)."""

    envelope_id: str = Field(description="The envelope to link into (list_envelopes)")
    category_ids: List[str] = Field(
        min_length=1,
        description="The unlinked categories to link (ids from list_categories)",
    )


def _category_batch(change_type: str, op: str, input_data: dict):
    """One card for one category, one batch card for several — each item its own change
    through the same choke, reported per item."""
    envelope_id = (input_data.get("envelope_id") or "").strip()
    ids = [str(c).strip() for c in input_data.get("category_ids") or [] if str(c).strip()]
    items = [{"type": change_type, "envelope_id": envelope_id, "category_id": c} for c in ids]
    display = {"op": op, "envelope_id": envelope_id, "category_ids": ids}
    if len(items) == 1:
        return _proposed(items[0], display)
    return _proposed_batch(f"{change_type}_batch", items, display)


@tool(
    "link_categories",
    description=(
        "Link UNLINKED categories (no envelope — their spending shows as unbudgeted) to one "
        "envelope, making them budgetable. No money moves. A category already linked "
        "elsewhere is REFUSED (unlink it first); several are reported per item. Nothing else to call to apply it."
    ),
    input_schema=schema_of(LinkCategoriesInput, tool_name="link_categories"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def link_categories(input_data: dict):
    return _category_batch("envelope_add_category", "Link categories", input_data)


class UnlinkCategoriesInput(ToolInput):
    """Schema-of-record for ``unlink_categories`` (see ``schema_from_model``)."""

    envelope_id: str = Field(description="The envelope to unlink from (list_envelopes)")
    category_ids: List[str] = Field(
        min_length=1, description="The linked categories to detach from it"
    )


@tool(
    "unlink_categories",
    description=(
        "UNLINK categories from an envelope. Each becomes ENVELOPE-LESS — no new envelope is "
        "minted; its spending shows as unbudgeted until relinked. Legal even for the SOLE "
        "member (the envelope stays alive and keeps its money; nothing moves). Nothing else to call to apply it."
    ),
    input_schema=schema_of(UnlinkCategoriesInput, tool_name="unlink_categories"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def unlink_categories(input_data: dict):
    return _category_batch("envelope_split_category", "Unlink categories", input_data)


class DeleteEnvelopeInput(ToolInput):
    """Schema-of-record for ``delete_envelope`` (see ``schema_from_model``)."""

    envelope_id: str = Field(description="The envelope to delete")


@tool(
    "delete_envelope",
    description=(
        "DELETE an envelope FOR REAL. On apply: every "
        "linked category detaches (→ 'Unbudgeted', never deleted), its Assigned zeroes across "
        "EVERY month, target/note/50-30-20-cap cleared, and the envelope is HARD-DELETED — not "
        "archived, not restorable by this tool. A POSITIVE balance (unspent money) releases to "
        "Ready to Assign. A NEGATIVE balance (an unresolved overspend) is neither forgiven nor "
        "charged now: its detached categories are unlinked, and unlinked cash spend keeps "
        "holding the same hole until the month-end roll, so Ready to Assign does NOT drop. "
        "Refused if archived or Commitments-synced. Nothing else to call to apply it; a delete made "
        "in the app has its own short-lived Undo in the UI, and an older Lucius write can be "
        "undone via restore_checkpoint — nothing else to call here."
    ),
    input_schema=schema_of(DeleteEnvelopeInput, tool_name="delete_envelope"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def delete_envelope(input_data: dict):
    change = {
        "type": "envelope_delete",
        "envelope_id": (input_data.get("envelope_id") or "").strip(),
    }
    return _proposed(change, {"op": "Delete envelope", "envelope_id": change["envelope_id"]})

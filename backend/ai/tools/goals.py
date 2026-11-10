"""Savings-goal tools — "save X by date Y" (the app's Goals).

Reads (``get_goals``, ``get_goal_allocation``) surface the service-computed pace and the
auto-allocation across goals; writes (``create_goal``, ``update_goal``, ``delete_goal``) go
through the same ``TargetsStore`` write path as REST, so they are checkpoint-captured. All money
math lives in ``services.targets``; these handlers only marshal in/out. (The store, service and
REST keep their historical "targets" names; the TOOLS say goal, as the app does.)

Renamed 2026-09-26 from ``*_target(s)``: "target" also named Zero-Based's per-envelope target
(``set_envelope_targets``) and the investment target mix, three meanings for one word; and the
pre-envelope spending-limit setters that lived here were retired — the flat per-category limit
system has no screen, so a cap written there was invisible (ADR
2026-09-26-budget-and-category-tools).
"""

from __future__ import annotations

import json
import logging
from typing import List, Literal, Optional

from pydantic import Field

from backend.ai.tools._shared import no_op_write_result
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import ValidationError
from backend.services import targets as targets_service

logger = logging.getLogger(__name__)


# ── Goals (read) ─────────────────────────────────────────────────────────────


class GetGoalsInput(ToolInput):
    """Schema-of-record for ``get_goals`` (see ``schema_from_model``)."""

    status: Optional[str] = Field(
        default=None, description="Optional filter: active|completed|cancelled"
    )


@tool(
    "get_goals",
    description=(
        "Get the user's savings goals — 'save X by date Y'. Each is hydrated with the "
        "service-computed remaining (base currency), months_remaining, required_monthly_base "
        "(what to set aside each month to hit the deadline), and a pace status "
        "(on_track|ahead|behind|past_due|completed). Read this before discussing whether the "
        "user is on pace for a goal."
    ),
    input_schema=schema_of(GetGoalsInput, tool_name="get_goals"),
)
async def get_goals(input_data: dict):
    rows = targets_service.list_hydrated(status=input_data.get("status"))
    return json.dumps(rows, ensure_ascii=False), None


class GetGoalAllocationInput(ToolInput):
    """Schema-of-record for ``get_goal_allocation`` (see ``schema_from_model``)."""


@tool(
    "get_goal_allocation",
    description=(
        "Get the auto-allocation summary across active savings goals: the total monthly "
        "earmark needed (total_required_monthly_base), the user's monthly free cash "
        "(income − expenses), what's left after funding goals (free_after_targets_base), the "
        "liquid-cash baseline, and any shortfall. This is a SUGGESTION ONLY — it never moves "
        "money. To actually contribute, add a normal transfer."
    ),
    input_schema=schema_of(GetGoalAllocationInput, tool_name="get_goal_allocation"),
)
async def get_goal_allocation(input_data: dict):
    return json.dumps(targets_service.allocation_summary(), ensure_ascii=False), None


# ── Goals (write) ──────────────────────────────────────────────────────────────


class CreateGoalInput(ToolInput):
    """Schema-of-record for ``create_goal`` (see ``schema_from_model``)."""

    name: str = Field(description="e.g. 'Emergency fund'")
    target_amount: float = Field(description="The goal amount (must be > 0)")
    current_amount: Optional[float] = Field(default=None, description="Saved so far (default 0)")
    currency: Optional[str] = Field(default=None, description="Native currency; defaults to base")
    target_date: str = Field(description="Deadline, ISO date (YYYY-MM-DD)")
    linked_account_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional savings accounts whose SUMMED balance IS the progress (ids from list_accounts)",
    )
    category: Optional[str] = Field(
        default=None, description="Optional budget category tie, e.g. 'travel'"
    )
    priority: Optional[int] = Field(
        default=None, description="Funding order (lower = funded first; default 1)"
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Free-form note about the goal, stored on the target and shown with it. Display "
            "only — never read by progress, pace, or funding math. Defaults to empty."
        ),
    )


@tool(
    "create_goal",
    description=(
        "Create a savings goal ('save X by date Y'). currency defaults to the base currency; "
        "current_amount is what's saved so far (0 if starting fresh). Optionally link one or more "
        "savings accounts (linked_account_ids — an array of account ids from list_accounts); their "
        "SUMMED live balance then becomes the progress, overriding current_amount. Optionally tie "
        "it to a budget category."
    ),
    input_schema=schema_of(CreateGoalInput, tool_name="create_goal"),
    mutates=("budget",),
)
async def create_goal(input_data: dict):
    try:
        t = targets_service.create_target(input_data)
    except (ValueError, ValidationError) as e:
        return json.dumps({"ok": False, "error": str(e)}), None
    hydrated = targets_service.get_hydrated(t["id"])
    logger.info("tool create_goal: id=%s name=%s", t["id"], t.get("name"))
    return json.dumps({"ok": True, "goal": hydrated}, ensure_ascii=False), None


class UpdateGoalInput(ToolInput):
    """Schema-of-record for ``update_goal`` (see ``schema_from_model``)."""

    goal_id: str = Field(description="The savings goal to edit, by its id (from get_goals).")
    name: Optional[str] = Field(
        default=None,
        description="New name for the goal, e.g. 'Emergency fund'. Omit to leave it unchanged.",
    )
    target_amount: Optional[float] = Field(
        default=None,
        description=(
            "New goal amount, in the target's own `currency`. Must be > 0. Omit to leave it "
            "unchanged."
        ),
    )
    current_amount: Optional[float] = Field(
        default=None,
        description=(
            "New saved-so-far amount, in the target's own `currency`. IGNORED for progress "
            "whenever `linked_account_ids` is non-empty — those accounts' summed live balance "
            "is authoritative then. Omit to leave it unchanged."
        ),
    )
    currency: Optional[str] = Field(
        default=None,
        description=(
            "New native 3-letter ISO currency for this target's amounts (progress and pace are "
            "compared in the portfolio base after conversion). Omit to leave it unchanged."
        ),
    )
    target_date: Optional[str] = Field(default=None, description="ISO date (YYYY-MM-DD)")
    linked_account_ids: Optional[List[str]] = Field(
        default=None,
        description="Funding accounts whose SUMMED balance IS the progress (replaces the full list)",
    )
    category: Optional[str] = Field(
        default=None,
        description=(
            "Budget category label tied to this goal, e.g. 'travel'. Stored as a plain label "
            "only — it does not link the target to an envelope and does not affect progress or "
            "funding. Pass an empty string to clear it; omit to leave it unchanged."
        ),
    )
    priority: Optional[int] = Field(
        default=None,
        description=(
            "Funding order — LOWER is funded first (default 1). Sorts the target list and the "
            "funding plan. Omit to leave it unchanged."
        ),
    )
    status: Optional[Literal["active", "completed", "cancelled"]] = Field(
        default=None,
        description=(
            "Declared lifecycle state. 'cancelled' is terminal and passes through to reads "
            "verbatim. NOTE: reads DERIVE the displayed verdict — a target whose progress "
            "meets its amount reads 'completed' whether or not you set it here, and an "
            "unmet one reads ahead/on_track/behind/past_due from its pace regardless. Omit to leave it unchanged."
        ),
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Replacement free-form note on the goal (replaces the whole note, never appends). "
            "Display only. Omit to leave it unchanged."
        ),
    )


@tool(
    "update_goal",
    description=(
        "Update a savings goal — change its amount, deadline, current saved amount, linked "
        "accounts, category, priority, note, or mark it completed/cancelled (status). Pass only "
        "the fields to change. Call get_goals first for the id."
    ),
    input_schema=schema_of(UpdateGoalInput, tool_name="update_goal"),
    mutates=("budget",),
)
async def update_goal(input_data: dict):
    tid = input_data.get("goal_id") or ""
    changes = {
        k: v
        for k, v in input_data.items()
        if k
        in (
            "name",
            "target_amount",
            "current_amount",
            "currency",
            "target_date",
            "linked_account_ids",
            "category",
            "priority",
            "status",
            "note",
        )
    }
    if not changes:
        # An id-only call carries no field that could change the target — the store's own
        # update_target(tid, {}) short-circuits to find_by_id and echoes the unchanged row,
        # byte-indistinguishable from a real edit. Check existence first (a missing id keeps
        # its not-found error) and report the empty patch honestly instead.
        hydrated = targets_service.get_hydrated(tid)
        if hydrated is None:
            return json.dumps({"ok": False, "error": "goal not found"}), None
        return json.dumps(no_op_write_result({"goal": hydrated}), ensure_ascii=False), None
    try:
        updated = targets_service.update_target(tid, changes)
    except ValidationError as e:
        return json.dumps({"ok": False, "error": str(e)}), None
    if updated is None:
        return json.dumps({"ok": False, "error": "goal not found"}), None
    return json.dumps(
        {"ok": True, "goal": targets_service.get_hydrated(tid)}, ensure_ascii=False
    ), None


class DeleteGoalInput(ToolInput):
    """Schema-of-record for ``delete_goal`` (see ``schema_from_model``)."""

    goal_id: str = Field(
        description=(
            "The savings goal to delete, by its id (from get_goals). Removes the "
            "goal outright — it is not archived and linked accounts are untouched."
        )
    )


@tool(
    "delete_goal",
    description="Delete a savings goal. This removes the goal entirely.",
    input_schema=schema_of(DeleteGoalInput, tool_name="delete_goal"),
    mutates=("budget",),
)
async def delete_goal(input_data: dict):
    ok = targets_service.targets_store.delete(input_data.get("goal_id") or "")
    return json.dumps({"ok": ok}), None

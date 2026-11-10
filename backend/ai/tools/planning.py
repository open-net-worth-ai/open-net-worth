"""Planning (YNAB envelope-budgeting) tools (PLAN) — UI↔Lucius parity.

THE MODEL (envelope ⊥ category — see ``docs/PLANNING_MODEL.md`` +
``.claude/rfcs/envelope-category-full-split.md``): an ENVELOPE is the entity that HOLDS
money (Assigned amount, a target, a colour, a group placement) and links 0..N CATEGORIES,
which are pure transaction labels with no money of their own. A budget row IS an envelope —
``get_budget_month`` renders one row per non-archived envelope, full stop, and every money
ref parameter across these tools (``envelope_id``, ``from_envelope_id``, ``to_envelope_id``)
is the ENVELOPE's own id — get it from ``get_budget_month``'s row ``envelope_id``/``key``, or
``list_envelopes``. An UNLINKED category (one mapped to no envelope) has no budget row at
all: it can't be assigned/targeted/moved directly until linked (``link_categories``); its
activity instead surfaces read-only in the derived "Unbudgeted" group.

  * ``get_budget_month``            — read the full budget table (proposed_change = None;
                                      on the headless allowlist — a read, never a write).
  * ``assign_budget`` / ``move_money`` / ``set_envelope_targets`` and the envelope-group CRUD
    (``create_envelope_group`` / ``rename_envelope_groups`` / ``reorder_envelope_groups`` /
    ``delete_envelope_groups`` / ``move_envelopes_to_group`` / ``reorder_envelopes_in_group`` —
    renamed 2026-07-19 from ``*_category_group(s)``, which contradicted the sibling
    ``move_envelopes_to_group``: group placement has been envelope-keyed, not category-keyed,
    since the envelope split; the old names were migrated, not aliased)
    — APPROVE-TIER: each returns a
    ``proposed_change`` for the user to confirm; it writes NOTHING. After the user confirms,
    approving the card re-runs THIS tool on the arguments the server stored.
    Every one writes through the one service choke point
    ``planning_service.apply_budget_change`` (checkpoint-covered) — there is no apply TOOL;
    the confirm card applies itself (ADR 2026-08-25).

The envelope ENTITY's own lifecycle (the ``list_envelopes`` read; ``create_envelope`` /
``update_envelopes`` / ``merge_envelopes`` / ``link_categories`` / ``unlink_categories`` /
``delete_envelope`` APPROVE-TIER writes — each a thin wrapper over the matching ``envelope_*``
``apply_budget_change`` type) lives in the
sibling module ``envelopes.py``, kept separate so this file stays scoped to the per-row MONEY
ops + category-group CRUD (mirrors ``categories.py`` already being split out for the same
reason).

All math lives in ``services.planning``; these handlers only marshal in/out + build the
confirm-card display. Money writes are NOT on the headless allowlist (no unattended money
assignment) — only ``get_budget_month`` is.

**Batch fields (2026-07-19, "act on ONE thing should accept MANY"):** budgeting a month means
assigning to 15-20 envelopes — today that was 15-20 calls. ``assign_budget`` (``assignments``),
``move_money`` (``moves``), ``set_envelope_targets`` (``targets``), ``rename_envelope_groups``
(``groups``) and ``delete_envelope_groups`` (``group_ids``) all gained an optional plural field
alongside their existing singular one — the trivial single-item call stays BYTE-IDENTICAL. At
the time, none of these were renamed (each name was judged to read fine for either arity) — see
the "2026-07-19c naming-finish" note below for the three that were subsequently renamed after
all. A batch propose stages ONE ``*_batch`` change (``items`` = N fully-formed single-op change
dicts, each with its own ``type``); the write path LOOPS them through the SAME per-op
choke (``services.planning.apply_budget_change``, one call per item) — best-effort, never
atomic, so one bad item (an unknown envelope, a stale group id) is reported without discarding
the rows that succeeded (mirrors ``bulk_update_transactions``'s per-id result reporting).
``reorder_envelopes_in_group`` was left singular — it already takes the FULL ordered list of one
group's rows, which IS its plural shape; reordering several groups in one call would need a
nested per-group structure with no real-world use case observed. ``create_envelope_group``,
``auto_assign`` and the since-retired per-field note tools were out of scope for this pass (not
named in the audit that drove this change).

**2026-07-19c naming-finish (the ``tool_inventory --lint`` sweep of the last 12 arity-naming
defects):** ``set_envelope_target``/``rename_envelope_group``/``delete_envelope_group`` each
already batched over an entity SET (``targets``/``groups``/``group_ids`` — several DIFFERENT
envelopes or groups, never one entity's own sub-parts), so leaving the tool NAME singular after
the batch-fields pass above was, on a fresh read, the same arity-naming lie the rest of that
sweep fixed elsewhere. Renamed to ``set_envelope_targets``/``rename_envelope_groups``/
``delete_envelope_groups``; the old names were migrated onto them via
``tool_renames.RENAMES``, never aliased. ``assign_budget`` was
DELIBERATELY left singular in this same pass — not an oversight, a judgment call recorded in
``backend/scripts/tool_inventory.py``'s ``UNCOUNTABLE_OBJECTS`` comment: its ``assignments``
array is the identical entity-set shape, but "budget" plays the same substance/domain role
``move_money``'s "money" already does (the model is assigning BUDGET, a mass noun, to however
many envelopes — not several distinct "budgets"), so it is exempted on the same grounds as its
sibling rather than pluralized into a name that would misleadingly read as "several different
household budgets."

**Follow-up param rename (2026-07-19, owner complaint — "why is account_id a list and not
account_ids? what the fuck is that?"):** ``move_envelopes_to_group`` DID widen its ``category``
param to accept a LIST at the pass above, and reasoned the tool NAME didn't need to change — but
left the PARAM singularly named while it silently accepted a list, exactly the trap the owner
called out on a sibling tool: the model reads ``category``, wants to move several rows, and
STRINGIFIES the list into one string instead of passing an array. The param is renamed to
``categories`` (still scalar-or-list — a single move stays a trivial one-line call); the TOOL
name is unchanged (the task itself only called for a param rename here, and no stored data
persists a param name the way it persists a tool name, so no alias/migration entry is needed).
"""

from __future__ import annotations

import json
import logging
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.ai.tools._budget_confirm import NAMED_ARGS_PREVIEW
from backend.ai.tools._budget_confirm import proposed as _proposed
from backend.ai.tools._budget_confirm import proposed_batch as _proposed_batch
from backend.ai.tools._shared import no_op_write_result
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import (
    ToolInput,
    schema_of,
    schema_of_batch_only,
)
from backend.core.errors import OpenNetWorthError
from backend.core.user_time import user_today
from backend.services import budget_catalog
from backend.services import planning as planning_service

logger = logging.getLogger(__name__)


class _PlanItem(BaseModel):
    """Base for a per-item object nested inside a batch array (``assignments``, ``moves``,
    ``targets``, ``groups``, …). ``extra='forbid'`` mirrors ``ToolInput`` so a typo'd field in
    ONE item of a batch is rejected the same way a bad top-level field is, rather than silently
    ignored."""

    model_config = ConfigDict(extra="forbid")


# ── Read ─────────────────────────────────────────────────────────────────────────


class GetBudgetMonthInput(ToolInput):
    """Schema-of-record for ``get_budget_month`` (see ``schema_from_model``)."""

    month: Optional[str] = Field(
        default=None,
        description="Budget month as YYYY-MM (e.g. '2026-06'). Omit for the current month.",
    )


@tool(
    "get_budget_month",
    description=(
        "Zero-Based's envelope budget for a month (default: current) — for 50/30/20 use "
        "get_budget_view. Ready to "
        "Assign is cash not yet assigned to an envelope. An envelope HOLDS its own overspend "
        "until month-end — Ready to Assign does not move when a category goes red, cash or "
        "card. If still red when the month rolls, the hole is charged to next month's Ready to "
        "Assign. Covering an overspend BEFORE then costs Ready to Assign, up to the hole's "
        "size. CASH spending with no envelope "
        "(uncategorized, or a category linked to none) HOLDS the same way, in a bucket nothing "
        "can be assigned to (held_outside_envelopes) — charged at the roll if still unsorted. "
        "CARD debt never moves Ready to Assign; card credit is money, spent first. Returns "
        "ready_to_assign and the terms it is made "
        "of (on_budget_cash, card_credit, pre_budget_baseline, held_in_envelopes, "
        "held_outside_envelopes, "
        "card_spend_not_covered, assigned_in_future_months), plus Category Groups → rows (ONE PER "
        "ENVELOPE). Row fields: envelope_id (the ref for assign_budget/move_money/"
        "set_envelope_targets — key mirrors the same value), linked_categories [{id, label}], "
        "note, color, "
        "Assigned/Activity/Available, target, needed_this_month, status. 'Uncategorized'/"
        "'Unbudgeted' groups hold uncategorized/unlinked activity (link_categories folds the "
        "latter in). Empty book → zeros, never fabricated. "
        "flow and cash_overspending are the SAME identity, reframed as YNAB's own added/deducted "
        "terms for the breakdown popover — not new numbers to re-derive."
    ),
    input_schema=schema_of(GetBudgetMonthInput, tool_name="get_budget_month"),
)
async def get_budget_month(input_data: dict):

    month = (input_data.get("month") or "").strip()
    if not month:
        t = user_today()
        month = f"{t.year:04d}-{t.month:02d}"
    try:
        table = budget_catalog.label_budget_rows(planning_service.budget_month(month))
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}), None
    return json.dumps(table, ensure_ascii=False), None


class AssignmentItem(_PlanItem):
    """One row of a batch ``assign_budget`` call."""

    envelope_id: str = Field(
        description="The envelope id (env_...) from get_budget_month/list_envelopes"
    )
    amount: float = Field(description="Total assigned for the month (base currency)")
    month: Optional[str] = Field(
        default=None, description="Overrides the top-level month for THIS row only"
    )


class AssignBudgetInput(ToolInput):
    """Schema-of-record for ``assign_budget`` (see ``schema_from_model``)."""

    month: Optional[str] = Field(
        default=None,
        description=(
            "Budget month YYYY-MM. Required for the single-envelope form; in the batch form "
            "(assignments) it's the default for any row that omits its own month."
        ),
    )
    envelope_id: Optional[str] = Field(
        default=None,
        description="Single-envelope form: the envelope id (env_...) from get_budget_month/list_envelopes",
    )
    amount: Optional[float] = Field(
        default=None,
        description="Single-envelope form: total assigned for the month (base currency)",
    )
    assignments: Optional[List[AssignmentItem]] = Field(
        default=None,
        description=(
            "Batch form: assign a DIFFERENT amount to several envelopes in ONE call — e.g. 500 "
            "to groceries AND 200 to dining. Each row may override month. Omit envelope_id/"
            "amount at the top level when using this."
        ),
    )


@tool(
    "assign_budget",
    description=(
        "ASSIGN money into an envelope for a month (YNAB 'give every dollar a "
        "job'). amount SETS the TOTAL for (month, envelope). "
        "envelope_id is the envelope's own id (get_budget_month's row envelope_id/key, or "
        "list_envelopes). Use move_money to shift between envelopes.\n\n"
        "MANY AT ONCE: pass `assignments` — [{envelope_id, amount, month?}] — to assign "
        "different amounts to several envelopes in ONE call instead of one call per envelope "
        "(a whole month's budgeting — 15-20 envelopes — in ONE call). Each row may override "
        "month; the top-level month is the default. Nothing else to call to apply it."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        AssignBudgetInput, array="assignments", tool_name="assign_budget"
    ),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def assign_budget(input_data: dict):
    top_month = (input_data.get("month") or "").strip()
    raw_rows = input_data.get("assignments")
    if raw_rows:
        items = [
            {
                "type": "assign",
                "month": (row.get("month") or top_month or "").strip(),
                "envelope_id": (row.get("envelope_id") or "").strip(),
                "amount": row.get("amount"),
            }
            for row in raw_rows
        ]
        display = {
            "op": "Assign (batch)",
            "count": len(items),
            "rows": [
                {"month": i["month"], "envelope_id": i["envelope_id"], "amount": i["amount"]}
                for i in items[:12]
            ],
        }
        return _proposed_batch("assign_batch", items, display)
    change = {
        "type": "assign",
        "month": top_month,
        "envelope_id": (input_data.get("envelope_id") or "").strip(),
        "amount": input_data.get("amount"),
    }
    return _proposed(
        change,
        {
            "op": "Assign",
            "month": change["month"],
            "envelope_id": change["envelope_id"],
            "amount": change["amount"],
        },
    )


class MoveItem(_PlanItem):
    """One row of a batch ``move_money`` call."""

    from_envelope_id: str = Field(description="Source: an envelope id, or '__rta'")
    to_envelope_id: str = Field(description="Destination: an envelope id, or '__rta'")
    amount: float = Field(description="Amount to move (base currency, > 0)")
    month: Optional[str] = Field(
        default=None, description="Overrides the top-level month for THIS row only"
    )


class MoveMoneyInput(ToolInput):
    """Schema-of-record for ``move_money`` (see ``schema_from_model``)."""

    month: Optional[str] = Field(
        default=None,
        description=(
            "Budget month YYYY-MM. Required for the single-move form; in the batch form "
            "(moves) it's the default for any row that omits its own month."
        ),
    )
    from_envelope_id: Optional[str] = Field(
        default=None, description="Single-move form: source envelope id, or '__rta'"
    )
    to_envelope_id: Optional[str] = Field(
        default=None, description="Single-move form: destination envelope id, or '__rta'"
    )
    amount: Optional[float] = Field(
        default=None, description="Single-move form: amount to move (base currency, > 0)"
    )
    moves: Optional[List[MoveItem]] = Field(
        default=None,
        description=(
            "Batch form: several independent money moves in ONE call — e.g. release $50 from "
            "groceries to RTA AND $30 from dining to RTA. Omit from_envelope_id/to_envelope_id/"
            "amount at the top level when using this."
        ),
    )


@tool(
    "move_money",
    description=(
        "MOVE assigned money between two envelopes within a month. from/to must be DIFFERENT envelopes. Either end may be '__rta' (not "
        "both): to '__rta' releases money to Ready-to-Assign (capped at the source's "
        "available this month); from '__rta' funds FROM Ready-to-Assign (may go negative, "
        "like assign_budget).\n\n"
        "MANY AT ONCE: pass `moves` — [{from_envelope_id, to_envelope_id, amount, month?}] — "
        "for several independent moves in ONE call instead of one call each. Nothing else to call to apply it."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(MoveMoneyInput, array="moves", tool_name="move_money"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def move_money(input_data: dict):
    top_month = (input_data.get("month") or "").strip()
    raw_rows = input_data.get("moves")
    if raw_rows:
        items = [
            {
                "type": "move_money",
                "month": (row.get("month") or top_month or "").strip(),
                "from_envelope_id": (row.get("from_envelope_id") or "").strip(),
                "to_envelope_id": (row.get("to_envelope_id") or "").strip(),
                "amount": row.get("amount"),
            }
            for row in raw_rows
        ]
        display = {
            "op": "Move money (batch)",
            "count": len(items),
            "rows": [
                {
                    "month": i["month"],
                    "from_envelope_id": i["from_envelope_id"],
                    "to_envelope_id": i["to_envelope_id"],
                    "amount": i["amount"],
                }
                for i in items[:12]
            ],
        }
        return _proposed_batch("move_money_batch", items, display)
    change = {
        "type": "move_money",
        "month": top_month,
        "from_envelope_id": (input_data.get("from_envelope_id") or "").strip(),
        "to_envelope_id": (input_data.get("to_envelope_id") or "").strip(),
        "amount": input_data.get("amount"),
    }
    return _proposed(
        change,
        {
            "op": "Move money",
            "month": change["month"],
            "from_envelope_id": change["from_envelope_id"],
            "to_envelope_id": change["to_envelope_id"],
            "amount": change["amount"],
        },
    )


# Shared between the batch `targets[]` item and the single-envelope top-level twins below.
# The nested shape shipped blank while the top level carried every one of these — write the
# text once so a future edit cannot document one shape and leave the other silent.
_TARGET_TYPE_DESC = "Target type; '' or omitted clears (or, with snooze_until, snoozes)"
_TARGET_AMOUNT_DESC = "Target amount (base currency, > 0)"
_TARGET_DATE_DESC = "Deadline YYYY-MM-DD (required for by_date, optional for yearly)"
_TARGET_DUE_WEEKDAY_DESC = "0-6 (Monday=0) — weekly targets only"
_TARGET_DUE_DAY_DESC = "1-28, or -1 = last day of the month — monthly targets only"
_TARGET_SNOOZE_DESC = "YYYY-MM — suspend funding through this month (blank un-snoozes)"
_TARGET_WHOLE_AMOUNT_DESC = (
    "How progress is measured. true (default) = what you SET ASIDE: this "
    "month's assigned (recurring) or contributions to date (by_date) — "
    "spending never sets it back. false = the BALANCE: rollover + assigned "
    "(recurring) or current available (by_date). Omit to keep default."
)


class TargetItem(_PlanItem):
    """One row of a batch ``set_envelope_targets`` call — same fields as the single-envelope
    form (see ``SetEnvelopeTargetInput``)."""

    envelope_id: str = Field(
        description="The envelope id (env_...) from get_budget_month/list_envelopes"
    )
    type: Optional[Literal["monthly", "weekly", "yearly", "by_date", ""]] = Field(
        default=None, description=_TARGET_TYPE_DESC
    )
    amount: Optional[float] = Field(default=None, description=_TARGET_AMOUNT_DESC)
    date: Optional[str] = Field(default=None, description=_TARGET_DATE_DESC)
    due_weekday: Optional[int] = Field(default=None, description=_TARGET_DUE_WEEKDAY_DESC)
    due_day: Optional[int] = Field(default=None, description=_TARGET_DUE_DAY_DESC)
    snooze_until: Optional[str] = Field(default=None, description=_TARGET_SNOOZE_DESC)
    needs_whole_amount: Optional[bool] = Field(default=None, description=_TARGET_WHOLE_AMOUNT_DESC)


class SetEnvelopeTargetInput(ToolInput):
    """Schema-of-record for ``set_envelope_targets`` (see ``schema_from_model``)."""

    envelope_id: Optional[str] = Field(
        default=None,
        description="Single-envelope form: the envelope id (env_...) from get_budget_month/list_envelopes",
    )
    type: Optional[Literal["monthly", "weekly", "yearly", "by_date", ""]] = Field(
        default=None, description=_TARGET_TYPE_DESC
    )
    amount: Optional[float] = Field(default=None, description=_TARGET_AMOUNT_DESC)
    date: Optional[str] = Field(default=None, description=_TARGET_DATE_DESC)
    due_weekday: Optional[int] = Field(default=None, description=_TARGET_DUE_WEEKDAY_DESC)
    due_day: Optional[int] = Field(default=None, description=_TARGET_DUE_DAY_DESC)
    snooze_until: Optional[str] = Field(default=None, description=_TARGET_SNOOZE_DESC)
    needs_whole_amount: Optional[bool] = Field(default=None, description=_TARGET_WHOLE_AMOUNT_DESC)
    targets: Optional[List[TargetItem]] = Field(
        default=None,
        description=(
            "Batch form: set a DIFFERENT target spec on several envelopes in ONE call. Omit "
            "envelope_id (and the other single-form fields) at the top level when using this."
        ),
    )


def _target_change_and_display(row: dict) -> tuple[dict, dict]:
    """Build the ``set_target`` change + confirm-card display fields from one row of fields —
    shared by the single-envelope form and each item of a batch ``targets`` call."""
    ttype = (row.get("type") or "").strip() or None
    has_snooze = "snooze_until" in row
    snooze_until = (row.get("snooze_until") or "").strip() or None
    change = {
        "type": "set_target",
        "envelope_id": (row.get("envelope_id") or "").strip(),
        "target_type": ttype,
        "amount": row.get("amount", 0.0),
        "date": (row.get("date") or "").strip() or None,
    }
    if row.get("due_weekday") is not None:
        change["due_weekday"] = row["due_weekday"]
    if row.get("due_day") is not None:
        change["due_day"] = row["due_day"]
    if has_snooze:
        change["snooze_until"] = snooze_until
    # Only thread needs_whole_amount when explicitly provided; omitting preserves the True
    # default at the service layer so existing stored targets are never silently altered.
    if "needs_whole_amount" in row:
        change["needs_whole_amount"] = bool(row["needs_whole_amount"])
    if ttype is None and snooze_until:
        op = "Snooze target"
    elif ttype is None:
        op = "Unsnooze target" if has_snooze else "Clear target"
    else:
        op = "Set target"
    display = {
        "op": op,
        "envelope_id": change["envelope_id"],
        "target_type": ttype,
        "amount": change["amount"],
        "date": change["date"],
        "snooze_until": snooze_until,
        "needs_whole_amount": change.get("needs_whole_amount"),
    }
    return change, display


@tool(
    "set_envelope_targets",
    description=(
        "Set, snooze, or clear a funding TARGET on an envelope (basis for "
        "the '$X more needed' pill), stored PER ENVELOPE (one per merged envelope). type: monthly/weekly/yearly/by_date (see amount + "
        "date). Deadline display fields: due_weekday (0-6, weekly), due_day (1-28/-1=last, "
        "monthly), date (yearly). snooze_until (YYYY-MM) suspends funding; alone=snooze, "
        "blank=un-snooze; type='' clears.\n\n"
        "MANY AT ONCE: pass `targets` — [{envelope_id, type?, amount?, date?, ...}] — to set a "
        "different target on several envelopes in ONE call. Nothing else to call to apply it."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        SetEnvelopeTargetInput, array="targets", tool_name="set_envelope_targets"
    ),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def set_envelope_targets(input_data: dict):
    raw_rows = input_data.get("targets")
    if raw_rows:
        items = []
        displays = []
        for row in raw_rows:
            change, display = _target_change_and_display(row)
            items.append(change)
            displays.append(display)
        batch_display = {
            "op": "Set target (batch)",
            "count": len(items),
            "rows": displays[:12],
        }
        return _proposed_batch("set_target_batch", items, batch_display)
    change, display = _target_change_and_display(input_data)
    return _proposed(change, display)


class CreateEnvelopeGroupInput(ToolInput):
    """Schema-of-record for ``create_envelope_group`` (see ``schema_from_model``)."""

    name: str = Field(description="Group display name")


@tool(
    "create_envelope_group",
    description=(
        "Create a new, EMPTY envelope group (a heading that holds budget envelopes — "
        "categories belong to no group). Fill it afterwards with move_envelopes_to_group. Refused "
        "for the names of the app's own groups (Commitments, Credit Card Payments) and for "
        "reserved `__x__` names."
    ),
    input_schema=schema_of(CreateEnvelopeGroupInput, tool_name="create_envelope_group"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def create_envelope_group(input_data: dict):
    change = {
        "type": "group_create",
        "name": (input_data.get("name") or "").strip(),
    }
    return _proposed(change, {"op": "Create group", "name": change["name"]})


class GroupRenameItem(_PlanItem):
    """One row of a batch ``rename_envelope_groups`` call."""

    group_id: str = Field(
        description="The envelope group to rename, from get_budget_month's group ids."
    )
    name: Optional[str] = Field(default=None, description="New name (omit to keep)")
    collapsed: Optional[bool] = Field(default=None, description="Collapse/expand (omit to keep)")


class RenameEnvelopeGroupInput(ToolInput):
    """Schema-of-record for ``rename_envelope_groups`` (see ``schema_from_model``)."""

    group_id: Optional[str] = Field(default=None, description="Single-group form")
    name: Optional[str] = Field(default=None, description="New name (omit to keep)")
    collapsed: Optional[bool] = Field(default=None, description="Collapse/expand (omit to keep)")
    groups: Optional[List[GroupRenameItem]] = Field(
        default=None,
        description=(
            "Batch form: rename/collapse several groups in ONE call — [{group_id, name?, "
            "collapsed?}]. Omit group_id at the top level when using this."
        ),
    )


@tool(
    "rename_envelope_groups",
    description=(
        "Rename a Planning group (and/or collapsing it) — a bucket of envelopes, "
        "not a category grouping. Get the group_id from get_budget_month "
        "(groups[].id).\n\n"
        "MANY AT ONCE: pass `groups` — [{group_id, name?, collapsed?}] — to rename several "
        "groups in ONE call."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        RenameEnvelopeGroupInput, array="groups", tool_name="rename_envelope_groups"
    ),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def rename_envelope_groups(input_data: dict):
    raw_rows = input_data.get("groups")
    if raw_rows:
        items = []
        no_op_group_ids = []
        for row in raw_rows:
            gid = row.get("group_id") or ""
            name = row.get("name")
            collapsed = row.get("collapsed")
            # No name AND no collapsed: nothing this row could change. Staging it anyway
            # would produce a card that, once confirmed, echoes the group back unchanged
            # (services.planning._op_group_rename patches {} and returns the row as-is) —
            # indistinguishable from a real rename. Drop it from what gets staged.
            if name is None and collapsed is None:
                no_op_group_ids.append(gid)
                continue
            items.append(
                {"type": "group_rename", "group_id": gid, "name": name, "collapsed": collapsed}
            )
        if not items:
            return (
                json.dumps(no_op_write_result({"group_ids": no_op_group_ids}), ensure_ascii=False),
                None,
            )
        display = {
            "op": "Rename group (batch)",
            "count": len(items),
            "rows": [{"group_id": i["group_id"], "name": i["name"]} for i in items[:12]],
        }
        if no_op_group_ids:
            display["no_op_group_ids"] = no_op_group_ids
        # Tell the MODEL too, not just the confirm-card display — a silently-dropped no-op
        # row would leave it claiming it staged a rename for a group it didn't. It rides on
        # the CHANGE now rather than the tool_result text: a staged call has no result text
        # (it pauses), so ``_write_budget_change`` echoes this back when the approval runs.
        return _proposed_batch(
            "group_rename_batch",
            items,
            display,
            note={"no_op_group_ids": no_op_group_ids} if no_op_group_ids else None,
        )
    name = input_data.get("name")
    collapsed = input_data.get("collapsed")
    group_id = input_data.get("group_id") or ""
    if name is None and collapsed is None:
        return json.dumps(no_op_write_result({"group_id": group_id}), ensure_ascii=False), None
    change = {
        "type": "group_rename",
        "group_id": group_id,
        "name": name,
        "collapsed": collapsed,
    }
    return _proposed(
        change,
        {"op": "Rename group", "group_id": change["group_id"], "name": change["name"]},
    )


class ReorderEnvelopeGroupsInput(ToolInput):
    """Schema-of-record for ``reorder_envelope_groups`` (see ``schema_from_model``)."""

    order: List[str] = Field(min_length=1, description="Group ids in the desired order")


@tool(
    "reorder_envelope_groups",
    description=(
        "Reorder the Planning groups (buckets of envelopes). Pass "
        "`order` = the full list of group ids in the desired top-to-bottom order (ids from "
        "get_budget_month)."
    ),
    input_schema=schema_of(ReorderEnvelopeGroupsInput, tool_name="reorder_envelope_groups"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def reorder_envelope_groups(input_data: dict):
    change = {"type": "group_reorder", "order": input_data.get("order") or []}
    return _proposed(change, {"op": "Reorder groups", "order": change["order"]})


class DeleteEnvelopeGroupInput(ToolInput):
    """Schema-of-record for ``delete_envelope_groups`` (see ``schema_from_model``). Only the
    batch array is advertised — a bare ``group_id`` is still ACCEPTED by the handler (dispatch
    validates effective type, never ``additionalProperties``), one spelling per call."""

    group_ids: List[str] = Field(min_length=1, description="The groups to delete")


@tool(
    "delete_envelope_groups",
    description=(
        "Delete Planning groups (buckets of envelopes) — `group_ids`, from get_budget_month or "
        "list_envelopes. Their envelopes are NOT lost: they move into the 'Others' catch-all "
        "group. Nothing else to call to apply it."
    ),
    input_schema=schema_of(DeleteEnvelopeGroupInput, tool_name="delete_envelope_groups"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def delete_envelope_groups(input_data: dict):
    raw_ids = [g for g in (input_data.get("group_ids") or []) if g]
    if raw_ids:
        items = [{"type": "group_delete", "group_id": gid} for gid in raw_ids]
        display = {"op": "Delete group (batch)", "count": len(items), "group_ids": raw_ids[:12]}
        return _proposed_batch("group_delete_batch", items, display)
    change = {"type": "group_delete", "group_id": input_data.get("group_id") or ""}
    return _proposed(change, {"op": "Delete group", "group_id": change["group_id"]})


class MoveEnvelopesToGroupInput(ToolInput):
    """Schema-of-record for ``move_envelopes_to_group`` (see ``schema_from_model``)."""

    envelope_ids: List[str] = Field(
        min_length=1,
        description=(
            "The envelopes to move (env_… from list_envelopes). Several all go to the SAME group."
        ),
    )
    group_id: str = Field(description="Destination group id")
    index: Optional[int] = Field(
        default=None,
        description=(
            "Optional insert position within the destination group (0-based; omit to append "
            "at the end). Only for a single envelope — several are appended in the given order."
        ),
    )


@tool(
    "move_envelopes_to_group",
    description=(
        "Move envelopes into an envelope group (out of any other) — group placement is "
        "envelope-keyed. Refused for a group the app keeps (Commitments, Credit Card Payments), "
        "for an income category (income has no envelope and no group), and for an envelope "
        "holding several categories. Several "
        "envelopes go to the SAME group, appended in the given order (`index` places a single "
        "one). Nothing else to call to apply it."
    ),
    input_schema=schema_of(MoveEnvelopesToGroupInput, tool_name="move_envelopes_to_group"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def move_envelopes_to_group(input_data: dict):
    raw = input_data.get("envelope_ids")
    group_id = input_data.get("group_id") or ""
    if isinstance(raw, list) and len(raw) == 1:
        raw = raw[0]
    if isinstance(raw, list):
        refs = [str(c).strip() for c in raw if str(c).strip()]
        items = [{"type": "move_category", "category": ref, "group_id": group_id} for ref in refs]
        display = {
            "op": "Move category (batch)",
            "count": len(items),
            "categories": refs[:12],
            "group_id": group_id,
        }
        return _proposed_batch("move_category_batch", items, display)
    change = {
        "type": "move_category",
        "category": (raw or "").strip(),
        "group_id": group_id,
    }
    if input_data.get("index") is not None:
        change["index"] = input_data.get("index")
    return _proposed(
        change,
        {"op": "Move category", "category": change["category"], "group_id": change["group_id"]},
    )


class ReorderEnvelopesInGroupInput(ToolInput):
    """Schema-of-record for ``reorder_envelopes_in_group`` (see ``schema_from_model``)."""

    group_id: str = Field(description="The group whose envelopes to reorder")
    envelope_ids: List[str] = Field(
        min_length=1, description="ALL of the group's envelope ids, in the new order"
    )


@tool(
    "reorder_envelopes_in_group",
    description=(
        "Reorder the envelopes WITHIN one Planning group: envelope_ids = ALL of that group's "
        "envelopes in the new order — same set, just reordered (move_envelopes_to_group adds "
        "or removes one). Nothing else to call to apply it."
    ),
    input_schema=schema_of(ReorderEnvelopesInGroupInput, tool_name="reorder_envelopes_in_group"),
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def reorder_envelopes_in_group(input_data: dict):
    change = {
        "type": "reorder_categories",
        "group_id": input_data.get("group_id") or "",
        "categories": input_data.get("envelope_ids") or [],
    }
    return _proposed(
        change,
        {
            "op": "Reorder envelopes",
            "group_id": change["group_id"],
            "categories": change["categories"],
        },
    )


_AutoAssignMode = Literal[
    "underfunded",
    "assigned_last_month",
    "spent_last_month",
    "average_assigned",
    "average_spent",
    "cover_overspending",
    "reset_assigned",
]


class AutoAssignInput(ToolInput):
    """Schema-of-record for ``auto_assign`` (see ``schema_from_model``)."""

    month: Optional[str] = Field(
        default=None, description="Budget month YYYY-MM (default: current)"
    )
    mode: _AutoAssignMode = Field(description="Which auto-assign strategy to apply")


@tool(
    "auto_assign",
    description=(
        "Run an AUTO-ASSIGN operation for the current budget month: fill category Assigned "
        "amounts in bulk using a named mode. Nothing else to call to apply it. Ready to Assign is cash "
        "not yet assigned to an envelope. An "
        "envelope HOLDS its own overspend until month-end — Ready to Assign does not move when "
        "a category goes red, cash or card — but covering that overspend costs Ready to "
        "Assign, up to the hole's size. Modes: "
        "'underfunded' = fund all underfunded targets, then each card's payment shortfall "
        "(capped at Ready-to-Assign); "
        "'assigned_last_month' = repeat what was assigned last month; "
        "'spent_last_month' = assign what was spent last month; "
        "'average_assigned' = average assigned per category over up to 12 months since the "
        "budget began; "
        "'average_spent' = the same window's average spent per category; "
        "'cover_overspending' = tops up overspent SPENDING envelopes to zero (a card's payment "
        "envelope is funded by coverage, never a step) — this DOES reduce Ready to Assign, up "
        "to the size of each hole; run on a LATER month it ALSO writes future assignments, "
        "which further reduce this month's displayed figure; "
        "'reset_assigned' = release all assigned amounts back to RTA."
    ),
    input_schema=schema_of(AutoAssignInput, tool_name="auto_assign"),
    # A write, not a read: approving the card re-runs this and writes every assignment, and an
    # unattended caller writes straight away (_budget_confirm.proposed).
    mutates=("budget",),
    preview_adapter=NAMED_ARGS_PREVIEW,
)
async def auto_assign(input_data: dict):

    month = (input_data.get("month") or "").strip()
    if not month:
        t = user_today()
        month = f"{t.year:04d}-{t.month:02d}"
    mode = (input_data.get("mode") or "").strip()
    try:
        preview = planning_service.auto_assign_preview(month, mode)
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}), None
    change = {"type": "auto_assign", "month": month, "mode": mode}
    steps = preview.get("steps", [])
    total = preview.get("total_delta", 0.0)
    display = {
        "op": "Auto-assign",
        "mode": mode,
        "month": month,
        "steps_count": len(steps),
        "total_delta": total,
        "preview": [
            {"envelope_id": s["envelope_id"], "label": s["label"], "amount": s["new_assigned"]}
            for s in steps[:10]
        ],
    }
    return _proposed(change, display)


# ── Apply (write ONLY; call after explicit confirmation) ─────────────────────────

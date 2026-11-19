"""Financial-commitment tools.

**Batch fields (2026-07-19, "act on ONE thing should accept MANY"):** ``update_commitments``
(``updates``), ``update_commitment_actions`` (``updates``) and ``update_commitment_installments``
(``updates``) all gained an optional plural field alongside their existing singular one — each
row carries its OWN commitment/installment/action id and its OWN changes, since patching several
DIFFERENT rows with DIFFERENT edits (e.g. mark 3 different installments across 2 different
commitments paid, each with its own paid_txn_id) is the real use case, not one shared edit
broadcast to many ids.

**Arity-naming fixup (harness reliability program, 2026-07-19b, owner incident — "why is
delete_account still singular / account_id even though it's a list?"):** an EARLIER pass over
this file (see git history) left all three tool NAMES singular even though each now accepts an
array of DIFFERENT commitments/installments/actions — an entity set, per the rule "the array is
the set of entities the tool acts on, so the name must be plural too." That earlier reasoning
("English tolerates either arity here") is superseded: the owner's complaint is precisely that a
plural-accepting tool with a singular name lies about arity, the same defect fixed on
``update_instrument``/``resolve_instrument`` (``portfolio.py``) in this same pass. All three are
renamed here (`update_commitment` → `update_commitments`, `update_commitment_action` →
`update_commitment_actions`, `update_commitment_installment` → `update_commitment_installments`),
with the old names migrated onto them (a tool name is persisted data — see registry.py) —
including the literal name `tests/test_parity_backfill_tools.py` pinned, which is updated in the
same change rather than treated as a blocker.
``create_commitment``/``create_loan_repayment``/``delete_commitment``/``get_commitments`` and
the wholesale-replace tools (``set_commitment_installments``/``set_funding_plan``/
``set_commitment_actions``, each already an array argument DESCRIBING one commitment — an
attribute of one entity, not an entity set) were not named in the audit that drove this change
and stay singular/unrenamed — left untouched here to keep this diff scoped to what was asked.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.ai.tools import _bounded as _b
from backend.ai.tools._shared import no_op_write_result
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import (
    NestedInput,
    ToolInput,
    bare_object_schema,
    schema_of,
    schema_of_batch_only,
)
from backend.core.errors import ValidationError
from backend.services import commitments as _cs
from backend.services import commitments_execution


class _CommitmentItem(BaseModel):
    """Base for a per-item object nested inside a batch ``updates`` array. ``extra='forbid'``
    mirrors ``ToolInput`` so a typo'd field in ONE row is rejected like a bad top-level field."""

    model_config = ConfigDict(extra="forbid")


class GetCommitmentsInput(ToolInput):
    """Schema-of-record for ``get_commitments`` (see ``schema_from_model``)."""

    query: Optional[str] = Field(
        default=None,
        description=(
            "Case-insensitive substring over the commitment's NAME or note. The cheapest way "
            "to find one commitment."
        ),
    )
    kind: Optional[str] = Field(
        default=None, description="Optional filter: purchase|loan_repayment|auto_pay|goal"
    )
    status: Optional[str] = Field(
        default=None, description="Optional filter: active|completed|cancelled"
    )
    limit: Optional[int] = Field(
        default=None,
        description=f"Max rows to return (default {_b.DEFAULT_COMMITMENT_LIMIT}). Page further with offset.",
    )
    offset: Optional[int] = Field(
        default=None, description="Rows to skip before applying limit. Default 0."
    )


@tool(
    "get_commitments",
    description=(
        "Get the user's financial commitments — REAL future obligations, already decided and "
        "tracked in the ledger, with dated installment schedules and funding plans (property "
        "purchases, car payments, renovations, school fees, tax GIRO, loan repayments). Each "
        "has installments (label, due_date, amount in the commitment's native currency, "
        "status) and a funding_plan. Read this before discussing whether the user can afford "
        "an upcoming installment.\n\n"
        "NOT for a HYPOTHETICAL what-if on the FIRE/Outlook plan — 'move the car purchase to "
        "5 years' or 'what if we buy a house at 40' means a life event on the FIRE-plan "
        "baseline, a different thing entirely with no installments or funding plan. Use "
        "get_plan_baseline / set_plan_baseline for that instead.\n\n"
        "FILTER RATHER THAN FETCH EVERYTHING. Looking for one commitment? Pass "
        "query='condo down payment'. Only loans? kind='loan_repayment'. Only what's still "
        f"open? status='active'. An unfiltered call returns up to {_b.DEFAULT_COMMITMENT_LIMIT} "
        "rows — every response reports `showing` and `total` so you can always tell whether "
        "you're looking at a subset."
    ),
    input_schema=schema_of(GetCommitmentsInput, tool_name="get_commitments"),
)
async def get_commitments(input_data: dict):
    total = len(_cs.query_commitments())
    rows = _cs.query_commitments(kind=input_data.get("kind"), status=input_data.get("status"))
    query = (input_data.get("query") or "").strip().lower()
    if query:
        rows = [
            c
            for c in rows
            if query in (c.get("name") or "").lower() or query in (c.get("note") or "").lower()
        ]
    matched = len(rows)
    offset = max(0, int(input_data.get("offset") or 0))
    limit_raw = input_data.get("limit")
    limit = int(limit_raw) if limit_raw not in (None, "") else _b.DEFAULT_COMMITMENT_LIMIT
    page, _total_after_offset = _b.cap(rows[offset:], limit)
    page = [commitments_execution.annotate(c) for c in page]
    showing = len(page)

    out: dict = {"commitments": page, "showing": showing, "total": total}
    notes: list[str] = []
    if showing == 0:
        if total == 0:
            notes.append("The user has no commitments yet.")
        elif matched == 0:
            notes.append(
                f"0 of {total} commitments match these filters — they DO exist, your filter "
                "excluded them. Drop or loosen the filter and retry. Do NOT report that the "
                "user has no commitments."
            )
        else:
            notes.append(
                f"offset={offset} is past the end of the {matched} matching commitments — "
                "retry with a smaller offset (e.g. offset=0). Do NOT report that the user has "
                "no commitments."
            )
    elif offset + showing < matched:
        notes.append(
            f"Showing {showing} of {matched} matching commitments (total {total}) — "
            f"page with offset={offset + showing}."
        )
    elif matched < total:
        notes.append(
            f"Showing {showing} of {matched} commitments — filtered, not truncated (total {total})."
        )
    if notes:
        out["note"] = " ".join(notes)
    return json.dumps(out, ensure_ascii=False), None


class CreateCommitmentInput(ToolInput):
    """Schema-of-record for ``create_commitment`` (see ``schema_from_model``)."""

    name: str = Field(
        description=(
            "What the user is committing to, in their own words (e.g. 'Bangkok condo "
            "down-payment', 'Citi loan payback'). Shown on the commitment and used to name the "
            "Planning envelope synced to its next installment."
        )
    )
    kind: Optional[str] = Field(
        default=None, description="purchase|loan_repayment|auto_pay|goal (default purchase)"
    )
    currency: Optional[str] = Field(
        default=None, description="Native currency of the installments, e.g. 'VND', 'SGD'"
    )
    linked_position_id: Optional[str] = Field(
        default=None,
        description="Optional: the property/asset position this funds (from get_portfolio)",
    )
    linked_account_id: Optional[str] = Field(
        default=None,
        description="Optional: for loan_repayment, the liability account (from list_accounts)",
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Free-form context about the obligation (terms, counterparty, what it is for). "
            "Stored on the commitment for the user and for later reasoning; never parsed and "
            "never read by the schedule, funding, or envelope sync. Defaults to empty."
        ),
    )
    installments: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Optional inline schedule: [{label, due_date (ISO), amount (native), description}]",
        json_schema_extra=bare_object_schema,
    )
    funding_plan: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Optional funding sources (see set_funding_plan)",
        json_schema_extra=bare_object_schema,
    )


@tool(
    "create_commitment",
    description=(
        "Create a financial commitment. Use for any dated future obligation the user is "
        "planning for. kind: 'purchase' (property/car/etc), 'loan_repayment' (paying down a "
        "loan — link the liability account), 'auto_pay' (tax GIRO / standing orders — a "
        "tracker, no cash planning needed), 'goal' (open-ended savings target, no schedule). "
        "currency is the native currency of the installments. You can pass installments and "
        "funding_plan inline, or add them afterwards with set_commitment_installments / "
        "set_funding_plan. NOT for a "
        "REMINDER: someone who asks to be reminded about a bill wants a notification, not a "
        "tracked obligation in their plan — that is create_automation with a schedule "
        "trigger."
    ),
    input_schema=schema_of(CreateCommitmentInput, tool_name="create_commitment"),
    mutates=("commitments",),
)
async def create_commitment(input_data: dict):
    c = _cs.create_commitment(input_data)
    return json.dumps({"ok": True, "commitment": c}, ensure_ascii=False), None


class SetCommitmentInstallmentsInput(ToolInput):
    """Schema-of-record for ``set_commitment_installments`` (see ``schema_from_model``)."""

    commitment_id: str = Field(
        description="The commitment whose schedule to replace, by id (from get_commitments)."
    )
    installments: List[Dict[str, Any]] = Field(
        description=(
            "The COMPLETE new schedule — this REPLACES every existing installment, so any row "
            "you omit is dropped along with its paid_txn_id link. Each: {label, due_date (ISO "
            "date), amount (in the commitment's native currency), description, status "
            "(paid|due|upcoming)}. To change one row, call update_commitment_installments "
            "instead."
        ),
        json_schema_extra=bare_object_schema,
    )


@tool(
    "set_commitment_installments",
    description=(
        "Replace the ENTIRE installment schedule of a commitment. Use after you compute a "
        "schedule (e.g. from loan terms the user describes, or a developer payment plan). "
        "Each installment: {label, due_date (ISO date), amount (in the commitment's native "
        "currency), description, status (paid|due|upcoming)}. For an EXISTING schedule "
        "prefer the granular tools — update_commitment_installments to fix rows, "
        "add_commitment_installments / remove_commitment_installments to grow or trim it: "
        "a row resent here without its id gets a NEW id (breaking due-reminder dedup) and "
        "loses its paid_txn_id link unless you carry both through."
    ),
    input_schema=schema_of(SetCommitmentInstallmentsInput, tool_name="set_commitment_installments"),
    mutates=("commitments",),
)
async def set_commitment_installments(input_data: dict):
    try:
        c = _cs.set_installments(input_data["commitment_id"], input_data.get("installments", []))
    except ValidationError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps({"ok": c is not None, "commitment": c}, ensure_ascii=False), None


# Shared with the single-row top-level twins below — the nested shape shipped blank while
# the top level carried the text. One constant each so they cannot drift apart again.
_INSTALLMENT_COMMITMENT_ID_DESC = "The commitment owning this installment (from get_commitments)."
_INSTALLMENT_ID_DESC = "The installment's own id, from that commitment's installments[] list."
_INSTALLMENT_CHANGES_DESC = (
    "Fields to change on this one installment: amount, due_date, label, description, status, "
    "paid_txn_id. Only the keys you pass are touched. Prefer linking paid_txn_id to the real "
    "ledger transaction over setting status='paid' — 'paid' is anchored to that link."
)


def _find_installment(commitment: dict | None, installment_id: str) -> dict | None:
    """Existence check only — no store write. Used to tell an EMPTY patch on a real
    installment (a no-op) apart from a patch naming an installment that doesn't exist
    (the existing not-found error stays not-found, never becomes a no-op)."""
    if not commitment:
        return None
    for inst in commitment.get("installments", []):
        if inst.get("id") == installment_id:
            return inst
    return None


class UpdateInstallmentBatchItem(NestedInput):
    """Schema-of-record for an ``update_commitment_installments.updates`` batch item (see
    ``schema_from_model``)."""

    commitment_id: str = Field(description=_INSTALLMENT_COMMITMENT_ID_DESC)
    installment_id: str = Field(description=_INSTALLMENT_ID_DESC)
    changes: Dict[str, Any] = Field(
        json_schema_extra=bare_object_schema, description=_INSTALLMENT_CHANGES_DESC
    )


class UpdateCommitmentInstallmentsInput(ToolInput):
    """Schema-of-record for ``update_commitment_installments`` (see ``schema_from_model``)."""

    commitment_id: Optional[str] = Field(default=None, description="Single-row form")
    installment_id: Optional[str] = Field(
        default=None,
        description="Single-row form: the installment's id (from get_commitments)",
    )
    changes: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Single-row form: fields to change — amount, due_date, label, "
            "description, status, paid_txn_id"
        ),
        json_schema_extra=bare_object_schema,
    )
    updates: List[UpdateInstallmentBatchItem] = Field(
        default_factory=list,
        description=(
            "Batch form: [{commitment_id, installment_id, changes}] — patch several "
            "installment rows in ONE call. Omit commitment_id/installment_id/changes "
            "at the top level when using this."
        ),
    )


@tool(
    "update_commitment_installments",
    description=(
        "Patch ONE installment row (fix a wrong amount/date/label, or mark it paid). To mark "
        "an installment paid against a real payment, set status='paid' AND paid_txn_id to the "
        "ledger transaction id — only a ledger-linked installment is treated as truly paid by "
        "the funding projection (a bare status flag is display-only).\n\n"
        "MANY AT ONCE: pass `updates` — [{commitment_id, installment_id, changes}] — to patch "
        "several installment rows (across one or several commitments) in ONE call instead of "
        "one call each. Returns per-item results {index, ok, commitment} or "
        "{index, ok:false, error}."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        UpdateCommitmentInstallmentsInput,
        array="updates",
        tool_name="update_commitment_installments",
    ),
    mutates=("commitments",),
)
async def update_commitment_installments(input_data: dict):
    raw_updates = input_data.get("updates")
    if raw_updates:
        results = []
        for i, row in enumerate(raw_updates):
            cid = row.get("commitment_id") or ""
            iid = row.get("installment_id") or ""
            if not cid or not iid:
                results.append(
                    {
                        "index": i,
                        "ok": False,
                        "error": "commitment_id and installment_id are required",
                    }
                )
                continue
            changes = row.get("changes") or {}
            if not changes:
                # Structurally-empty patch — inst.update({}) would be a no-op write.
                # An installment that doesn't exist keeps the existing not-found shape;
                # only a REAL installment with nothing to change is reported as no_op.
                existing = _cs.get_commitment(cid)
                if _find_installment(existing, iid) is None:
                    results.append({"index": i, "ok": False, "commitment": None})
                    continue
                results.append({"index": i, **no_op_write_result({"commitment": existing})})
                continue
            try:
                c = _cs.update_installment(cid, iid, changes)
            except ValidationError as exc:
                results.append({"index": i, "ok": False, "error": exc.message or str(exc)})
                continue
            results.append({"index": i, "ok": c is not None, "commitment": c})
        return json.dumps(
            {
                "ok": True,
                "count": len(raw_updates),
                "updated": sum(1 for r in results if r["ok"]),
                "results": results,
            },
            ensure_ascii=False,
        ), None
    cid = input_data.get("commitment_id") or ""
    iid = input_data.get("installment_id") or ""
    if not cid or not iid:
        return json.dumps(
            {"ok": False, "error": "commitment_id and installment_id are required"}
        ), None
    changes = input_data.get("changes") or {}
    if not changes:
        existing = _cs.get_commitment(cid)
        if _find_installment(existing, iid) is None:
            return json.dumps({"ok": False, "commitment": None}), None
        return json.dumps(no_op_write_result({"commitment": existing}), ensure_ascii=False), None
    try:
        c = _cs.update_installment(cid, iid, changes)
    except ValidationError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps({"ok": c is not None, "commitment": c}, ensure_ascii=False), None


def _removal_result(c, unknown: list, blocked: list, *, cid: str, noun: str, blocked_hint: str):
    """Shared JSON shaping for the all-or-nothing remove_* tools: (commitment, unknown,
    blocked) from the store → the {"ok": ...} contract. Both failure reasons are reported
    TOGETHER when they co-occur, so one call surfaces everything to fix — never a
    fix-retry-discover round trip."""
    if unknown or blocked:
        parts = []
        if unknown:
            parts.append(
                f"unknown {noun} id(s): {', '.join(unknown)} — read the commitment's "
                f"current {noun} ids via get_commitments"
            )
        if blocked:
            parts.append(f"refused to remove {', '.join(blocked)} — {blocked_hint}")
        return {"ok": False, "error": f"nothing was removed; {'; and '.join(parts)}"}
    if c is None:
        return {"ok": False, "error": f"commitment not found: {cid}"}
    return {"ok": True, "commitment": c}


class AddCommitmentInstallmentsInput(ToolInput):
    """Schema-of-record for ``add_commitment_installments`` (see ``schema_from_model``)."""

    commitment_id: str = Field(
        description="The commitment to add installments to, by id (from get_commitments)."
    )
    installments: List[Dict[str, Any]] = Field(
        min_length=1,
        description=(
            "[{label, due_date (ISO), amount (native currency), description?, status? "
            "(paid|due|upcoming)}] — same row shape as set_commitment_installments; each new "
            "row gets a server-minted id."
        ),
        json_schema_extra=bare_object_schema,
    )


@tool(
    "add_commitment_installments",
    description=(
        "Add installments to a commitment's EXISTING schedule — the rows already there (and "
        "their ids, which key due-reminder de-duplication and paid_txn_id ledger anchors) are "
        "untouched. Prefer this over set_commitment_installments when adding a fee or extra "
        "payment to a schedule the user isn't otherwise changing."
    ),
    input_schema=schema_of(AddCommitmentInstallmentsInput, tool_name="add_commitment_installments"),
    mutates=("commitments",),
)
async def add_commitment_installments(input_data: dict):
    cid = input_data.get("commitment_id") or ""
    installments = input_data.get("installments") or []
    if not cid or not installments:
        return (
            json.dumps({"ok": False, "error": "commitment_id and installments are required"}),
            None,
        )
    try:
        c = _cs.add_installments(cid, installments)
    except ValidationError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps({"ok": c is not None, "commitment": c}, ensure_ascii=False), None


class RemoveCommitmentInstallmentsInput(ToolInput):
    """Schema-of-record for ``remove_commitment_installments`` (see ``schema_from_model``)."""

    commitment_id: str = Field(description="The commitment whose installments to remove, by id.")
    installment_ids: List[str] = Field(
        min_length=1,
        description=(
            "Ids of the installments to remove, from that commitment's installments[]. "
            "All-or-nothing: an unknown or paid id fails the whole call and nothing is removed."
        ),
    )


@tool(
    "remove_commitment_installments",
    description=(
        "Remove installments from a commitment by id, leaving the rest of the schedule "
        "untouched. All-or-nothing: an unknown id fails the call and removes nothing. A PAID "
        "installment (paid_txn_id set — it anchors the real ledger transaction that paid it) "
        "REFUSES removal; if the user truly means it, unlink first via "
        "update_commitment_installments with changes={'paid_txn_id': null}, then remove."
    ),
    input_schema=schema_of(
        RemoveCommitmentInstallmentsInput, tool_name="remove_commitment_installments"
    ),
    mutates=("commitments",),
)
async def remove_commitment_installments(input_data: dict):
    cid = input_data.get("commitment_id") or ""
    iids = input_data.get("installment_ids") or []
    if not cid or not iids:
        return (
            json.dumps({"ok": False, "error": "commitment_id and installment_ids are required"}),
            None,
        )
    c, unknown, blocked = _cs.remove_installments(cid, iids)
    out = _removal_result(
        c,
        unknown,
        blocked,
        cid=cid,
        noun="installment",
        blocked_hint=(
            "each is PAID (paid_txn_id anchors a real ledger transaction). Unlink via "
            "update_commitment_installments changes={'paid_txn_id': null} first if the user "
            "truly wants it removed"
        ),
    )
    return json.dumps(out, ensure_ascii=False), None


class SetFundingPlanInput(ToolInput):
    """Schema-of-record for ``set_funding_plan`` (see ``schema_from_model``)."""

    commitment_id: str = Field(
        description="The commitment whose funding plan to replace, by id (from get_commitments)."
    )
    funding_plan: List[Dict[str, Any]] = Field(
        description=(
            "[{type, label, amount, currency, ref_position_id?, sell_quantity?, "
            "available_date?, ref_account_id?}]. EVERY source needs a real amount+currency now, "
            "including asset_sale — see the tool description."
        ),
        json_schema_extra=bare_object_schema,
    )


@tool(
    "set_funding_plan",
    description=(
        "Replace a commitment's funding plan — the named sources that say HOW each installment "
        "gets paid. Source types: 'cash' (a savings earmark — amount+currency; money already on "
        "hand), 'income' (rely on recurring monthly surplus), 'asset_sale' (sell a holding — "
        "amount+currency is the TARGET you intend to raise (compute it from the position's "
        "current price × sell_quantity when the user gives you a share count, or ask the user "
        "for a target amount and back into sell_quantity yourself) plus ref_position_id + "
        "sell_quantity for context; the plan is FROZEN at this amount and does NOT drift with "
        "the market afterward — re-run this tool if the user wants it re-priced), 'loan' "
        "(borrow — amount+currency+available_date; consider also creating a loan_repayment "
        "commitment for the payback), 'windfall' (a one-off like a bonus — amount+currency+"
        "available_date). New-money sources (asset_sale/loan/windfall) carry an available_date "
        "(when the money lands). Prefer add_funding_sources / remove_funding_sources when the "
        "plan already has rows the user isn't changing — this tool REPLACES the whole plan, and "
        "any existing row resent without its id gets a NEW id, orphaning commitment actions "
        "that reference it via funding_id. There is no manual UI for funding."
    ),
    input_schema=schema_of(SetFundingPlanInput, tool_name="set_funding_plan"),
    mutates=("commitments",),
)
async def set_funding_plan(input_data: dict):
    c = _cs.set_funding_plan(input_data["commitment_id"], input_data.get("funding_plan", []))
    return json.dumps({"ok": c is not None, "commitment": c}, ensure_ascii=False), None


class UpdateFundingSourceInput(ToolInput):
    """Schema-of-record for ``update_funding_source`` (see ``schema_from_model``)."""

    commitment_id: str = Field(description="The commitment owning this funding source.")
    funding_id: str = Field(
        description="The source's own id, from that commitment's funding_plan[]."
    )
    changes: Dict[str, Any] = Field(
        description=(
            "Fields to change on this one source: label, amount, currency, available_date, "
            "ref_position_id, sell_quantity, ref_account_id, note, disabled, status, "
            "realized_txn_id. "
            "status='done' records that this money ACTUALLY arrived (optionally with "
            "realized_txn_id, the ledger transaction that proves it); 'planned' is the "
            "default and means it has not happened yet. There is no 'missed' value — a "
            "planned source whose available_date has passed is derived as overdue. "
            "disabled=true drops it from the funding projection and earmark reconciliation — "
            "use when the action that would have unlocked it is marked 'not happening'; "
            "disabled=false re-enables it. Type is immutable once created."
        ),
        json_schema_extra=bare_object_schema,
    )


@tool(
    "update_funding_source",
    description=(
        "Patch ONE funding source on a commitment — mark it done once the money actually "
        "arrived (status='done'), fix its amount/date, or disable it (disabled=true) when "
        "the action that would have unlocked it isn't happening. To replace the whole "
        "funding plan use set_funding_plan instead."
    ),
    input_schema=schema_of(UpdateFundingSourceInput, tool_name="update_funding_source"),
    mutates=("commitments",),
)
async def update_funding_source(input_data: dict):
    cid = input_data.get("commitment_id") or ""
    fid = input_data.get("funding_id") or ""
    if not cid or not fid:
        return json.dumps({"ok": False, "error": "commitment_id and funding_id are required"}), None
    changes = input_data.get("changes") or {}
    if not changes:
        return json.dumps({"ok": False, "error": "changes must not be empty"}), None
    c = _cs.update_funding_source(cid, fid, changes)
    return json.dumps({"ok": c is not None, "commitment": c}, ensure_ascii=False), None


class AddFundingSourcesInput(ToolInput):
    """Schema-of-record for ``add_funding_sources`` (see ``schema_from_model``)."""

    commitment_id: str = Field(
        description="The commitment to add funding sources to, by id (from get_commitments)."
    )
    sources: List[Dict[str, Any]] = Field(
        min_length=1,
        description=(
            "[{type, label, amount, currency, ref_position_id?, sell_quantity?, "
            "available_date?, ref_account_id?}] — the same source shape and type semantics "
            "as set_funding_plan; each new source gets a server-minted id."
        ),
        json_schema_extra=bare_object_schema,
    )


@tool(
    "add_funding_sources",
    description=(
        "Add funding sources to a commitment's EXISTING plan — the rows already there (and "
        "their ids, which commitment actions reference via funding_id) are untouched. Prefer "
        "this over set_funding_plan whenever the plan has rows the user isn't changing. "
        "Source types and their rules are documented on set_funding_plan."
    ),
    input_schema=schema_of(AddFundingSourcesInput, tool_name="add_funding_sources"),
    mutates=("commitments",),
)
async def add_funding_sources(input_data: dict):
    cid = input_data.get("commitment_id") or ""
    sources = input_data.get("sources") or []
    if not cid or not sources:
        return (
            json.dumps({"ok": False, "error": "commitment_id and sources are required"}),
            None,
        )
    c = _cs.add_funding_sources(cid, sources)
    return json.dumps({"ok": c is not None, "commitment": c}, ensure_ascii=False), None


class RemoveFundingSourcesInput(ToolInput):
    """Schema-of-record for ``remove_funding_sources`` (see ``schema_from_model``)."""

    commitment_id: str = Field(description="The commitment whose funding sources to remove, by id.")
    funding_ids: List[str] = Field(
        min_length=1,
        description=(
            "Ids of the sources to remove, from that commitment's funding_plan[]. "
            "All-or-nothing: an unknown id fails the whole call and nothing is removed."
        ),
    )


@tool(
    "remove_funding_sources",
    description=(
        "Remove funding sources from a commitment by id, leaving the rest of the plan "
        "untouched. All-or-nothing: an unknown id fails the call and removes nothing. If a "
        "source's unlock action is merely 'not happening', prefer disabling it "
        "(update_funding_source, disabled=true) — that is reversible and keeps the action's "
        "funding_id link intact; remove only sources entered in error."
    ),
    input_schema=schema_of(RemoveFundingSourcesInput, tool_name="remove_funding_sources"),
    mutates=("commitments",),
)
async def remove_funding_sources(input_data: dict):
    cid = input_data.get("commitment_id") or ""
    fids = input_data.get("funding_ids") or []
    if not cid or not fids:
        return (
            json.dumps({"ok": False, "error": "commitment_id and funding_ids are required"}),
            None,
        )
    c, unknown = _cs.remove_funding_sources(cid, fids)
    out = _removal_result(c, unknown, [], cid=cid, noun="funding", blocked_hint="")
    return json.dumps(out, ensure_ascii=False), None


class SetCommitmentActionsInput(ToolInput):
    """Schema-of-record for ``set_commitment_actions`` (see ``schema_from_model``)."""

    commitment_id: str = Field(
        description="The commitment whose action checklist to replace, by id (from get_commitments)."
    )
    actions: List[Dict[str, Any]] = Field(
        description=(
            "The COMPLETE new checklist — this REPLACES every existing action, so any row you "
            "omit is dropped. Each: {label, category, due_date (ISO), start_by? (ISO), amount? "
            "(native currency), currency?, status (pending|done), counterparty?, note}. These "
            "are NON-payment tasks (apply for a loan, sign a contract, a review date); "
            "payments belong in installments."
        ),
        json_schema_extra=bare_object_schema,
    )


@tool(
    "set_commitment_actions",
    description=(
        "Replace a commitment's actions/events — the dated checklist of NON-payment tasks: "
        "apply for a loan, sign a contract, confirm a counterparty transfer, verify a loan "
        "auto-debit, a stock-sale window, a plan review, handover. (Payments go in "
        "installments; this is everything else you must DO or watch for.) Each action: "
        "{label, category, due_date (ISO), start_by? (ISO), amount? (native), currency?, "
        "status (pending|done), counterparty?, note}. For an EXISTING checklist prefer the "
        "granular tools — update_commitment_actions to edit rows, add_commitment_actions / "
        "remove_commitment_actions to grow or trim it: a row resent here without its id gets "
        "a NEW id (breaking due-reminder dedup), and any funding_id / reminder link you omit "
        "from the resent row is dropped."
    ),
    input_schema=schema_of(SetCommitmentActionsInput, tool_name="set_commitment_actions"),
    mutates=("commitments",),
)
async def set_commitment_actions(input_data: dict):
    c = _cs.set_actions(input_data["commitment_id"], input_data.get("actions", []))
    return json.dumps({"ok": c is not None, "commitment": c}, ensure_ascii=False), None


# Shared with the single-row top-level twins below (see the installment constants above).
_ACTION_COMMITMENT_ID_DESC = "The commitment owning this action (from get_commitments)."
_ACTION_ID_DESC = "The action's own id, from that commitment's actions[] checklist."
_ACTION_CHANGES_DESC = (
    "Fields to change on this one action: label, category, due_date, start_by, amount, "
    "currency, status, counterparty, note, funding_id. Only the keys you pass are touched; "
    "status='done' is how an action is marked complete, status='missed' is how it's marked "
    "\"not happening\" (reversible — patch back to 'pending' to undo). funding_id ties this "
    "action to the funding_plan[] source it unlocks — when a 'missed' action has one, also "
    "disable that source with update_funding_source so the funding projection drops it too."
)


def _find_action(commitment: dict | None, action_id: str) -> dict | None:
    """Existence check only — no store write. See `_find_installment`'s docstring for why
    this stays separate from an empty-patch no_op."""
    if not commitment:
        return None
    for a in commitment.get("actions", []):
        if a.get("id") == action_id:
            return a
    return None


class UpdateActionBatchItem(NestedInput):
    """Schema-of-record for an ``update_commitment_actions.updates`` batch item (see
    ``schema_from_model``)."""

    commitment_id: str = Field(description=_ACTION_COMMITMENT_ID_DESC)
    action_id: str = Field(description=_ACTION_ID_DESC)
    changes: Dict[str, Any] = Field(
        json_schema_extra=bare_object_schema, description=_ACTION_CHANGES_DESC
    )


class UpdateCommitmentActionsInput(ToolInput):
    """Schema-of-record for ``update_commitment_actions`` (see ``schema_from_model``)."""

    commitment_id: Optional[str] = Field(default=None, description="Single-row form")
    action_id: Optional[str] = Field(default=None, description="Single-row form")
    changes: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Single-row form: fields to change — label, category, due_date, start_by, "
            "amount, currency, status, counterparty, note"
        ),
        json_schema_extra=bare_object_schema,
    )
    updates: List[UpdateActionBatchItem] = Field(
        default_factory=list,
        description=(
            "Batch form: [{commitment_id, action_id, changes}]. Omit commitment_id/"
            "action_id/changes at the top level when using this."
        ),
    )


@tool(
    "update_commitment_actions",
    description=(
        "Patch ONE action/event on a commitment — typically mark it done, or fix its "
        "date/amount.\n\n"
        "MANY AT ONCE: pass `updates` — [{commitment_id, action_id, changes}] — to patch "
        "several action rows (across one or several commitments) in ONE call instead of one "
        "call each. Returns per-item results {index, ok, commitment} or "
        "{index, ok:false, error}."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        UpdateCommitmentActionsInput, array="updates", tool_name="update_commitment_actions"
    ),
    mutates=("commitments",),
)
async def update_commitment_actions(input_data: dict):
    raw_updates = input_data.get("updates")
    if raw_updates:
        results = []
        for i, row in enumerate(raw_updates):
            cid = row.get("commitment_id") or ""
            aid = row.get("action_id") or ""
            if not cid or not aid:
                results.append(
                    {"index": i, "ok": False, "error": "commitment_id and action_id are required"}
                )
                continue
            changes = row.get("changes") or {}
            if not changes:
                # Structurally-empty patch — a.update({}) would be a no-op write. An
                # action that doesn't exist keeps the existing not-found shape; only a
                # REAL action with nothing to change is reported as no_op.
                existing = _cs.get_commitment(cid)
                if _find_action(existing, aid) is None:
                    results.append({"index": i, "ok": False, "commitment": None})
                    continue
                results.append({"index": i, **no_op_write_result({"commitment": existing})})
                continue
            c = _cs.update_action(cid, aid, changes)
            results.append({"index": i, "ok": c is not None, "commitment": c})
        return json.dumps(
            {
                "ok": True,
                "count": len(raw_updates),
                "updated": sum(1 for r in results if r["ok"]),
                "results": results,
            },
            ensure_ascii=False,
        ), None
    cid = input_data.get("commitment_id") or ""
    aid = input_data.get("action_id") or ""
    if not cid or not aid:
        return json.dumps({"ok": False, "error": "commitment_id and action_id are required"}), None
    changes = input_data.get("changes") or {}
    if not changes:
        existing = _cs.get_commitment(cid)
        if _find_action(existing, aid) is None:
            return json.dumps({"ok": False, "commitment": None}), None
        return json.dumps(no_op_write_result({"commitment": existing}), ensure_ascii=False), None
    c = _cs.update_action(cid, aid, changes)
    return json.dumps({"ok": c is not None, "commitment": c}, ensure_ascii=False), None


class AddCommitmentActionsInput(ToolInput):
    """Schema-of-record for ``add_commitment_actions`` (see ``schema_from_model``)."""

    commitment_id: str = Field(
        description="The commitment to add actions to, by id (from get_commitments)."
    )
    actions: List[Dict[str, Any]] = Field(
        min_length=1,
        description=(
            "[{label, category?, due_date (ISO), start_by? (ISO), amount? (native currency), "
            "currency?, status? (pending|done), counterparty?, note?, funding_id?}] — same "
            "row shape as set_commitment_actions; each new action gets a server-minted id."
        ),
        json_schema_extra=bare_object_schema,
    )


@tool(
    "add_commitment_actions",
    description=(
        "Add actions to a commitment's EXISTING checklist — the rows already there (and "
        "their ids, which key due-reminder de-duplication) are untouched. Prefer this over "
        "set_commitment_actions when adding a task to a checklist the user isn't otherwise "
        "changing. Actions are NON-payment tasks (apply for a loan, sign a contract, a "
        "sale window); payments belong in installments."
    ),
    input_schema=schema_of(AddCommitmentActionsInput, tool_name="add_commitment_actions"),
    mutates=("commitments",),
)
async def add_commitment_actions(input_data: dict):
    cid = input_data.get("commitment_id") or ""
    actions = input_data.get("actions") or []
    if not cid or not actions:
        return (
            json.dumps({"ok": False, "error": "commitment_id and actions are required"}),
            None,
        )
    c = _cs.add_actions(cid, actions)
    return json.dumps({"ok": c is not None, "commitment": c}, ensure_ascii=False), None


class RemoveCommitmentActionsInput(ToolInput):
    """Schema-of-record for ``remove_commitment_actions`` (see ``schema_from_model``)."""

    commitment_id: str = Field(description="The commitment whose actions to remove, by id.")
    action_ids: List[str] = Field(
        min_length=1,
        description=(
            "Ids of the actions to remove, from that commitment's actions[]. All-or-nothing: "
            "an unknown id, or one with a live reminder Automation, fails the whole call and "
            "nothing is removed."
        ),
    )


@tool(
    "remove_commitment_actions",
    description=(
        "Remove actions from a commitment by id, leaving the rest of the checklist "
        "untouched. All-or-nothing: an unknown id fails the call and removes nothing. An "
        "action with a LIVE reminder Automation (reminder_automation_id set) REFUSES removal "
        "— the reminder would keep firing about a row that no longer exists; first delete "
        "the Automation (delete_automations) and clear the link via update_commitment_actions "
        "with changes={'reminder_automation_id': null}, then remove. An action that is merely "
        "'not happening' should be marked status='missed' (reversible), not removed."
    ),
    input_schema=schema_of(RemoveCommitmentActionsInput, tool_name="remove_commitment_actions"),
    mutates=("commitments",),
)
async def remove_commitment_actions(input_data: dict):
    cid = input_data.get("commitment_id") or ""
    aids = input_data.get("action_ids") or []
    if not cid or not aids:
        return (
            json.dumps({"ok": False, "error": "commitment_id and action_ids are required"}),
            None,
        )
    c, unknown, blocked = _cs.remove_actions(cid, aids)
    out = _removal_result(
        c,
        unknown,
        blocked,
        cid=cid,
        noun="action",
        blocked_hint=(
            "each has a live reminder Automation (reminder_automation_id). Delete the "
            "Automation (delete_automations) and clear the link via update_commitment_actions "
            "changes={'reminder_automation_id': null} first"
        ),
    )
    return json.dumps(out, ensure_ascii=False), None


class CreateLoanRepaymentInput(ToolInput):
    """Schema-of-record for ``create_loan_repayment`` (see ``schema_from_model``)."""

    name: str = Field(description="e.g. 'Bank X FlexiLoan repayment'")
    principal: float = Field(
        description=(
            "Amount BORROWED, positive, in the loan's `currency` — the starting balance the "
            "schedule amortizes down to zero, not the monthly payment and not a total-with-"
            "interest figure (interest is derived from annual_rate_pct)."
        )
    )
    annual_rate_pct: float = Field(
        description="Annual interest rate %, e.g. 6.5 (EIR). 0 for interest-free."
    )
    months: int = Field(description="Term in months, e.g. 24")
    start_date: str = Field(description="First payment date (ISO), e.g. '2026-10-15'")
    currency: Optional[str] = Field(default=None, description="Loan currency, e.g. 'SGD'")
    linked_account_id: Optional[str] = Field(
        default=None, description="Optional liability account (from list_accounts)"
    )


@tool(
    "create_loan_repayment",
    description=(
        "Model a loan's payback as a 'loan_repayment' commitment with an amortized schedule. "
        "Use when the user takes or plans a loan (e.g. a personal loan funding a property) so "
        "the repayments show up in their cash flow. Computes a standard fixed-payment schedule "
        "from principal / annual rate / term — deterministic, so don't compute the installments "
        "yourself. The matching 'loan' funding source on the funded commitment is the inflow; "
        "this is the outflow side."
    ),
    input_schema=schema_of(CreateLoanRepaymentInput, tool_name="create_loan_repayment"),
    mutates=("commitments",),
)
async def create_loan_repayment(input_data: dict):
    principal = float(input_data["principal"])
    apr = float(input_data["annual_rate_pct"])
    months = int(input_data["months"])
    sched, pmt = _cs.amortization_schedule(principal, apr, months, input_data["start_date"])
    c = _cs.create_commitment(
        {
            "name": input_data["name"],
            "kind": "loan_repayment",
            "currency": input_data.get("currency", ""),
            "linked_account_id": input_data.get("linked_account_id"),
            "installments": sched,
            "loan": {
                "principal": principal,
                "apr": apr,
                "term_months": months,
                "payment": pmt,
            },
        }
    )
    return json.dumps(
        {"ok": True, "monthly_payment": pmt, "commitment": c}, ensure_ascii=False
    ), None


_COMMITMENT_EDITABLE_FIELDS = (
    "name",
    "kind",
    "currency",
    "status",
    "linked_position_id",
    "linked_account_id",
    "note",
)


# Shared between `update_commitments`' single-commitment top-level fields and the
# per-commitment `updates[]` twins. Verified against services/commitments.py: every
# projection, funding and envelope-sync path filters on status="active".
_COMMITMENT_ID_DESC = "The commitment to patch (from get_commitments)."
_COMMITMENT_NAME_DESC = (
    "New display name for the commitment (e.g. 'Condo down payment'). Cosmetic — it does "
    "not re-link the synced Planning envelope. Omit to leave unchanged."
)
_COMMITMENT_STATUS_DESC = (
    "Lifecycle state. 'active' is the ONLY status the funding engine, cash projections and "
    "envelope sync consider — flipping to 'completed' or 'cancelled' drops the commitment "
    "out of all of them and retires its synced envelope. Neither deletes it. Omit to leave "
    "unchanged."
)
_COMMITMENT_LINKED_POSITION_DESC = (
    "The property/asset position this commitment funds (from get_portfolio). Reference "
    "link only — it does not move money or change the position. Omit to leave unchanged."
)
_COMMITMENT_LINKED_ACCOUNT_DESC = (
    "For loan_repayment, the liability account being paid down (from list_accounts). "
    "Reference link only — installments are anchored to real transactions via "
    "paid_txn_id, not derived from this. Omit to leave unchanged."
)
_COMMITMENT_NOTE_DESC = (
    "Free-text note about the commitment. REPLACES the existing note wholesale rather "
    "than appending. Omit to leave unchanged."
)


class CommitmentUpdateItem(_CommitmentItem):
    """One row of a batch ``update_commitments`` call — a per-commitment id + its OWN changes,
    for when different commitments need different edits in the same call."""

    commitment_id: str = Field(description=_COMMITMENT_ID_DESC)
    name: Optional[str] = Field(default=None, description=_COMMITMENT_NAME_DESC)
    kind: Optional[str] = Field(default=None, description="purchase|loan_repayment|auto_pay|goal")
    currency: Optional[str] = Field(default=None, description="Native currency, e.g. 'SGD', 'VND'")
    status: Optional[Literal["active", "completed", "cancelled"]] = Field(
        default=None, description=_COMMITMENT_STATUS_DESC
    )
    linked_position_id: Optional[str] = Field(
        default=None, description=_COMMITMENT_LINKED_POSITION_DESC
    )
    linked_account_id: Optional[str] = Field(
        default=None, description=_COMMITMENT_LINKED_ACCOUNT_DESC
    )
    note: Optional[str] = Field(default=None, description=_COMMITMENT_NOTE_DESC)


class UpdateCommitmentInput(ToolInput):
    """Schema-of-record for ``update_commitments`` (see ``schema_from_model``)."""

    commitment_id: Optional[str] = Field(default=None, description="Single-commitment form")
    name: Optional[str] = Field(default=None, description=_COMMITMENT_NAME_DESC)
    kind: Optional[str] = Field(default=None, description="purchase|loan_repayment|auto_pay|goal")
    currency: Optional[str] = Field(default=None, description="Native currency, e.g. 'SGD', 'VND'")
    status: Optional[Literal["active", "completed", "cancelled"]] = Field(
        default=None, description=_COMMITMENT_STATUS_DESC
    )
    linked_position_id: Optional[str] = Field(
        default=None, description=_COMMITMENT_LINKED_POSITION_DESC
    )
    linked_account_id: Optional[str] = Field(
        default=None, description=_COMMITMENT_LINKED_ACCOUNT_DESC
    )
    note: Optional[str] = Field(default=None, description=_COMMITMENT_NOTE_DESC)
    updates: Optional[List[CommitmentUpdateItem]] = Field(
        default=None,
        description=(
            "Batch form: patch several DIFFERENT commitments' top-level fields in ONE call — "
            "[{commitment_id, ...fields}]. Omit commitment_id at the top level when using this."
        ),
    )


def _commitment_changes_from(row: dict) -> dict:
    return {k: v for k, v in row.items() if k in _COMMITMENT_EDITABLE_FIELDS}


@tool(
    "update_commitments",
    description=(
        "Patch a commitment's TOP-LEVEL fields — rename it, change its currency or kind, "
        "re-link the funded position/liability account, mark the whole commitment "
        "completed/cancelled (status), or edit its note. Pass only the fields to change. This "
        "does NOT touch installments/funding/actions — use the dedicated set_* tools for those. "
        "Call get_commitments first to get the id.\n\n"
        "MANY AT ONCE: pass `updates` — [{commitment_id, ...fields}] — to patch several "
        "DIFFERENT commitments in ONE call instead of one call each. Returns per-item results "
        "{index, ok, commitment} or {index, ok:false, error}."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        UpdateCommitmentInput, array="updates", tool_name="update_commitments"
    ),
    mutates=("commitments",),
)
async def update_commitments(input_data: dict):
    raw_updates = input_data.get("updates")
    if raw_updates:
        results = []
        for i, row in enumerate(raw_updates):
            cid = row.get("commitment_id") or ""
            existing = _cs.get_commitment(cid) if cid else None
            if not cid or existing is None:
                results.append({"index": i, "ok": False, "error": "commitment not found"})
                continue
            changes = _commitment_changes_from(row)
            if not changes:
                results.append({"index": i, **no_op_write_result({"commitment": existing})})
                continue
            c = _cs.update_commitment(cid, changes)
            results.append({"index": i, "ok": c is not None, "commitment": c})
        return json.dumps(
            {
                "ok": True,
                "count": len(raw_updates),
                "updated": sum(1 for r in results if r["ok"]),
                "results": results,
            },
            ensure_ascii=False,
        ), None
    cid = input_data.get("commitment_id") or ""
    if not cid:
        return json.dumps({"ok": False, "error": "commitment_id is required"}), None
    existing = _cs.get_commitment(cid)
    if existing is None:
        return json.dumps({"ok": False, "error": "commitment not found"}), None
    changes = _commitment_changes_from(input_data)
    if not changes:
        return json.dumps(no_op_write_result({"commitment": existing}), ensure_ascii=False), None
    c = _cs.update_commitment(cid, changes)
    return json.dumps({"ok": c is not None, "commitment": c}, ensure_ascii=False), None


class DeleteCommitmentInput(ToolInput):
    """Schema-of-record for ``delete_commitment`` (see ``schema_from_model``)."""

    commitment_id: str = Field(
        description=(
            "The commitment to delete, by id (from get_commitments). Removes it outright "
            "along with its whole installment schedule and funding plan; its synced Planning "
            "envelope is retired on the next sync."
        )
    )


@tool(
    "delete_commitment",
    description="Delete a financial commitment. This removes its whole schedule and funding plan.",
    input_schema=schema_of(DeleteCommitmentInput, tool_name="delete_commitment"),
    mutates=("commitments",),
)
async def delete_commitment(input_data: dict):
    ok = _cs.delete_commitment(input_data["commitment_id"])
    return json.dumps({"ok": ok}), None

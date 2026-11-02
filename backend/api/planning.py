"""
REST API for Planning (YNAB envelope budgeting) (PLAN).

Thin routes over ``services.planning`` — ALL math + the single ``apply_budget_change`` write
choke point live in the service. Every monetary write funnels through ``apply_budget_change``
(checkpoint-covered), so the REST + AI + (future) UI paths converge on one entrypoint.

    GET    /api/planning/month?ym=YYYY-MM        the full budget table (RTA + groups + totals)
    GET    /api/planning/health?ym=YYYY-MM       budget-health summary + book-wide Age of Money
    GET    /api/planning/budget-config           {rollover_start, budget_start, pre_budget_baseline}
    PUT    /api/planning/budget-config           move/clear the carry floor ONLY (Resume history)
    GET    /api/planning/fresh-start-preview?ym= what starting fresh would do (writes nothing)
    POST   /api/planning/fresh-start             clear the month's assignments + move the floor
    POST   /api/planning/fresh-start/undo        put both back from the returned snapshot
    POST   /api/planning/assign                  set Assigned for one (month, envelope)
    POST   /api/planning/move                    move money between two envelopes in a month
    POST   /api/planning/apply                   generic apply_budget_change (any change dict)
    PUT    /api/planning/targets/{envelope_id}   set/clear a budget row's target
    GET    /api/planning/groups                  list envelope groups (ordered)
    POST   /api/planning/groups                  create a group
    PATCH  /api/planning/groups/{id}             rename / collapse a group
    DELETE /api/planning/groups/{id}             delete a group (its envelopes → Others)
    POST   /api/planning/groups/reorder          set the full group order
    POST   /api/planning/groups/move-category    move an envelope into a group (optional index)
    POST   /api/planning/groups/{id}/reorder-categories   reorder an envelope group's rows
    GET    /api/planning/envelopes                list envelopes (id, name, category_ids, ...)
    POST   /api/planning/envelopes                create a standalone envelope (no categories)
    GET    /api/planning/envelopes/archived       list envelopes archived before hard-delete shipped
    POST   /api/planning/envelopes/merge          fold one envelope into another (hard-deletes source)
    POST   /api/planning/envelopes/{id}/split     unlink one category — envelope-less, no mint
    POST   /api/planning/envelopes/{id}/add-category   link an unlinked category to an envelope
    PATCH  /api/planning/envelopes/{id}           rename and/or recolor an envelope directly
    GET    /api/planning/envelopes/{id}/delete-preview?ym=   what deleting it would do to RTA (writes nothing)
    DELETE /api/planning/envelopes/{id}?ym=       delete an envelope FOR REAL — returns {undo_token, delta_rta, ...}
    POST   /api/planning/envelopes/undo-delete    put a delete back from its returned undo_token

    Pluggable budget engines (.claude/rfcs/2026-08-22-pluggable-budget-engines.md) — a
    second surface alongside the native envelope routes above, never replacing them:
    GET  /api/planning/engines                          descriptors + home + configured
    GET  /api/planning/view?engine=&ym=                  one engine's ViewModel (default: home)
    GET  /api/planning/engines/{engine_id}/draft?ym=      any engine's draft() proposal (applies
                                                            nothing); proportional/draft is a thin
                                                            alias kept for wire compatibility
                                                            (v1, kept for wire compatibility)
    GET  /api/planning/proportional/month?ym=             the 50/30/20 screen's own contract —
                                                            envelope rows with group/cap/activity/
                                                            history, income, unassigned spend
                                                            (2026-09-25 envelope-based redesign)
    POST /api/planning/home-engine                       switch the book's home lens (Phase 2)
    POST /api/planning/verb                               apply a declared verb {engine_id,
                                                            verb_id, params} on any engine (Phase 3)
    POST /api/planning/budget-lens-intro/dismiss          persist that this book has seen the
                                                            Plan -> Budget first-run introduction

A missing/empty month or empty book returns zeros, never a 500.
"""

from __future__ import annotations

import dataclasses
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.core.user_time import user_today
from backend.models.budget import (
    ApplyVerbBody,
    AssignBody,
    AutoAssignPreviewBody,
    EnvelopeAddCategoryBody,
    EnvelopeCreateBody,
    EnvelopeDeleteUndoBody,
    EnvelopeMergeBody,
    EnvelopeRenameBody,
    EnvelopeSplitBody,
    FreshStartBody,
    FreshStartUndoBody,
    GroupCreateBody,
    GroupRenameBody,
    GroupReorderBody,
    HomeEngineBody,
    MoveCategoryBody,
    MoveMoneyBody,
    NoteBody,
    ReorderCategoriesBody,
    RolloverStartBody,
    TargetBody,
)
from backend.services import envelopes as envelopes_service
from backend.services import planning as planning_service
from backend.services.budget_engines import service as budget_engines_service
from backend.services.labels import label_txns

router = APIRouter()


def _apply(change: dict) -> dict:
    """Run a change through the service choke point, mapping typed errors to HTTP codes."""
    try:
        return planning_service.apply_budget_change(change)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


def _this_month() -> str:

    t = user_today()
    return f"{t.year:04d}-{t.month:02d}"


def _call_engine_service(fn, *args):
    """Call a budget-engines service function (view/draft/config), mapping typed errors
    to HTTP codes — the ``_apply``/``get_month`` twin for the pluggable-engines surface."""
    try:
        return fn(*args)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


# ── Read ─────────────────────────────────────────────────────────────────────────


@router.get("/api/planning/engines")
def list_budget_engines():
    """Every registered budget-paradigm engine's descriptor, plus which is home and
    which is configured (RFC 2026-08-22-pluggable-budget-engines.md, Decision B/E)."""
    return budget_engines_service.list_engines()


@router.get("/api/planning/view")
def get_budget_view(engine: Optional[str] = None, ym: Optional[str] = None):
    """The paradigm-neutral ``ViewModel`` for ``engine`` (default: the book's home lens)
    + ``ym`` (default: current month) — Decision C. No caller-chosen ``fields``/
    ``include`` param; the shape is server-defined in full."""
    view = _call_engine_service(budget_engines_service.get_view, engine, ym or _this_month())
    return dataclasses.asdict(view)


def _draft_result(engine_id: str, ym: Optional[str]) -> dict:
    proposal = _call_engine_service(
        budget_engines_service.draft_engine, engine_id, ym or _this_month()
    )
    return {
        "engine_id": engine_id,
        "has_proposal": proposal is not None,
        "proposed_state": proposal,
    }


@router.get("/api/planning/engines/proportional/draft")
def draft_proportional_engine(ym: Optional[str] = None):
    """The 50/30/20 ``draft()`` proposal for ``ym`` (default: current month) — a preview
    only, applies and persists nothing. Kept for wire compatibility (existing tests + a
    live caller): a thin alias over the generic ``draft_engine`` route below — registered
    FIRST, so this literal path still wins over that route's ``{engine_id}`` pattern for
    exactly this one URL (Starlette matches routes in registration order, not by
    specificity)."""
    return _draft_result("proportional", ym)


@router.get("/api/planning/engines/{engine_id}/draft")
def draft_engine(engine_id: str, ym: Optional[str] = None):
    """Any engine's own ``draft()`` proposal for ``ym`` (default: current month) — a
    preview only, applies and persists nothing. 404s naming the real engines
    (``services.budget_engines.base.get_engine``) for an unknown ``engine_id``. An engine
    with nothing to propose (``zero_based`` always; any other engine that's already fully
    configured with no re-draft story) returns ``has_proposal: false`` with a
    ``proposed_state: null`` — never an error."""
    return _draft_result(engine_id, ym)


@router.get("/api/planning/proportional/month")
def get_proportional_month(ym: Optional[str] = None):
    """The 50/30/20 screen's own contract (2026-09-25 envelope-based redesign) for ``ym``
    (default: current month): the groups (the book's Zero-Based groups, each with its target,
    Excluded flag and spend), every row envelope's group/cap/activity/pace history, savings, the
    month's income (amount + source), the income-category picker, and unassigned spend.
    Shares its ONE computation with ``GET /api/planning/view?engine=proportional`` (Lucius's
    surface) via ``services.budget_engines.service.proportional_month`` —
    ``proportional_fold.compute_month`` — so the two can never disagree."""
    return _call_engine_service(budget_engines_service.proportional_month, ym or _this_month())


# ── Write (pluggable engines, Phase 2/3) ────────────────────────────────────────────


@router.post("/api/planning/home-engine")
def set_home_budget_engine(body: HomeEngineBody):
    """Switch the book's home budget-engine lens (Decision A/E). 404s naming the real
    engines for an unknown ``engine_id`` — never a silent switch onto a lens that
    resolves nothing. Never touches any OTHER engine's own state; returns the same shape
    ``GET /api/planning/engines`` does, with fresh ``is_home`` flags."""
    return _call_engine_service(budget_engines_service.set_home_engine, body.engine_id)


@router.post("/api/planning/verb")
def apply_budget_verb(body: ApplyVerbBody):
    """Execute a declared write verb on any budget engine — ``{engine_id, verb_id,
    params}`` (Decision F). ``zero_based``'s verbs route through the existing
    ``apply_budget_change`` choke point; every other engine's verb persists via
    ``BudgetEngineStateStore``. No new write choke is created. 404s naming the real
    engines/verbs for an unknown ``engine_id``/``verb_id``."""
    return _call_engine_service(
        budget_engines_service.apply_verb, body.engine_id, body.verb_id, body.params
    )


@router.post("/api/planning/budget-lens-intro/dismiss")
def dismiss_budget_lens_intro():
    """Persist that this book has seen the Plan -> Budget first-run introduction
    (``BudgetLensIntroStore``) — it never reappears once dismissed. Idempotent."""
    return budget_engines_service.dismiss_intro()


@router.get("/api/planning/month")
def get_month(ym: Optional[str] = None):
    """The full budget table for ``ym`` (default: the current month)."""
    month = ym or _this_month()
    try:
        return planning_service.budget_month(month)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/planning/budget-config")
def get_budget_config():
    """``{rollover_start, budget_start, pre_budget_baseline}`` — the stored budget config.
    ``rollover_start`` is the carry-walk floor ("start fresh from this month")."""
    return planning_service.budget_config()


@router.put("/api/planning/budget-config")
def put_budget_config(body: RolloverStartBody):
    """Move the carry-walk floor to ``rollover_start`` (YYYY-MM), or ``null`` to resume
    carrying all history. Nothing else is settable here — ``pre_budget_baseline`` is a cutover
    capture. Returns the resulting config; 422s a non-YYYY-MM month.

    The FLOOR ONLY. "Resume earlier history" is this route; a fresh start is
    ``POST /api/planning/fresh-start``, because it also clears the month's assignments and so
    needs a snapshot to be undoable."""
    try:
        return planning_service.set_rollover_start(body.rollover_start)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/planning/fresh-start")
def post_fresh_start(body: FreshStartBody):
    """Start fresh from ``ym``: clear that month's assignments AND move the carry floor to it,
    as one transaction. Returns ``{token, month}`` — a HANDLE to the before-image, which never
    leaves the server. 422s a non-YYYY-MM or future month, having written nothing."""
    try:
        return planning_service.fresh_start(body.ym)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/planning/fresh-start/undo", status_code=204)
def post_fresh_start_undo(body: FreshStartUndoBody):
    """Put a fresh start back — the floor AND every cleared assignment — replaying the
    SERVER-HELD before-image named by ``token``, in one transaction. 204, no body.

    The request carries a token and nothing else. This is the one path that relaxes
    ``_op_assign``'s negative-amount guard, so the amounts it replays have to be ones the server
    itself wrote; anything else in the body is ignored (``ONBaseModel`` is ``extra="ignore"``)
    and can never reach the write. One-shot: undoing twice, or with a token that isn't the most
    recent fresh start, is a 409 that writes nothing."""
    try:
        planning_service.undo_fresh_start(body.token)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return Response(status_code=204)


@router.get("/api/planning/fresh-start-preview")
def get_fresh_start_preview(ym: Optional[str] = None):
    """What "start fresh from ``ym``" would do, WITHOUT writing. The after-figure is the real
    RTA fold run at the hypothetical floor with that month's assignments treated as zero (both
    halves of the write); the rest is the ledger the confirm dialog renders under it, which sums
    exactly — the SAME identity the RTA popover renders, no residual term:

        ready_to_assign_after = cash_now + card_credit − baseline − held_in_envelopes
                                 − held_outside_envelopes − card_spend_not_covered
                                 − assigned_in_future

    ``assigned_in_future`` is present only when a later month's own assignments clamp the
    displayed figure (``_display_rta``) — projected straight through when the service includes
    it, omitted otherwise, so the dialog can render its optional row."""
    month = ym or _this_month()
    try:
        preview = planning_service.fresh_start_preview(month)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    fields = [
        "month",
        "ready_to_assign_after",
        "cash_now",
        "card_credit",
        "held_in_envelopes",
        "held_outside_envelopes",
        "card_spend_not_covered",
        "baseline",
    ]
    if "assigned_in_future" in preview:
        fields.append("assigned_in_future")
    return {k: preview[k] for k in fields}


@router.get("/api/planning/income")
def get_income(ym: Optional[str] = None):
    """Income-view for ym (default: current month): the income-kind categories as a flat
    list (income belongs to no group), with this-month received + YTD per category and total.
    All amounts are base-currency-correct (recomputed from native legs). Empty book → zeros."""
    month = ym or _this_month()
    try:
        return planning_service.income_view(month)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/planning/health")
def get_health(ym: Optional[str] = None):
    """The Planning health strip for ``ym``: RTA status, underfunded/overspent counts,
    total available/assigned, plus the book-wide Age of Money. Empty/young book → zeros +
    ``age_of_money.forecastable: False`` (never a fabricated age), never a 500."""
    month = ym or _this_month()
    try:
        return planning_service.planning_health(month)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/planning/activity-transactions")
def get_activity_transactions(
    category: Annotated[List[str], Query()] = [],  # noqa: B006 — FastAPI Query default, never mutated
    ym: Optional[str] = None,
    kind: Optional[str] = None,
):
    """The Activity popover's data source: transactions that actually moved ``category``'s
    envelope Activity for ``ym`` (default: current month) — see
    ``planning_service.activity_transactions`` for the eligibility rules this mirrors."""
    month = ym or _this_month()
    try:
        out = planning_service.activity_transactions(category, month, kind=kind)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    # Rows arrive with their payee/account names — the popover never looks ids up.
    out["transactions"] = label_txns(out.get("transactions") or [])
    return out


@router.get("/api/planning/groups")
def list_groups():
    return planning_service.list_groups()


@router.get("/api/planning/envelopes")
def list_envelopes(include_archived: bool = False):
    """Every envelope (id, name, category_ids, group_id, order, archived, color) — target and
    note are stored separately, keyed by this id (``GET /planning/month`` echoes the resolved
    target on each row; ``GET /planning/notes`` returns the note map). Archived (deleted/
    merged-away) envelopes are excluded by default — pass ``include_archived=true`` to see
    them, or use ``GET /envelopes/archived`` for the restore-footer shape (archive metadata +
    resolved former-member labels)."""
    rows = envelopes_service.all_envelopes()
    if not include_archived:
        rows = [e for e in rows if not e.get("archived")]
    return rows


@router.get("/api/planning/envelopes/archived")
def list_archived_envelopes():
    """Envelopes archived BEFORE hard-delete shipped (ADR
    2026-09-26-deleting-an-envelope-deletes-it) — a historical, permanently-frozen list; every
    delete/merge/etc. since then hard-deletes instead, so nothing new ever lands here.
    ``{id, name, color, archived_at, archive_reason, merged_into, merged_into_name,
    former_category_ids, former_members:[{key,label}]}``."""
    return envelopes_service.list_archived_envelopes()


@router.post("/api/planning/envelopes", status_code=201)
def create_envelope(body: EnvelopeCreateBody):
    """Create a standalone envelope — NO category attached (RFC
    envelope-category-full-split.md §Backend 1). Placed last in ``group_id``'s display order
    (Others when omitted/unknown). ``color`` (handoff delta #5) is optional, hex-validated."""
    result = _apply(
        {
            "type": "envelope_create",
            "name": body.name,
            "group_id": body.group_id,
            "color": body.color,
        }
    )
    return result["envelope"]


# ── Money writes (through apply_budget_change) ───────────────────────────────────


@router.post("/api/planning/assign")
def assign(body: AssignBody):
    return _apply(
        {
            "type": "assign",
            "month": body.month,
            "envelope_id": body.envelope_id,
            "amount": body.amount,
        }
    )


@router.post("/api/planning/move")
def move(body: MoveMoneyBody):
    return _apply(
        {
            "type": "move_money",
            "month": body.month,
            "from_envelope_id": body.from_envelope_id,
            "to_envelope_id": body.to_envelope_id,
            "amount": body.amount,
        }
    )


@router.put("/api/planning/targets/{envelope_id}")
def set_target(envelope_id: str, body: TargetBody):
    return _apply(
        {
            "type": "set_target",
            "envelope_id": envelope_id,
            "target_type": body.type,
            "amount": body.amount,
            "date": body.date,
            "due_weekday": body.due_weekday,
            "due_day": body.due_day,
            "snooze_until": body.snooze_until,
            "needs_whole_amount": body.needs_whole_amount,
        }
    )


# ── Envelope-group CRUD (config writes; also through apply_budget_change) ─────────


@router.post("/api/planning/groups")
def create_group(body: GroupCreateBody):
    return _apply({"type": "group_create", "name": body.name})


@router.patch("/api/planning/groups/{group_id}")
def rename_group(group_id: str, body: GroupRenameBody):
    return _apply(
        {
            "type": "group_rename",
            "group_id": group_id,
            "name": body.name,
            "collapsed": body.collapsed,
        }
    )


@router.delete("/api/planning/groups/{group_id}")
def delete_group(group_id: str):
    return _apply({"type": "group_delete", "group_id": group_id})


@router.post("/api/planning/groups/reorder")
def reorder_groups(body: GroupReorderBody):
    return _apply({"type": "group_reorder", "order": body.order})


@router.post("/api/planning/groups/move-category")
def move_category(body: MoveCategoryBody):
    return _apply(
        {
            "type": "move_category",
            "category": body.category,
            "group_id": body.group_id,
            "index": body.index,
        }
    )


@router.post("/api/planning/groups/{group_id}/reorder-categories")
def reorder_categories(group_id: str, body: ReorderCategoriesBody):
    return _apply(
        {"type": "reorder_categories", "group_id": group_id, "categories": body.categories}
    )


@router.post("/api/planning/envelopes/merge")
def merge_envelopes(body: EnvelopeMergeBody):
    return _apply(
        {
            "type": "envelope_merge",
            "source_envelope_id": body.source_envelope_id,
            "target_envelope_id": body.target_envelope_id,
        }
    )


@router.post("/api/planning/envelopes/{envelope_id}/split")
def split_envelope(envelope_id: str, body: EnvelopeSplitBody):
    return _apply(
        {
            "type": "envelope_split_category",
            "envelope_id": envelope_id,
            "category_id": body.category_id,
        }
    )


@router.post("/api/planning/envelopes/{envelope_id}/add-category")
def add_category_to_envelope(envelope_id: str, body: EnvelopeAddCategoryBody):
    """Link an UNLINKED category (one with no envelope) to ``envelope_id`` (contract
    addendum, RFC envelope-category-full-split.md) — not composable from split+merge, since
    an unlinked category has no source envelope to split FROM. 409 if the category already
    belongs to some envelope."""
    return _apply(
        {
            "type": "envelope_add_category",
            "envelope_id": envelope_id,
            "category_id": body.category_id,
        }
    )


@router.patch("/api/planning/envelopes/{envelope_id}")
def rename_envelope(envelope_id: str, body: EnvelopeRenameBody):
    """Rename and/or recolor an envelope (handoff delta #5 widens this from name-only to
    {name?, color?}) — at least one field is required."""
    return _apply(
        {
            "type": "envelope_rename",
            "envelope_id": envelope_id,
            "name": body.name,
            "color": body.color,
        }
    )


@router.get("/api/planning/envelopes/{envelope_id}/delete-preview")
def get_envelope_delete_preview(envelope_id: str, ym: Optional[str] = None):
    """What deleting ``envelope_id`` would do to Ready to Assign for ``ym`` (default: current
    month) — computed, never written; the exact figure the DELETE route itself will realize.
    See ``planning_service.envelope_delete_preview`` for the derivation. Returns
    ``{month, envelope_id, available, delta_rta}``. 404/422 on an unknown/non-deletable
    envelope — the same refusal the delete itself would give."""
    month = ym or _this_month()
    try:
        return planning_service.envelope_delete_preview(envelope_id, month)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.delete("/api/planning/envelopes/{envelope_id}")
def delete_envelope(envelope_id: str, ym: Optional[str] = None):
    """Delete an envelope FOR REAL (ADR 2026-09-26-deleting-an-envelope-deletes-it): detach all
    members (→ Unbudgeted, never deleted), release its reserve to Ready-to-Assign, hard-delete
    the row. Returns ``{ok, undo_token, former_category_ids, zeroed_assignments, month,
    available, delta_rta}`` — the before-image lives server-side under ``undo_token``; replay it
    with ``POST /api/planning/envelopes/undo-delete``. ``available``/``delta_rta`` are the
    REALIZED figures for ``ym`` (default: current month) — the same number
    ``GET .../delete-preview`` showed a moment earlier, read an instant before the write.
    Refuses an already-archived (grandfathered pre-hard-delete) or Commitments/card-payment
    -synced envelope."""
    month = ym or _this_month()
    return _apply({"type": "envelope_delete", "envelope_id": envelope_id, "month": month})


@router.post("/api/planning/envelopes/undo-delete", status_code=204)
def post_undo_delete_envelope(body: EnvelopeDeleteUndoBody):
    """Put a delete back — the envelope row, its target/note/50-30-20 cap, and every cleared
    assignment — replaying the SERVER-HELD before-image named by ``token``, in one transaction.
    204, no body. The request carries a token and nothing else, mirroring
    ``POST /api/planning/fresh-start/undo``'s exact reasoning. One-shot: undoing twice, or with
    a token that isn't the most recent delete, is a 409 that writes nothing."""
    try:
        envelopes_service.undo_delete_envelope(body.token)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return Response(status_code=204)


@router.post("/api/planning/apply")
def apply_change(change: Dict[str, Any]):
    """Apply a generic budget change dict through the service choke point.

    Accepts any ``apply_budget_change`` payload (e.g. ``{type: auto_assign, month, mode}``).
    Returns the service result or raises an HTTP error for typed service exceptions."""
    return _apply(change)


@router.post("/api/planning/auto-assign-preview")
def auto_assign_preview(body: AutoAssignPreviewBody):
    """Compute what auto-assign would do for a given month and mode WITHOUT writing anything.

    Returns ``{steps: [{envelope_id, label, current_assigned, new_assigned, delta}], total_delta}``.
    Each step is a budget row whose Assigned would change. Modes: underfunded,
    assigned_last_month, spent_last_month, average_assigned, average_spent,
    reset_assigned, cover_overspending."""
    try:
        return planning_service.auto_assign_preview(body.month, body.mode)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/planning/sync-commitment-categories")
def sync_commitment_categories():
    """Sync the pinned 'Commitments' envelope group with all active commitments (URL path
    kept for wire compatibility — the underlying mechanism is now envelope-native, no
    category is minted; see ``planning_service.sync_commitment_envelopes``).

    Idempotent — safe to call on every page load. Ensures one dedicated envelope per active
    commitment (linked via ``commitment.envelope_id``) with a monthly target = next
    installment amount divided by months until due. Retires envelopes for commitments that
    dropped out. Returns ``{group_id, synced, removed}``."""
    try:
        return planning_service.sync_commitment_envelopes()
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


# ── Per-envelope notes ──────────────────────────────────────────────────────────


@router.get("/api/planning/notes")
def get_all_notes():
    """Return the full ``{envelope_id: note}`` map."""
    return {"notes": planning_service.read_category_notes()}


@router.put("/api/planning/categories/{envelope_id}/note")
def set_envelope_note(envelope_id: str, body: NoteBody):
    """Set or clear the note for the budget row backed by ``envelope_id``. Pass ``note=''``
    to clear."""
    try:
        planning_service.write_category_note(envelope_id, body.note)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return {"ok": True, "envelope_id": envelope_id}


@router.get("/api/planning/summary")
def get_planning_summary(ym: Optional[str] = None):
    """One month rolled up: envelope progress with its totals, dated goals, month-over-month
    category moves, the day-by-day spend calendar, recurring commitments (converted to base),
    the month's cash flow, and Ready-to-Assign."""
    from backend.services import planning_summary as planning_summary_service

    try:
        return planning_summary_service.budget_aggregates(ym)
    except (OpenNetWorthError, ValueError) as exc:
        raise HTTPException(
            to_http_status(exc) if isinstance(exc, OpenNetWorthError) else 422, str(exc)
        ) from exc

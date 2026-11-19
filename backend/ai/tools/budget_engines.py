"""The Lucius surface over the pluggable budget-engine registry
(``.claude/rfcs/2026-08-22-pluggable-budget-engines.md``, Decision F).

Five tools, exposing the interface ``services/budget_engines/`` ships (``base.py``'s
``ENGINES`` registry + ``get_engine``/``get_verb``/``home_engine_id``/
``set_home_engine_id``, two engines: ``zero_based``, ``proportional``).
This module adds NO business logic of its own; it marshals the registry's own dataclasses
(``Descriptor``/``ViewModel``) into JSON and, for the two writes, delegates straight to
``services/budget_engines/service.py`` — the same way every other ``ai/tools`` module
wraps a ``services/`` call.

  * ``list_budget_engines``        — every registered engine's descriptor + which is home
                                      + which is configured. (read)
  * ``get_budget_view``            — one engine's ``ViewModel`` for a period (default: the
                                      home engine, current month). (read)
  * ``draft_budget_engine_config`` — the engine's ``draft()`` preview; applies nothing. (read)
  * ``apply_budget_operation``     — run a declared operation on any engine: ``(engine_id,
                                      operation, params)`` → the engine's own ``apply_verb``
                                      (renamed 2026-09-26 from ``apply_budget_verb`` —
                                      internally the engines still call them verbs).
                                      ``mutates=("budget",)``. Mirrors ``apply_budget_change``'s
                                      "one tool, many op types" shape (``ai/tools/planning.py``),
                                      generalized across engines — see
                                      ``services.budget_engines.service.apply_verb`` for how a
                                      ``zero_based`` verb routes through the EXISTING
                                      ``apply_budget_change`` choke while every other engine's
                                      verb persists via ``BudgetEngineStateStore``. No new write
                                      choke is created.
  * ``set_home_budget_engine``     — switch the book's home lens (Decision A: never touches
                                      any OTHER engine's own state). ``mutates=("budget",)``.

**Known limitation, not a regression** (RFC Decision F point 1): ``ai.tools._confirm_gate.
effective_level`` keys permission on TOOL NAME only, so ``apply_budget_operation`` collapses
EVERY engine's EVERY operation into one confirm-gate entry — identical to how
``apply_budget_change`` already collapses 20 envelope-native op types into one entry today.
Not solved here; per-verb granularity (if ever wanted) is a change to ``effective_level``'s
contract made for both tools at once, not something either tool can opt into alone.

Zero-based's 24 existing tools (``ai/tools/planning.py``/``envelopes.py``) are untouched:
this is a second, generic surface alongside them, not a replacement — "adding an engine
adds ~0 tools" is the property this earns.

**Phase-1 scope limit, stated explicitly**: this tool surface still passes an empty
``shared``/``history`` to every ``fold``/``draft`` call — building the REAL
paradigm-neutral ``shared`` context (transactions/rates/categories/base currency) per
request is ``services/budget_engines/service.py``'s job, wired through
``GET /api/planning/view`` — not this tool surface. That's harmless for ``zero_based``
(``ZeroBasedEngine.fold``/``draft`` IGNORE ``shared``/``own_state``/``prior_state``/
``history`` entirely — they read ``services.planning`` internally instead) and also
harmless for ``proportional`` (RFC/Decision-H lineage): it is SELF-SUFFICIENT — an empty
``shared``/``history`` just means it computes its own transactions/rates/categories/
spend-history reads instead of reusing a caller-supplied copy (see its module docstring). ``own_state`` DOES come from
the real ``BudgetEngineStateStore`` for every engine, so the one piece of
forward-compatibility that costs nothing was not skipped.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any, Dict, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, bare_object_schema, schema_of
from backend.core.errors import OpenNetWorthError
from backend.core.user_time import user_today
from backend.db.budget_engine_state_store import BudgetEngineStateStore
from backend.services import budget_catalog
from backend.services.budget_engines import base
from backend.services.budget_engines import service as engines_service

logger = logging.getLogger(__name__)


def _current_period() -> str:
    t = user_today()
    return f"{t.year:04d}-{t.month:02d}"


def _is_configured(engine_id: str, configured_state: dict[str, dict]) -> bool:
    """Whether ``engine_id`` has real, usable state to fold from.

    NOT simply "``kind == 'derived'`` -> always ready": ``proportional`` is ``kind ==
    'derived'`` (Decision H) but still owns real per-book state (its needs/wants mapping)
    that must actually be SET before it's usable — acceptance C1's "Set up 50/30/20" empty
    state exists precisely because an unconfigured derived engine is NOT trivially ready.
    So every engine but ``zero_based`` is checked against real ``budget_engine_state``
    presence, regardless of ``kind``. ``zero_based`` alone reads as configured unconditionally: its state lives in
    ``envelopes``/``budget_assignments``/``profile.category_targets`` (Decision E, "no
    engine state is migrated"), never empty for an existing book.
    """
    if engine_id == "zero_based":
        return True
    return engine_id in configured_state


class ListBudgetEnginesInput(ToolInput):
    """Schema-of-record for ``list_budget_engines`` (see ``schema_from_model``)."""


@tool(
    "list_budget_engines",
    description=(
        "List every registered budget-paradigm engine (zero-based, 50/30/20) — id, name, "
        "description, the question it answers, kind (derived = no owned state, configured "
        "= owns real state), and render mode (generic = host's own shapes, custom = ships "
        "its own panel). Also reports the book's current HOME engine, which engines are "
        "configured (ready to fold a view), each engine's own declared OPERATIONS (id/"
        "label/params — pass one of these to apply_budget_operation; the authoritative "
        "list, more reliable than this tool's own free-text guidance), plus a model_note on "
        "some (guidance for you, not user-facing). Call before get_budget_view/"
        "draft_budget_engine_config/apply_budget_operation to discover valid engine_id/"
        "operation values."
    ),
    input_schema=schema_of(ListBudgetEnginesInput, tool_name="list_budget_engines"),
)
async def list_budget_engines(input_data: dict):
    home = base.home_engine_id()
    configured_state = BudgetEngineStateStore().all_for_book()
    engines = [
        {
            "id": engine.descriptor.id,
            "name": engine.descriptor.name,
            "description": engine.descriptor.description,
            "question": engine.descriptor.question,
            "kind": engine.descriptor.kind,
            "render": engine.descriptor.render,
            "configured": _is_configured(engine_id, configured_state),
            "is_home": engine_id == home,
            "operations": [dataclasses.asdict(v) for v in engine.verbs],
            **(
                {"model_note": engine.descriptor.model_note} if engine.descriptor.model_note else {}
            ),
        }
        for engine_id, engine in sorted(base.ENGINES.items())
    ]
    return json.dumps({"engines": engines, "home_engine_id": home}, ensure_ascii=False), None


class GetBudgetViewInput(ToolInput):
    """Schema-of-record for ``get_budget_view`` (see ``schema_from_model``)."""

    engine_id: Optional[str] = Field(
        default=None,
        description=(
            "Engine id from list_budget_engines (e.g. 'zero_based', 'proportional'). Omit "
            "for the book's current home engine."
        ),
    )
    period: Optional[str] = Field(
        default=None,
        description="Budget period as YYYY-MM (e.g. '2026-06'). Omit for the current month.",
    )


@tool(
    "get_budget_view",
    description=(
        "Get the paradigm-neutral budget ViewModel for one engine + period: a headline "
        "figure (e.g. Ready to Assign), groups[].rows[], a health verdict + reasons, and "
        "unmapped (category=None 'Uncategorized' spend). health.reasons is the COMPLETE list of what is over budget (each group past its target, each "
        "envelope past its cap or overspent): answer 'what is over budget' from it, never by "
        "scanning rows. For 'proportional' (50/30/20): groups are the book's Zero-Based groups "
        "(each with its spend, % of income and target when set) then Savings; each row is an "
        "ENVELOPE with measures activity ('Spent'), pct_of_income and, when a cap is set, cap "
        "and left (negative once over); an Excluded group's rows ride on unmapped.rows (Row."
        "status names the group); savings' subtotal carries an 'income' measure. Same numbers "
        "the screen shows. Defaults to the book's HOME engine and the current month. Amounts "
        "are base currency, server-computed; every id ships with its own label, never render "
        "a bare id."
    ),
    input_schema=schema_of(GetBudgetViewInput, tool_name="get_budget_view"),
)
async def get_budget_view(input_data: dict):
    engine_id = (input_data.get("engine_id") or "").strip() or base.home_engine_id()
    period = (input_data.get("period") or "").strip() or _current_period()
    try:
        engine = base.get_engine(engine_id)
        own_state = BudgetEngineStateStore().get(engine_id) or {}
        view = engine.fold(period, shared={}, own_state=own_state, prior_state=None)
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}), None
    return json.dumps(dataclasses.asdict(view), ensure_ascii=False), None


class DraftBudgetEngineConfigInput(ToolInput):
    """Schema-of-record for ``draft_budget_engine_config`` (see ``schema_from_model``)."""

    engine_id: str = Field(description="Engine id from list_budget_engines (e.g. 'proportional')")


@tool(
    "draft_budget_engine_config",
    description=(
        "Propose an initial config for a budget engine from shared data + spending "
        "history (zero_based: a monthly target per envelope; 50/30/20: a target share of "
        "income per group, from each group's last-3-months spend) — a PREVIEW only, "
        "applies and stages nothing."
    ),
    input_schema=schema_of(DraftBudgetEngineConfigInput, tool_name="draft_budget_engine_config"),
)
async def draft_budget_engine_config(input_data: dict):
    engine_id = (input_data.get("engine_id") or "").strip()
    if not engine_id:
        return json.dumps({"error": "engine_id required"}), None
    try:
        engine = base.get_engine(engine_id)
        proposal = engine.draft({}, {})
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}), None
    if proposal is None:
        return (
            json.dumps(
                {
                    "engine_id": engine_id,
                    "has_proposal": False,
                    "message": (
                        f"{engine.descriptor.name} has nothing to propose — it is already "
                        "configured, or needs no draft at all."
                    ),
                },
                ensure_ascii=False,
            ),
            None,
        )
    return (
        json.dumps(
            {"engine_id": engine_id, "has_proposal": True, "proposed_state": proposal},
            ensure_ascii=False,
        ),
        None,
    )


# ── Write: declared operations (RFC Decision F, Phase 3) ──────────────────────────────────


class ApplyBudgetOperationInput(ToolInput):
    """Schema-of-record for ``apply_budget_operation`` (see ``schema_from_model``)."""

    engine_id: str = Field(
        description="Engine id from list_budget_engines (e.g. 'zero_based', 'proportional')."
    )
    operation: str = Field(
        description=(
            "One of that engine's declared operation ids. Call list_budget_engines first — "
            "its response's engines[].operations is the authoritative, always-current list (id/"
            "label/params) for both engines: zero_based's ~20 envelope/group/category/"
            "assignment ops (assign, move_money, set_target, set_targets, "
            "envelope_create, group_create, auto_assign, ...), and 50/30/20's "
            "configure/set_group_targets/set_group_excluded/set_envelope_excluded/set_cap/"
            "set_caps/copy_targets_to_caps/set_income_categories. An unknown operation for the "
            "given engine_id errors, naming the real ones."
        )
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra=bare_object_schema,
        description=(
            "The operation's own parameters, per list_budget_engines' "
            "engines[].operations[].params "
            "— e.g. zero_based assign: {month, envelope_id, amount}. 50/30/20's groups are "
            "the book's Zero-Based groups: configure: {group_targets?: {group_id: percent}, "
            "excluded_groups?: [group_id]} starts it; set_group_targets: {targets: {group_id: "
            "percent or null}}; set_group_excluded: {group_id, excluded: true|false}; "
            "set_envelope_excluded: {envelope_id, excluded: true|false} (one envelope out of "
            "the split, still in its group); set_cap: "
            "{envelope_id, month: 'YYYY-MM', amount} (applies from that month onward, 0 "
            "clears, refused before the current month) or set_caps: {month, caps: "
            "{envelope_id: amount}} for several at once; copy_targets_to_caps: {month} "
            "seeds caps from Zero-Based's own targets; set_income_categories: "
            "{category_ids: [...] or null for every income category}. Moving an envelope to "
            "another group is Zero-Based's move_envelopes_to_group."
        ),
    )


@tool(
    "apply_budget_operation",
    description=(
        "Run one of a budget method's own operations: (engine_id, "
        "operation, params). Operations are per-engine; call list_budget_engines first "
        "to discover valid ids (its engines[].operations is authoritative). An unknown "
        "engine_id or operation errors, naming the real ones. zero_based operations route "
        "through the budget's own write choke (checkpoint-covered, undoable) — for "
        "zero_based prefer assign_budget/move_money/etc., which give a richer preview; use this tool directly for 50/30/20 (configure to start it, "
        "set_group_targets/set_group_excluded/set_envelope_excluded/set_cap/set_caps/"
        "copy_targets_to_caps/set_income_categories to adjust it), or any future engine. "
        "set_home_budget_engine refuses switching to 50/30/20 before configure has run — "
        "call configure first. The result names every envelope it touched."
    ),
    input_schema=schema_of(ApplyBudgetOperationInput, tool_name="apply_budget_operation"),
    mutates=("budget",),
)
async def apply_budget_operation(input_data: dict):
    engine_id = (input_data.get("engine_id") or "").strip()
    operation = (input_data.get("operation") or "").strip()
    params = input_data.get("params") or {}
    if not engine_id or not operation:
        return json.dumps({"ok": False, "error": "engine_id and operation are required"}), None
    try:
        result = engines_service.apply_verb(engine_id, operation, params)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info("apply_budget_operation %s.%s", engine_id, operation)
    return json.dumps(
        {
            "ok": True,
            "engine_id": engine_id,
            "operation": operation,
            "result": budget_catalog.label_engine_result(result, params),
        },
        ensure_ascii=False,
    ), None


# ── Write: home-lens switch (RFC Decision A/E, Phase 2) ──────────────────────────────


class SetHomeBudgetEngineInput(ToolInput):
    """Schema-of-record for ``set_home_budget_engine`` (see ``schema_from_model``)."""

    engine_id: str = Field(
        description="Engine id from list_budget_engines to make the book's home lens."
    )


@tool(
    "set_home_budget_engine",
    description=(
        "Switch the book's HOME budget lens — the paradigm the Plan tab/Overview/reports "
        "default to. Switching NEVER touches any other engine's own state (50/30/20's "
        "group targets/caps, zero_based's assignments all stay exactly as they were), "
        "so switching back later is instant and lossless. An unknown engine_id errors, "
        "naming the real ones. Switching to 'proportional' (50/30/20) BEFORE it has ever "
        "been configured is REFUSED (a typed error naming apply_budget_operation's "
        "'configure' operation) — configure it first: set income via update_profile "
        "(monthly_net_income), then apply_budget_operation operation='configure' "
        "(params may carry group_targets), THEN switch."
    ),
    input_schema=schema_of(SetHomeBudgetEngineInput, tool_name="set_home_budget_engine"),
    mutates=("budget",),
)
async def set_home_budget_engine(input_data: dict):
    engine_id = (input_data.get("engine_id") or "").strip()
    if not engine_id:
        return json.dumps({"ok": False, "error": "engine_id required"}), None
    try:
        result = engines_service.set_home_engine(engine_id)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info("set_home_budget_engine -> %s", engine_id)
    return json.dumps({"ok": True, **result}, ensure_ascii=False), None

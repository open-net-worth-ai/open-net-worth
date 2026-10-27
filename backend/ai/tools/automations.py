"""Lucius tools for the unified Automation surface."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import PreviewAdapter, tool
from backend.ai.tools.schema_from_model import (
    NestedInput,
    ToolInput,
    bare_object_schema,
    schema_of,
    schema_of_batch_only,
)

# The trigger is a free-form object in the schema (its branches are too irregular for a clean
# oneOf), so this string is the ONLY place the model can learn the key names. Keep it concrete
# and exhaustive: an owner incident (2026-08-09) had the model burn four failed calls guessing
# `type`, then `kind`+`datetime`, then `kind`+`schedule_type`, because the old one-line prose
# named no keys at all. Mirrors services/automations/schedules.validate_trigger.
_TRIGGER_DESC = (
    "Trigger object. Always has 'kind'. "
    "manual: {kind:'manual'}. "
    "schedule: {kind:'schedule', type:…} where type is "
    "once {at: ISO-8601 datetime}, daily|weekdays {time:'HH:MM', tzid?}, "
    "weekly {time, day:'mon'..'sun', tzid?}, monthly {time, day:1-31, tzid?}, "
    "interval {seconds:>=60}, or rrule {rrule: iCalendar RRULE body with FREQ and no "
    "'RRULE:' prefix, dtstart?, tzid?} for anything the fixed types cannot express. "
    "event: {kind:'event', event:'txn'|'balance'|'price', when:{…}, "
    "limit:'each'|'hour'|'day'}. "
    "txn when: {conditions:[{field, op, value}]} — ALL must hold; omit for every transaction. "
    "account is|is_not <account id or name> (either side of a transfer); kind is|is_not expense|"
    "income|transfer|investment|investment_buy|investment_sale|adjustment (a buy pays out of the "
    "account, a sale pays in; investment = both); payee is|is_not <payee id or name> — these "
    "three take a list too (is = any of, is_not = none of); amount over|under <number> — "
    "absolute, in the account's currency when 'account is' names one account, else the base "
    "currency; "
    "description|memo contains|not_contains <comma-separated phrases, any counts>, "
    "starts_with|regex <text>. "
    "balance when: {account:<account id>, direction:'below'|'above', amount:<number in that "
    "account's currency>}. "
    "price when: {symbol:<any market ticker, e.g. 'AAPL', 'D05.SI'>, direction:'below'|'above' "
    "(a price in the ticker's own quote currency) or 'rise'|'fall'|'move' (% vs. the previous "
    "close), value:<number>}."
)


_INSTRUCTIONS_DESC = (
    "Exactly what the Automation should do when it runs — never WHEN: no schedule, cadence or "
    "trigger condition ('every Monday', 'when a charge over 500 posts'). The trigger is the only "
    "'when', and each run is told its trigger and last completed run: say 'since the last run'."
)


class AutomationDraftInput(NestedInput):
    name: str = Field(description="Short user-facing name for the Automation.")
    instructions: str = Field(description=_INSTRUCTIONS_DESC)
    description: Optional[str] = Field(
        default=None, description="Optional one-sentence explanation shown in the Automation list."
    )
    trigger: Dict[str, Any] = Field(
        description=_TRIGGER_DESC,
        json_schema_extra=bare_object_schema,
    )
    permission_mode: Optional[Literal["manual", "auto", "skip"]] = Field(
        default=None,
        description=(
            "How much this Automation asks before acting: manual asks every time, auto "
            "proceeds except on hardline operations, skip proceeds regardless. Omit unless "
            "the user explicitly requested one."
        ),
    )
    model: Optional[str] = Field(default=None, description="Optional model override.")
    effort: Optional[Literal["low", "medium", "high", "xhigh", "max", "ultra"]] = Field(
        default=None, description="Reasoning effort for Lucius runs."
    )
    status: Optional[Literal["active", "paused"]] = Field(
        default=None, description="Initial lifecycle state. Omit for active."
    )
    max_runs: Optional[int] = Field(default=None, description="Optional positive run cap.")


class ListAutomationsInput(ToolInput):
    status: Optional[Literal["active", "paused", "completed"]] = Field(
        default=None, description="Filter by lifecycle state."
    )
    limit: int = Field(default=50, ge=1, le=200, description="Maximum rows to return.")
    offset: int = Field(default=0, ge=0, description="Zero-based paging offset.")


class CreateAutomationInput(ToolInput):
    name: str = Field(description="Short user-facing name for the Automation.")
    instructions: str = Field(description=_INSTRUCTIONS_DESC)
    description: Optional[str] = Field(default=None, description="Optional one-sentence summary.")
    trigger: Dict[str, Any] = Field(
        description=_TRIGGER_DESC,
        json_schema_extra=bare_object_schema,
    )
    permission_mode: Optional[Literal["manual", "auto", "skip"]] = None
    model: Optional[str] = Field(default=None, description="Optional model override.")
    effort: Optional[Literal["low", "medium", "high", "xhigh", "max", "ultra"]] = None
    status: Optional[Literal["active", "paused"]] = None
    max_runs: Optional[int] = Field(default=None, ge=1, description="Optional positive run cap.")


class AutomationUpdateInput(NestedInput):
    id: str = Field(description="Automation id from list_automations.")
    name: Optional[str] = None
    instructions: Optional[str] = None
    description: Optional[str] = None
    trigger: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Replacement trigger, same shape as create_automation. " + _TRIGGER_DESC,
        json_schema_extra=bare_object_schema,
    )
    permission_mode: Optional[Literal["manual", "auto", "skip"]] = None
    model: Optional[str] = None
    effort: Optional[Literal["low", "medium", "high", "xhigh", "max", "ultra"]] = None
    status: Optional[Literal["active", "paused"]] = None
    max_runs: Optional[int] = Field(default=None, ge=1)


# The keys an AI-originated update may write. A WHITELIST, not a blacklist, because the
# schema cannot enforce it: NestedInput emits no ``additionalProperties`` and dispatch strips
# that keyword anyway, so an undeclared key like ``always_allowed`` reaches the service
# unchallenged. ``always_allowed`` is the Automation's standing list of tools that skip the
# card — it is written ONLY by grant_automation_tool, from an actual approval tap, never by
# the model narrating an update.
_AI_UPDATABLE_FIELDS = frozenset(
    {
        "name",
        "instructions",
        "description",
        "trigger",
        "permission_mode",
        "model",
        "effort",
        "status",
        "max_runs",
    }
)


class _AutomationUpdatesPreview:
    def build(self, args: dict) -> dict:
        from backend.services.automations.service import preview_automation_updates

        updates = args.get("updates")
        return {
            "kind": "field_diff",
            "data": {
                "items": preview_automation_updates(updates if isinstance(updates, list) else [])
            },
            "raw_arguments": args,
        }

    def validate(self, preview: dict) -> Optional[str]:
        from backend.db.automation_store import AutomationStore

        items = (preview.get("data") or {}).get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            automation_id = item.get("id")
            current = AutomationStore().find_by_id(str(automation_id)) if automation_id else None
            if current is None:
                return f"automation {automation_id!r} was deleted"
            if current.get("updated_at") != item.get("baseline_updated_at"):
                return f"automation {automation_id!r} changed"
        return None


_AUTOMATION_UPDATES_PREVIEW: PreviewAdapter = _AutomationUpdatesPreview()


def _create_spec(input_data: dict) -> dict:
    spec = {key: input_data[key] for key in _AI_UPDATABLE_FIELDS if input_data.get(key) is not None}
    spec["execution"] = {"kind": "lucius"}
    return spec


def _precheck_create(input_data: dict) -> None:
    from backend.services.automations.service import validate_new_automation

    validate_new_automation(_create_spec(input_data))


class UpdateAutomationsInput(ToolInput):
    updates: List[AutomationUpdateInput] = Field(
        min_length=1, description="One or more Automation patches."
    )


class DeleteAutomationsInput(ToolInput):
    automation_ids: List[str] = Field(
        min_length=1, description="Automation ids to permanently delete."
    )


class RunAutomationInput(ToolInput):
    automation_id: str = Field(description="Automation id from list_automations.")


class PreviewAutomationInput(ToolInput):
    automation: AutomationDraftInput = Field(description="Draft to validate without saving it.")


@tool(
    "list_automations",
    description=(
        "List unified Automations, including manual, scheduled, and event-triggered work. "
        "Reminders and categorization rules are Automations, so this is where you look for "
        "an existing one before creating a duplicate."
    ),
    input_schema=schema_of(ListAutomationsInput, tool_name="list_automations"),
)
async def list_automations(input_data: dict):
    from backend.db.automation_store import AutomationStore

    rows = list(reversed(AutomationStore().all()))
    wanted_status = input_data.get("status")
    if wanted_status:
        rows = [row for row in rows if row.get("status") == wanted_status]
    total = len(rows)
    offset = max(0, int(input_data.get("offset") or 0))
    limit = min(200, max(1, int(input_data.get("limit") or 50)))
    page = rows[offset : offset + limit]
    payload = {"automations": page, "showing": len(page), "total": total}
    if offset + len(page) < total:
        payload["note"] = (
            f"Showing {len(page)} of {total}; call again with offset={offset + len(page)}."
        )
    return json.dumps(payload, ensure_ascii=False), None


@tool(
    "create_automation",
    description=(
        "Create one Lucius Automation only when the user explicitly asks to save it now. "
        "For a draft they want to see or review, call preview_automation and then "
        "run_screen_command with automations.review_draft instead. An Automation is also "
        "how a SPEND CATEGORIZATION RULE is expressed — a txn event trigger with match "
        "conditions, whose run categorizes the matching transaction (the old standalone "
        "rules engine was retired 2026-08-02). It is ALSO how a REMINDER is expressed — "
        "remind me to pay a bill, nudge me before a due date, follow up on a deadline — a "
        "schedule trigger whose run notifies the user. A reminder is NOT a commitment: do "
        "not create a commitment, installment or action row just to remind someone of a "
        "payment. A commitment is a dated obligation the user wants tracked against their "
        "plan; a reminder only sends a message."
    ),
    input_schema=schema_of(CreateAutomationInput, tool_name="create_automation"),
    mutates=("automations",),
    precheck=_precheck_create,
)
async def create_automation(input_data: dict):
    from backend.services.automations.service import apply_automation_change

    spec = _create_spec(input_data)
    try:
        automation = apply_automation_change({"op": "create", "automation": spec})
        return json.dumps({"ok": True, "automation": automation}, ensure_ascii=False), None
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), None


@tool(
    "update_automations",
    description="Update one or more existing Automations, including their trigger or paused state.",
    input_schema=schema_of(UpdateAutomationsInput, tool_name="update_automations"),
    mutates=("automations",),
    preview_adapter=_AUTOMATION_UPDATES_PREVIEW,
)
async def update_automations(input_data: dict):
    from backend.services.automations.service import apply_automation_change

    updated: list[dict] = []
    errors: list[dict] = []
    for item in input_data.get("updates") or []:
        automation_id = item.get("id")
        changes = {key: value for key, value in item.items() if key in _AI_UPDATABLE_FIELDS}
        try:
            updated.append(
                apply_automation_change({"op": "update", "id": automation_id, "changes": changes})
            )
        except Exception as exc:
            errors.append({"id": automation_id, "error": str(exc)})
    return json.dumps({"ok": not errors, "updated": updated, "errors": errors}), None


@tool(
    "delete_automations",
    description="Permanently delete one or more Automations after the user explicitly asks.",
    input_schema=schema_of(DeleteAutomationsInput, tool_name="delete_automations"),
    mutates=("automations",),
)
async def delete_automations(input_data: dict):
    from backend.services.automations.service import apply_automation_change

    deleted: list[str] = []
    errors: list[dict] = []
    for automation_id in input_data.get("automation_ids") or []:
        try:
            apply_automation_change({"op": "delete", "id": automation_id})
            deleted.append(automation_id)
        except Exception as exc:
            errors.append({"id": automation_id, "error": str(exc)})
    return json.dumps({"ok": not errors, "deleted": deleted, "errors": errors}), None


@tool(
    "run_automation",
    description="Start an Automation immediately and return its durable run and conversation ids.",
    input_schema=schema_of(RunAutomationInput, tool_name="run_automation"),
    mutates=("automations",),
)
async def run_automation(input_data: dict):
    from backend.services.automations.service import run_automation as run_now

    try:
        run = await run_now(input_data["automation_id"], trigger="manual")
        return json.dumps({"ok": True, "run": run}, ensure_ascii=False), None
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), None


@tool(
    "preview_automation",
    description=(
        "Validate and normalize an Automation draft without creating it — including a "
        "reminder or a categorization rule, both of which are Automations. When the user "
        "asked to see or review the draft, pass the returned automation to "
        "run_screen_command with command automations.review_draft; never create it first."
    ),
    input_schema=schema_of(PreviewAutomationInput, tool_name="preview_automation"),
)
async def preview_automation(input_data: dict):
    from backend.services.automations.schedules import describe_trigger
    from backend.services.automations.service import validate_automation

    fields = {
        "name",
        "instructions",
        "description",
        "trigger",
        "permission_mode",
        "model",
        "effort",
        "status",
        "max_runs",
    }
    raw = dict(input_data.get("automation") or {})
    draft = {key: raw[key] for key in fields if raw.get(key) is not None}
    draft["execution"] = {"kind": "lucius"}
    try:
        normalized = validate_automation(draft)
        return json.dumps(
            {
                "ok": True,
                "automation": normalized,
                "trigger_summary": describe_trigger(normalized["trigger"]),
            },
            ensure_ascii=False,
        ), None
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), None


class RunTaskInput(NestedInput):
    subject: str = Field(
        description=(
            "What this task accomplishes, in the words you would use telling the owner — "
            '"Read today\'s mail", not "email_search_messages". One short line.'
        )
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detail: what exactly this step covers, if the subject leaves it open.",
    )


class AddRunTasksInput(ToolInput):
    tasks: List[RunTaskInput] = Field(
        min_length=1,
        description="The tasks to add, in the order you intend to do them.",
    )


class UpdateRunTaskInput(ToolInput):
    task_id: str = Field(description='The id returned when the task was added, e.g. "2".')
    status: Literal["pending", "in_progress", "completed", "failed"] = Field(
        description="Where the task stands now."
    )


@tool(
    "add_run_tasks",
    description=(
        "Add tasks to this Automation run's task list — the checklist the owner watches. "
        "Call it FIRST, before any other tool, with every step you intend to take, in "
        "plain language. Add more later if the run turns out to need them. Returns each "
        "task's id; mark them started and finished with update_run_task. Has no effect "
        "outside an Automation run."
    ),
    input_schema=schema_of(AddRunTasksInput, tool_name="add_run_tasks"),
)
async def add_run_tasks(input_data: dict):
    from backend.ai import run_plan

    raw = input_data.get("tasks")
    if not isinstance(raw, list) or not raw:
        return json.dumps(
            {"ok": False, "error": "tasks must be a non-empty list of {subject}."},
            ensure_ascii=False,
        ), None
    # A resumed run re-declaring the plan it already has. Answered with the REAL list rather
    # than a bare refusal: the model asked this because the run's opening message told it to,
    # and what it actually needs is to see the ids and statuses it should be updating. A
    # silent no-op would leave it working from the list in its head — which is the same fork,
    # just invisible on the card.
    if run_plan.claim_restatement():
        existing = run_plan.get_tasks()
        return json.dumps(
            {
                "ok": True,
                "added": [],
                "note": (
                    "This run already has a task list from its earlier attempt — nothing was "
                    "added. Work THIS list: mark progress with update_run_task using the ids "
                    "below. Only call add_run_tasks again for a step that is genuinely new."
                ),
                "tasks": [
                    {"id": t["id"], "subject": t["label"], "status": t["status"]} for t in existing
                ],
                "total": len(existing),
            },
            ensure_ascii=False,
        ), None

    added = run_plan.add_tasks([t for t in raw if isinstance(t, dict)])
    if not added:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    "nothing was added — every task needs a non-empty subject, and a run "
                    f"holds at most {run_plan.MAX_TASKS} tasks."
                ),
            },
            ensure_ascii=False,
        ), None
    return json.dumps(
        {
            "ok": True,
            "added": [{"id": t["id"], "subject": t["label"]} for t in added],
            "total": len(run_plan.get_tasks()),
        },
        ensure_ascii=False,
    ), None


@tool(
    "update_run_task",
    description=(
        "Mark one task on this Automation run's list started, finished, or failed. Call it "
        "as each task begins and again as it ends, so the owner can see where the run is. "
        "Has no effect outside an Automation run."
    ),
    input_schema=schema_of(UpdateRunTaskInput, tool_name="update_run_task"),
)
async def update_run_task(input_data: dict):
    from backend.ai import run_plan

    task = run_plan.update_task(
        str(input_data.get("task_id") or ""), str(input_data.get("status") or "")
    )
    if task is None:
        known = [t["id"] for t in run_plan.get_tasks()]
        return json.dumps(
            {
                "ok": False,
                "error": (
                    f"no task {input_data.get('task_id')!r}, or an unusable status. "
                    f"Known task ids: {known or 'none yet — call add_run_tasks first'}. "
                    f"Valid statuses: {list(run_plan.VALID_STATUS)}."
                ),
            },
            ensure_ascii=False,
        ), None
    return json.dumps(
        {"ok": True, "task": {k: task[k] for k in ("id", "label", "status")}}, ensure_ascii=False
    ), None


# ── Rule suggestions ──────────────────────────────────────────────────────────
#
# Restored 2026-08-04. These two lived in ai/tools/rules.py and were deleted wholesale with
# it (f9b5d790) — collateral, not intent. The SERVICE behind them (services/corrections.py)
# stayed live and tested throughout: it watches the user re-categorize the same payee and
# infers "you keep moving STARBUCKS to food". That signal is just as useful now; what
# changed is the thing you create in response — an Automation, not a rules-engine row.


class GetRuleSuggestionsInput(ToolInput):
    """Schema-of-record for ``get_rule_suggestions`` (see ``schema_from_model``)."""


@tool(
    "get_rule_suggestions",
    description=(
        "List categorization SUGGESTIONS the system inferred from the user repeatedly "
        "re-categorizing the same payee (e.g. they moved STARBUCKS to food 3 times). Each is "
        "a payee → category pair plus how many times they made that correction. Use this to "
        "be PROACTIVE: 'you keep recategorizing STARBUCKS as food — want me to automate "
        "that?' — and if they say yes, call create_automation with a txn event trigger "
        "matching that payee (approve-tier: confirm first, never auto-create). If they "
        "decline, call dismiss_rule_suggestions so it stops being suggested. Returns [] when "
        "there is nothing to suggest."
    ),
    input_schema=schema_of(GetRuleSuggestionsInput, tool_name="get_rule_suggestions"),
)
async def get_rule_suggestions(input_data: dict):
    from backend.services import corrections as corrections_service

    return json.dumps(
        {"ok": True, "suggestions": corrections_service.suggestions()}, ensure_ascii=False
    ), None


class DismissalItem(NestedInput):
    """One ``dismiss_rule_suggestions.dismissals`` item. BARE-typed on purpose — the fields'
    own docs already appear once, at the top level of this SAME input_schema."""

    payee_key: str = Field()
    target_category: str = Field()


class DismissRuleSuggestionsInput(ToolInput):
    payee_key: Optional[str] = Field(
        default=None, description="The suggestion's payee_key (from get_rule_suggestions)."
    )
    target_category: Optional[str] = Field(
        default=None,
        description="The suggestion's target_category (from get_rule_suggestions).",
    )
    dismissals: List[DismissalItem] = Field(
        default_factory=list, description="Dismiss several at once, same shape as above."
    )


@tool(
    "dismiss_rule_suggestions",
    description=(
        "Dismiss a categorization suggestion so it never re-surfaces — use when the user "
        "says 'stop suggesting that' or declines one. Identify it by payee_key and "
        "target_category (both from get_rule_suggestions). This does NOT change any existing "
        "transactions; it only suppresses the suggestion."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        DismissRuleSuggestionsInput, array="dismissals", tool_name="dismiss_rule_suggestions"
    ),
    mutates=("corrections",),
    # The pre-2026-07-19c singular name. A tool name is persisted data, and this one
    # survived the rules deletion in skill frontmatter and transcripts.
)
async def dismiss_rule_suggestions(input_data: dict):
    from backend.services import corrections as corrections_service

    items = input_data.get("dismissals")
    if items is not None and items != []:
        if not isinstance(items, list):
            return json.dumps({"ok": False, "error": "dismissals must be a non-empty array"}), None
        results = []
        for i, item in enumerate(items):
            payee_key = isinstance(item, dict) and item.get("payee_key")
            target_category = isinstance(item, dict) and item.get("target_category")
            if not payee_key or not target_category:
                results.append(
                    {
                        "index": i,
                        "ok": False,
                        "error": "payee_key and target_category are required",
                    }
                )
                continue
            corrections_service.dismiss(payee_key, target_category)
            results.append({"index": i, "ok": True, "dismissed": {"payee_key": payee_key}})
        return json.dumps(
            {"ok": True, "results": results, "showing": len(results), "total": len(items)},
            ensure_ascii=False,
        ), None

    payee_key = input_data.get("payee_key")
    target_category = input_data.get("target_category")
    if not payee_key or not target_category:
        return json.dumps(
            {"ok": False, "error": "payee_key and target_category are required"}
        ), None
    corrections_service.dismiss(payee_key, target_category)
    return json.dumps({"ok": True, "dismissed": {"payee_key": payee_key}}, ensure_ascii=False), None

"""Lucius execution and owned-session cleanup adapters for Automations.

This is the upper-layer implementation of the dependency-inverted seams in
``core.automation_runner`` and ``services.automations.service``.  An Automation
run is a normal durable Lucius conversation: the adapter appends one user turn
to the supplied session id, forwards the assistant event stream, and never
clears history after execution.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any, AsyncGenerator, AsyncIterable, Callable, Optional, Sequence

from backend.ai import assistant, pending_injection, run_context, run_plan, skill_runtime, turns
from backend.ai.prompts import build_system_prompt
from backend.ai.tool_text import RoundText
from backend.ai.tools import _skill_scope
from backend.core.audit_context import clear_audit_scope, set_audit_scope
from backend.core.automation_runner import (
    AutomationApprovalContext,
    AutomationEventSink,
    AutomationRunnerRequest,
    AutomationRunnerResult,
    bind_automation_approval,
    register_automation_runner,
    register_automation_session_materializer,
    reset_automation_approval,
    stopped_summary_note,
)
from backend.core.email_consumer import get_email_consumer, set_email_consumer
from backend.core.run_steps import settle_in_flight_steps
from backend.core.turn_cancel import peek_cancel_reason
from backend.core.unattended import reset_unattended, set_unattended
from backend.core.user_time import user_now

logger = logging.getLogger(__name__)


# The tool that lets a run declare its own plan. Loaded before round 1 rather than
# discovered, because the plan is supposed to BE the first thing a run does — a run that
# has to call find_tools first would show "Find tools" above its own checklist. It is the
# run's progress API, not a capability: it reads and writes no user data.
_PLAN_TOOLS = ("add_run_tasks", "update_run_task")


async def _system_prompt(model_context) -> str:
    """``build_system_prompt`` OFF THE LOOP — every automation turn starts here.

    The prompt embeds the operator's current net worth so the model sees the same number
    the dashboard does, and ``ledger.net_worth`` folds the ENTIRE transaction table to get
    it. All three call sites below are ``async def``, so that fold ran on the event loop
    once per turn and held it for the duration — measured >500ms and named by
    ``core.loop_watch`` live on 2026-08-11 17:29 (turns._pump → _events → build_system_prompt
    → net_worth → transaction_store.all). Nothing about building a prompt is async, so it
    belongs in a worker thread.

    ``asyncio.to_thread`` copies the current context, so the audit scope, run plan and
    email consumer this runs inside all survive the hop.
    """
    return await asyncio.to_thread(
        build_system_prompt,
        model=model_context.model,
        base_url=model_context.base_url,
    )


def _load_plan_tool(session_id: str) -> None:
    existing: list[str] = []
    for entry in assistant._read_index():
        if entry.get("id") == session_id:
            existing = entry.get("extra_tool_names") or []
            break
    merged = sorted({*existing, *_PLAN_TOOLS})
    if merged == sorted(set(existing)):
        return
    assistant.upsert_session_meta(session_id, extra_tool_names=merged)


def _seed_plan_from(run: dict) -> None:
    """Restore a run's task list from the run row, so a continuation keeps the same one."""
    tasks = [t for t in (run.get("plan") or []) if isinstance(t, dict)]
    if tasks:
        run_plan.seed(tasks)


def _declared_plan() -> list[dict]:
    """The task list this run kept for itself, or [] if it never added one."""
    return run_plan.get_tasks()


async def automation_runner(
    request: AutomationRunnerRequest,
    event_sink: AutomationEventSink | None = None,
) -> AutomationRunnerResult:
    """Run one Automation as a durable, reopenable Lucius turn.

    ``assistant.run`` owns transcript append/persistence and the approval gate.
    This adapter only projects its event stream into the Automation run shape;
    token and cost values come exclusively from the authoritative
    ``turn_done`` event.
    """

    assistant.upsert_session_meta(
        request.session_id,
        title=request.title,
        source="automation",
        model=request.model,
    )
    audit_token = set_audit_scope(
        actor="ai",
        surface="automation",
        session_id=request.session_id,
        intent=request.instructions,
        automation=request.title,
        automation_id=request.automation_id,
        automation_run_id=request.run_id,
    )
    _load_plan_tool(request.session_id)
    plan_token = run_plan.begin()
    context_token = run_context.begin()
    approval_token = bind_automation_approval(
        AutomationApprovalContext(
            automation_id=request.automation_id,
            run_id=request.run_id,
            session_id=request.session_id,
            mode=request.permission_mode,
            always_allowed_tools=_canonical_always_allowed_tools(request.always_allowed),
            fetch_hosts=frozenset(request.fetch_hosts),
        )
    )
    skill_scope_snapshot = _skill_scope.snapshot_scope()
    # ``fetch_emails`` is incremental PER CONSUMER KEY, and the cursor never rewinds. Without
    # its own key an Automation drains DEFAULT_CONSUMER — the same cursor interactive chat
    # uses — so "check my email" in a conversation would silently eat the messages this run
    # was going to book.
    prior_email_consumer = get_email_consumer()
    set_email_consumer(f"automation:{request.automation_id}")
    # Reclaim anything the PREVIOUS run staged and never settled. A run that is SIGKILLed —
    # a server restart mid-turn, which happened live tonight — reaches neither commit nor
    # discard, and a stage left behind would hold that mail out of every future scan: the
    # very loss two-phase exists to prevent. The last run either finished (nothing staged)
    # or it did not (its claim is void), so an unsettled stage at start-up is always stale.

    text = RoundText()
    proposed_changes: list[dict] = []
    pending_tool_ids: list[str] = []
    steps: list[dict] = []
    steps_by_tool_id: dict[str, dict] = {}
    usage: dict[str, Any] | None = None
    failure: str | None = None
    last_progress_chars = 0

    def _record_failure(exc: BaseException) -> dict:
        """``turns._drive``'s error hook.

        ``_drive`` maps an exception to an event and does NOT re-raise, so without reading
        it here a run whose turn blew up would still report ``completed``.
        """
        nonlocal failure
        failure = str(exc) or type(exc).__name__
        logger.exception("automation Lucius run failed: %s", request.run_id, exc_info=exc)
        return {"type": "error", "message": failure, "run_id": request.run_id}

    async def _events(turn_id: str) -> AsyncGenerator[dict, None]:
        """This run's event source. ``turns._drive`` publishes, times and journals it.

        Closes ``source`` explicitly: if this generator is closed early (a cancelled turn) a
        bare ``async for`` would leave ``source`` merely unreferenced, to be force-closed
        later by asyncio's abandoned-generator finalizer in a freshly forked context — which
        is what corrupts ``_run_turn``'s own cleanup one layer down.
        """
        persisted = assistant.load_session_durable(request.session_id)
        # A new run's real authored instruction is materialized before the run row is
        # exposed. Do not append it twice when execution begins — checked by PRESENCE, not
        # exact equality against a freshly-built opening_message: _automation_message embeds
        # `_today_line()`, which changes every minute, so a real (non-instant) gap between
        # materialize and execution start made an exact-equality check spuriously fail and
        # append a second, near-duplicate copy of the whole instruction sheet as a real,
        # visible message. materialize_automation_session() is the only thing that ever
        # seeds this session's history, and it always runs first, so ANY existing history
        # means the opening message already landed.
        messages = [] if persisted else [_automation_message(request)]
        model_context = assistant.resolve_model_context(request.model or None)
        source = assistant.run(
            request.session_id,
            messages,
            await _system_prompt(model_context),
            model=request.model or None,
            model_context=model_context,
            effort=request.effort,
            surface="automation",
            turn_id=turn_id,
        )
        try:
            async for event in source:
                await _absorb(event)
                yield event
        finally:
            aclose = getattr(source, "aclose", None)
            if aclose is not None:
                await aclose()

    async def _absorb(event: dict) -> None:
        """Fold one event into the run's durable shape and emit a progress patch."""
        nonlocal usage, failure, last_progress_chars
        await _forward_event(event_sink, event)
        event_type = event.get("type")
        if event_type == "text_delta":
            text.add(str(event.get("text") or ""))
        elif event_type == "tool_use_start":
            text.boundary()
            tool_id = str(event.get("id") or "")
            step = {
                "tool_use_id": tool_id,
                "label": _tool_label(event),
                "status": "running",
                "done": False,
            }
            steps.append(step)
            if tool_id:
                steps_by_tool_id[tool_id] = step
        elif event_type == "tool_executing":
            text.boundary()
            # The only event carrying the tool's ARGUMENTS — which is where the run says
            # which accounts it read, which channel it notified, which skill it ran.
            run_context.observe(str(event.get("name") or ""), event.get("input"))
        elif event_type == "tool_done":
            tool_id = str(event.get("id") or "")
            step = steps_by_tool_id.get(tool_id)
            if step is not None:
                step.update({"status": "completed", "done": True})
        elif event_type == "proposed_change":
            change = event.get("change")
            if isinstance(change, dict):
                proposed_changes.append(dict(change))
            tool_id = str(event.get("tool_use_id") or "")
            if tool_id and tool_id not in pending_tool_ids:
                pending_tool_ids.append(tool_id)
            step = steps_by_tool_id.get(tool_id)
            if step is not None:
                step.update({"status": "waiting", "done": False})
        elif event_type == "turn_done":
            raw_usage = event.get("usage")
            usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}
        elif event_type == "error":
            failure = str(event.get("message") or "Automation execution failed")
        summary_so_far = text.raw()
        important = event_type in {
            "tool_use_start",
            "tool_done",
            "proposed_change",
            "turn_done",
            "error",
            "artifact",
        }
        if important or len(summary_so_far) - last_progress_chars >= 256:
            await _forward_event(
                event_sink,
                {
                    "type": "automation_progress",
                    "patch": _progress_patch(
                        request,
                        text.clean(),
                        steps,
                        _declared_plan(),
                        proposed_changes,
                        pending_tool_ids,
                        usage,
                        failure,
                        include_files=event_type in {"artifact", "turn_done"},
                    ),
                },
            )
            last_progress_chars = len(summary_so_far)

    # DELIBERATELY NOT set_unattended(True). Added 2026-08-09 by d2b1d631, it broke every
    # Automation the next morning: `is_unattended()` gates each tool call against the
    # caller's pre-existing OFFER (assistant.py), and an Automation's offer comes from its
    # `always_allowed` list — which is empty by design until the owner grants something from
    # a real approval tap. Empty offer + unattended = every tool refused, so a run fetched
    # mail, got refused on everything after, and still reported Completed.
    #
    # "Unattended" here has to mean "nobody is awake to answer a prompt" — which the
    # approval mode already expresses — NOT "authorized to call nothing". The list governs
    # APPROVAL, never REACHABILITY — which is what its 2026-08-27 rename to `always_allowed`
    # makes plain. The capture and headless paths still set the flag; they
    # each ship a real, non-empty offer, which is the precondition the check assumes.
    # Nobody is watching an Automation run while it executes. This is a statement of FACT,
    # read by exactly one gate (run_script's unsupervised-execution switch) — deliberately
    # NOT a tool ceiling. The predecessor flag was both, and as a ceiling it refused every
    # tool on every run; see core.unattended.
    _unattended_token = set_unattended(True)
    try:
        turn_status = await turns.run_inline(
            request.session_id,
            "automation",
            _events,
            error_event=_record_failure,
            cost_surface="automation",
        )
        if turn_status == "timeout":
            failure = failure or (
                "This run exceeded the turn time limit and was stopped before it finished."
            )
        _persist_automation_history(request.session_id)
    except asyncio.CancelledError:
        # The run row is settled by `runs._task_done` — the only place that can also release
        # the runtime claim — so this just releases the email stage and lets the
        # cancellation reach the task's owner.
        raise
    except Exception as exc:
        # `_drive` maps a failure INSIDE the turn to `_record_failure`; this catches the
        # finalization that happens after it returns, where persisting the transcript can
        # still fail and must not be reported as a completed run.
        logger.exception("automation run could not be finalized: %s", request.run_id)
        failure = str(exc) or type(exc).__name__
    finally:
        # SNAPSHOT BEFORE RESETTING. Both collectors are ContextVar-scoped and every
        # `return` below sits AFTER this block — reading them there got a closed scope, so
        # a run whose plan tool fired twice still persisted `plan: []`.
        declared_plan = _declared_plan()
        touched = run_context.collected()
        run_plan.reset(plan_token)
        run_context.reset(context_token)
        reset_automation_approval(approval_token)
        reset_unattended(_unattended_token)
        _skill_scope.restore_scope(skill_scope_snapshot)
        set_email_consumer(prior_email_consumer)
        clear_audit_scope(audit_token)

    files = _artifact_filenames(request.session_id)
    context = {
        "trigger": dict(request.trigger),
        "payload": dict(request.payload),
        **touched,
    }
    summary = text.clean()

    if failure is not None:
        _mark_running_steps(steps, "failed")
        plan = _mark_running_steps(declared_plan, "failed")
        return AutomationRunnerResult(
            status="failed",
            session_id=request.session_id,
            summary=summary or "Execution failed",
            error=failure[:2000],
            steps=steps,
            plan=plan,
            proposed_changes=proposed_changes,
            pending_tool_ids=pending_tool_ids,
            context=context,
            files=files,
            working_dir=_working_dir(request.execution),
            **_usage_fields(usage),
        )

    if pending_tool_ids:
        # Parked for approval: the turn is not over, but this process is. Release the claim
        # so the continuation (or the next run) is offered the same mail rather than
        # inheriting a stage nobody will ever commit.
        return AutomationRunnerResult(
            status="waiting",
            session_id=request.session_id,
            summary=summary or "Waiting for your approval.",
            steps=steps,
            plan=declared_plan,
            proposed_changes=proposed_changes,
            pending_tool_ids=pending_tool_ids,
            context=context,
            files=files,
            working_dir=_working_dir(request.execution),
            **_usage_fields(usage),
        )

    if usage is None:
        return AutomationRunnerResult(
            status="failed",
            session_id=request.session_id,
            summary=summary or "Execution failed",
            error="Lucius ended without a turn_done event",
            steps=steps,
            plan=declared_plan,
            proposed_changes=proposed_changes,
            context=context,
            files=files,
            working_dir=_working_dir(request.execution),
        )

    _mark_running_steps(steps, "completed")

    # A run finishes when the work it SAID it would do is accounted for. The task list is
    # what makes that checkable: a run that declared five tasks, resolved two and stopped
    # did not finish, however calmly its last message read. Before the list existed there
    # was no way to tell that apart from a genuine no-op ("no new mail, nothing to log"),
    # which is a real success — so the run was recorded green either way.
    #
    # In-flight tasks are NOT quietly completed here. Marking them done would erase the
    # only evidence of where the run stopped, which is the single most useful thing the
    # card can tell you afterwards.
    unfinished = [t for t in declared_plan if t.get("status") not in {"completed", "failed"}]
    failed_tasks = [t for t in declared_plan if t.get("status") == "failed"]
    if not unfinished and failed_tasks and len(failed_tasks) == len(declared_plan):
        # EVERY task failed. A run may deliberately fail one task and still succeed ("no
        # trades found" is a real outcome, pinned by test_a_task_the_run_deliberately_marked
        # _failed_does_not_block_completion), so a single failure must not condemn the run —
        # but nothing succeeding is not a success. This used to fall through to the green
        # path, so the 2026-08-10 run whose five tasks all failed reported `completed` AND
        # committed the staged email cursor. See the twin rule on the continuation path.
        return AutomationRunnerResult(
            status="failed",
            session_id=request.session_id,
            summary=summary or "Ended with failed work.",
            error=(
                f"{len(failed_tasks)} of {len(declared_plan)} tasks failed — "
                f"first failure: {failed_tasks[0].get('label') or 'task'}"
            ),
            steps=steps,
            plan=declared_plan,
            proposed_changes=proposed_changes,
            context=context,
            files=files,
            working_dir=_working_dir(request.execution),
        )
    if unfinished:
        for task in declared_plan:
            if task.get("status") == "in_progress":
                task.update({"status": "failed", "done": False})
        return AutomationRunnerResult(
            status="failed",
            session_id=request.session_id,
            summary=summary or "Ended with work outstanding.",
            error=(
                f"ended with {len(unfinished)} of {len(declared_plan)} tasks unfinished — "
                f"first unfinished: {unfinished[0].get('label') or 'task'}"
            ),
            steps=steps,
            plan=declared_plan,
            proposed_changes=proposed_changes,
            context=context,
            files=files,
            working_dir=_working_dir(request.execution),
            **_usage_fields(usage),
        )

    plan = declared_plan
    return AutomationRunnerResult(
        status="completed",
        session_id=request.session_id,
        summary=summary or "Completed.",
        steps=steps,
        plan=plan,
        proposed_changes=proposed_changes,
        context=context,
        files=files,
        working_dir=_working_dir(request.execution),
        **_usage_fields(usage),
    )


def automation_cleanup(context: dict, *, phase: str) -> dict:
    """Prove and delete only conversations owned by Automation run rows.

    Proof fails closed unless every requested session uses the synthetic
    ``automation-`` namespace and is durably indexed with ``source=automation``
    (or, unstamped, is exactly ``automation-run-{run_id}`` of the run naming it).
    Once that proof is recorded in a deletion tombstone, a retry may accept a
    session that is already absent, but never one that has been re-owned.
    Pending approval rows are removed before their transcript is deleted.
    """

    runs = context.get("runs") if isinstance(context, dict) else None
    rows = runs if isinstance(runs, list) else []
    session_ids = sorted(
        {
            str(row.get("session_id"))
            for row in rows
            if isinstance(row, dict) and row.get("session_id")
        }
    )
    automation = context.get("automation") if isinstance(context, dict) else None
    deletion = automation.get("deletion") if isinstance(automation, dict) else None
    durable_owned = (
        sorted({str(value) for value in deletion.get("owned_session_ids") or [] if str(value)})
        if isinstance(deletion, dict)
        and deletion.get("version") == 1
        and automation.get("status") == "deleting"
        else []
    )
    durable_proof = durable_owned == session_ids
    metadata = {row.get("id"): row for row in assistant.list_sessions()}
    # A run's session is minted as ``automation-run-{run_id}`` (runs.py), so that exact
    # id, referenced by that same run row, proves ownership even when the index entry lost
    # its ``source`` stamp (seen on a 2026-09-02 run). An explicit OTHER source still fails.
    derived = {
        str(row.get("session_id"))
        for row in rows
        if isinstance(row, dict)
        and row.get("id")
        and str(row.get("session_id")) == f"automation-run-{row.get('id')}"
    }
    owned = sorted(
        session_id
        for session_id in session_ids
        if session_id.startswith("automation-")
        and (
            metadata.get(session_id, {}).get("source") == "automation"
            or (durable_proof and session_id not in metadata)
            or (session_id in derived and not metadata.get(session_id, {}).get("source"))
        )
    )
    proof = {"ok": owned == session_ids, "owned_session_ids": owned}
    if phase == "prove" or not proof["ok"]:
        return proof
    if phase != "delete":
        return {"ok": False, "owned_session_ids": owned, "error": "unknown cleanup phase"}

    try:
        from backend.services.artifacts import delete_for_session, list_for_session

        for session_id in session_ids:
            delete_for_session(session_id)
        _delete_pending_for_sessions(set(session_ids))
        for session_id in session_ids:
            assistant.clear_session(session_id)
        remaining_metadata = {row.get("id") for row in assistant.list_sessions()}
        for session_id in session_ids:
            if assistant.load_session_durable(session_id) is not None:
                raise RuntimeError(f"Automation transcript cleanup failed for {session_id}")
            if session_id in remaining_metadata:
                raise RuntimeError(f"Automation session-index cleanup failed for {session_id}")
            if _pending_ids(session_id):
                raise RuntimeError(f"Automation pending-approval cleanup failed for {session_id}")
            if list_for_session(session_id):
                raise RuntimeError(f"Automation artifact cleanup failed for {session_id}")
    except Exception as exc:
        logger.exception("Automation dependency cleanup failed")
        return {
            "ok": False,
            "owned_session_ids": owned,
            "error": str(exc) or type(exc).__name__,
        }
    return {"ok": True, "owned_session_ids": owned}


def materialize_automation_session(run: dict, automation: Optional[dict] = None) -> str:
    """Create the durable conversation owned by ``run``, idempotently.

    Legacy rows have no trustworthy transcript mapping. Their replacement transcript is
    therefore a compact structured rendering of recorded run facts only—never invented
    dialogue or a guessed link to another conversation.
    """
    run_id = str(run.get("id") or "").strip()
    if not run_id:
        raise ValueError("automation run id is required to materialize its conversation")
    session_id = str(run.get("session_id") or f"automation-run-{run_id}")
    existing = assistant.load_session_durable(session_id)
    created_transcript = existing is None
    if not isinstance(existing, list) or not existing:
        if "started_at" not in run and automation is not None:
            request = AutomationRunnerRequest(
                automation_id=str(run.get("automation_id") or automation.get("id") or ""),
                run_id=run_id,
                session_id=session_id,
                title=str(automation.get("name") or run_id),
                instructions=str(automation.get("instructions") or ""),
                trigger=dict(run.get("trigger") or {}),
                execution=dict(automation.get("execution") or {"kind": "lucius"}),
                model=str(automation.get("model") or ""),
                effort=str(automation.get("effort") or "medium"),
                payload=dict(run.get("context") or {}),
                permission_mode=str(automation.get("permission_mode") or "manual"),
                always_allowed=list(automation.get("always_allowed") or []),
                fetch_hosts=list(automation.get("fetch_hosts") or []),
            )
            history = [_automation_message(request)]
        else:
            facts = {
                "status": run.get("status"),
                "summary": run.get("summary") or "",
                "error": run.get("error"),
                "steps": list(run.get("steps") or []),
                "files": list(run.get("files") or []),
                "context": dict(run.get("context") or {}),
                "cost_usd": run.get("cost_usd"),
                "input_tokens": run.get("input_tokens"),
                "output_tokens": run.get("output_tokens"),
                "cache_creation_tokens": run.get("cache_creation_tokens"),
                "cache_read_tokens": run.get("cache_read_tokens"),
                "model": run.get("model") or "",
                "effort": run.get("effort") or "medium",
            }
            record = json.dumps({"automation_run": facts}, ensure_ascii=False, sort_keys=True)
            answer = str(run.get("summary") or run.get("error") or run.get("status") or "")
            history = [{"role": "user", "content": record}]
            if answer:
                history.append({"role": "assistant", "content": answer})
        assistant.SESSIONS[session_id] = history
        try:
            assistant.save_session_durable(session_id, history)
        except Exception:
            assistant.SESSIONS.pop(session_id, None)
            raise
    try:
        assistant.upsert_session_meta(
            session_id,
            title=(automation or {}).get("name") or f"Automation run {run_id}",
            source="automation",
            model=run.get("model") or (automation or {}).get("model") or "",
            fail_loud=True,
        )
    except Exception:
        if created_transcript:
            assistant.clear_session(session_id)
        raise
    return session_id


async def stream_automation_followup(
    session_id: str,
    new_messages: list[dict],
    *,
    model: str | None = None,
    turn_id: str | None = None,
    direct_invocations: Sequence[skill_runtime.InvocationRequest] = (),
    recovered_injections: Sequence[pending_injection.QueuedMessage] = (),
    on_recovered_adopted: Callable[[Sequence[pending_injection.QueuedMessage]], None] | None = None,
) -> AsyncGenerator[dict, None]:
    """``model`` is the caller's already-resolved override (explicit request → session-
    stored → the Automation's own configured model — see ``api/chat.py``'s send handler).
    Without it this always fell back to ``request.model`` (the Automation's OWN config,
    from ``request_for_run``), silently discarding whatever the user picked in the
    composer for every follow-up typed into an Automation-owned conversation."""

    async def source(request: AutomationRunnerRequest) -> AsyncIterable[dict]:
        resolved_model = model or request.model or None
        model_context = assistant.resolve_model_context(resolved_model)
        return assistant.run(
            session_id,
            new_messages,
            await _system_prompt(model_context),
            model=resolved_model,
            model_context=model_context,
            effort=request.effort,
            surface="automation",
            turn_id=turn_id,
            direct_invocations=direct_invocations,
            recovered_injections=recovered_injections,
            on_recovered_adopted=on_recovered_adopted,
        )

    stream = _stream_continuation(session_id, source)
    try:
        async for event in stream:
            yield event
    finally:
        await stream.aclose()


async def stream_automation_resolution(
    session_id: str, tool_use_id: str, decision: str, *, turn_id: str | None = None
) -> AsyncGenerator[dict, None]:
    async def source(request: AutomationRunnerRequest) -> AsyncIterable[dict]:
        model_context = assistant.resolve_model_context(request.model or None)
        return assistant.resolve_pending_tool_use(
            session_id,
            tool_use_id,
            decision,
            await _system_prompt(model_context),
            model=request.model or None,
            model_context=model_context,
            effort=request.effort,
            surface="automation",
            turn_id=turn_id,
        )

    stream = _stream_continuation(session_id, source)
    try:
        async for event in stream:
            yield event
    finally:
        await stream.aclose()


async def _stream_continuation(
    session_id: str,
    source_factory,
) -> AsyncGenerator[dict, None]:
    from backend.services.automations.runs import (
        begin_automation_continuation,
        persist_automation_progress,
        reconcile_automation_continuation,
        request_for_run,
    )

    automation, baseline = begin_automation_continuation(session_id)
    # Resume the SAME checklist the run was ticking before it parked for approval. The plan
    # lives in a ContextVar that died with the original turn, so without this the
    # continuation would report only the steps it took after the card was answered and the
    # card would appear to erase the run's history.
    plan_token = run_plan.begin()
    context_token = run_context.begin()
    _seed_plan_from(baseline)
    projection: _EventProjection | None = None
    approval_token = None
    audit_token = None
    prior_email_consumer: str | None = None
    failure: str | None = None
    stream = None
    try:
        persisted = assistant.load_session_durable(session_id)
        if not persisted:
            raise RuntimeError("Automation conversation transcript is missing or empty")
        request = request_for_run(automation, baseline)
        projection = _EventProjection(request)
        approval_token = bind_automation_approval(
            AutomationApprovalContext(
                automation_id=request.automation_id,
                run_id=request.run_id,
                session_id=request.session_id,
                mode=request.permission_mode,
                always_allowed_tools=_canonical_always_allowed_tools(request.always_allowed),
                fetch_hosts=frozenset(request.fetch_hosts),
            )
        )
        audit_token = set_audit_scope(
            actor="ai",
            surface="automation",
            session_id=session_id,
            intent="Automation conversation continuation",
            automation=request.title,
            automation_id=request.automation_id,
            automation_run_id=request.run_id,
        )
        # Same per-Automation IMAP cursor as the initial run (see ``automation_runner``) — a
        # resumed turn must not fall back to the shared default and drain interactive email.
        prior_email_consumer = get_email_consumer()
        set_email_consumer(f"automation:{request.automation_id}")
        stream = await source_factory(request)
        try:
            async for event in stream:
                projection.consume(event)
                if projection.should_persist(event):
                    persist_automation_progress(
                        baseline["id"],
                        _merge_progress_patch(baseline, projection.patch()),
                    )
                yield event
            _persist_automation_history(session_id)
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
    except asyncio.CancelledError:
        # A Stop tap on an Automation conversation's follow-up turn. It settles `cancelled`
        # like a stopped RUN does — the same user action must not read as two statuses
        # depending on which turn of the conversation it landed on.
        if projection is not None:
            projection.cancelled = True
            projection.failure = None
        raise
    except BaseException as exc:
        if not isinstance(exc, GeneratorExit):
            failure = str(exc) or type(exc).__name__
            if projection is not None:
                projection.failure = failure
        raise
    finally:
        try:
            result = (
                projection.result(pending_tool_ids=_pending_ids(session_id))
                if projection is not None
                else AutomationRunnerResult(
                    status="failed",
                    session_id=session_id,
                    error=failure or "Automation continuation setup did not complete",
                )
            )
            reconcile_automation_continuation(automation, baseline, result)
        finally:
            run_plan.reset(plan_token)
            run_context.reset(context_token)
            if approval_token is not None:
                reset_automation_approval(approval_token)
            if prior_email_consumer is not None:
                set_email_consumer(prior_email_consumer)
            if audit_token is not None:
                clear_audit_scope(audit_token)


def _persist_automation_history(session_id: str) -> None:
    history = assistant.SESSIONS.get(session_id)
    if not isinstance(history, list) or not history:
        raise RuntimeError("Automation conversation has no in-memory transcript to persist")
    assistant.save_session_durable(session_id, history)


def register_automation_adapters() -> None:
    """Register the runner and cleanup implementations at their lower-layer seams."""

    from backend.services.automations.service import register_automation_cleanup

    register_automation_runner(automation_runner)
    register_automation_session_materializer(materialize_automation_session)
    register_automation_cleanup(automation_cleanup)
    from backend.services.automations.runs import recover_automation_runs

    outcome = recover_automation_runs(
        materialize_session=materialize_automation_session,
        pending_for_session=_pending_ids,
    )
    if outcome["backfilled"] or outcome["recovered"]:
        logger.info("automation runtime recovery: %s", outcome)


def unregister_automation_adapters() -> None:
    """Clear both registrations, primarily for isolated tests and shutdown."""

    from backend.services.automations.service import register_automation_cleanup

    register_automation_runner(None)
    register_automation_session_materializer(None)
    register_automation_cleanup(None)


class _EventProjection:
    def __init__(self, request: AutomationRunnerRequest):
        self.request = request
        self.text = RoundText()
        self.proposed_changes: list[dict] = []
        self.pending_tool_ids: list[str] = []
        self.steps: list[dict] = []
        self.steps_by_tool_id: dict[str, dict] = {}
        self.usage: dict[str, Any] | None = None
        self.failure: str | None = None
        self.cancelled = False
        self._last_progress_chars = 0

    def consume(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "text_delta":
            self.text.add(str(event.get("text") or ""))
        elif event_type == "tool_use_start":
            self.text.boundary()
            tool_id = str(event.get("id") or "")
            step = {
                "tool_use_id": tool_id,
                "label": _tool_label(event),
                "status": "running",
                "done": False,
            }
            self.steps.append(step)
            if tool_id:
                self.steps_by_tool_id[tool_id] = step
        elif event_type == "tool_executing":
            self.text.boundary()
            run_context.observe(str(event.get("name") or ""), event.get("input"))
        elif event_type == "tool_done":
            tool_id = str(event.get("id") or "")
            step = self.steps_by_tool_id.get(tool_id)
            if step is not None:
                step.update({"status": "completed", "done": True})
            elif tool_id:
                self.steps.append(
                    {
                        "tool_use_id": tool_id,
                        "label": "Approved action",
                        "status": "completed",
                        "done": True,
                    }
                )
        elif event_type == "proposed_change":
            change = event.get("change")
            if isinstance(change, dict):
                self.proposed_changes.append(dict(change))
            tool_id = str(event.get("tool_use_id") or "")
            if tool_id and tool_id not in self.pending_tool_ids:
                self.pending_tool_ids.append(tool_id)
            step = self.steps_by_tool_id.get(tool_id)
            if step is not None:
                step.update({"status": "waiting", "done": False})
        elif event_type == "turn_done":
            raw = event.get("usage")
            self.usage = dict(raw) if isinstance(raw, dict) else {}
        elif event_type == "error":
            self.failure = str(event.get("message") or "Automation execution failed")

    def should_persist(self, event: dict) -> bool:
        event_type = event.get("type")
        if event_type in {
            "tool_use_start",
            "tool_done",
            "proposed_change",
            "turn_done",
            "error",
            "artifact",
        }:
            return True
        size = len(self.text.raw())
        if event_type == "text_delta" and size - self._last_progress_chars >= 256:
            self._last_progress_chars = size
            return True
        return False

    @property
    def summary(self) -> str:
        return self.text.clean()

    def patch(self) -> dict:
        # Keyword-passed on purpose: this call previously drifted out of sync with the
        # signature TWICE (a deleted `reported_steps` property, and a `plan` parameter added
        # in the middle that silently shifted every later argument by one).
        return _progress_patch(
            self.request,
            summary=self.summary,
            steps=self.steps,
            plan=_declared_plan(),
            proposed_changes=self.proposed_changes,
            pending_tool_ids=self.pending_tool_ids,
            usage=self.usage,
            failure=self.failure,
            include_files=True,
        )

    def result(self, *, pending_tool_ids: list[str]) -> AutomationRunnerResult:
        if self.cancelled:
            # Outranks every other reading: the user stopped this turn, so whatever else the
            # projection saw, "failed" would be a lie about what happened.
            status = "cancelled"
        elif pending_tool_ids:
            status = "waiting"
        elif self.failure is not None:
            status = "failed"
        elif self.usage is not None:
            status = "completed"
        else:
            status = "failed"
            self.failure = "Automation continuation ended before Lucius completed the turn"
        plan = _declared_plan()
        if status == "completed":
            # Same rule as a fresh run: work the continuation SAID it would do has to be
            # accounted for. Resolved means completed or failed, not succeeded.
            unfinished = [t for t in plan if t.get("status") not in {"completed", "failed"}]
            failed_tasks = [t for t in plan if t.get("status") == "failed"]
            if unfinished:
                status = "failed"
                self.failure = (
                    f"ended with {len(unfinished)} of {len(plan)} tasks unfinished — "
                    f"first unfinished: {unfinished[0].get('label') or 'task'}"
                )
            elif failed_tasks and len(failed_tasks) == len(plan):
                # Twin of the fresh-run rule: one deliberate failure is fine, EVERY task
                # failing is not a completed run — and `completed` is what commits the
                # staged email cursor.
                status = "failed"
                self.failure = self.failure or (
                    f"{len(failed_tasks)} of {len(plan)} tasks failed — "
                    f"first failure: {failed_tasks[0].get('label') or 'task'}"
                )
        if status in {"failed", "cancelled"}:
            _mark_running_steps(self.steps, "failed")
            _mark_running_steps(plan, "failed")
        elif status == "completed":
            _mark_running_steps(self.steps, "completed")
        summary = self.summary
        if status == "cancelled":
            # peek, not take: the run's own settle path (services.automations.runs) is the
            # one that CONSUMES the reason, and a follow-up turn's projection must not eat
            # the explanation out from under it.
            note = stopped_summary_note(peek_cancel_reason(self.request.session_id))
            summary = f"{summary}\n\n{note}".strip()
        return AutomationRunnerResult(
            status=status,
            session_id=self.request.session_id,
            summary=summary,
            error=self.failure[:2000] if self.failure else None,
            steps=self.steps,
            plan=plan,
            proposed_changes=self.proposed_changes,
            pending_tool_ids=pending_tool_ids,
            # Merged onto the baseline by services.automations.runs._result_patch, so a
            # continuation ADDS what it touched rather than replacing the run's context.
            context=run_context.collected(),
            files=_artifact_filenames(self.request.session_id),
            working_dir=_working_dir(self.request.execution),
            **_usage_fields(self.usage),
        )


def _canonical_always_allowed_tools(always_allowed: list[dict]) -> frozenset[str]:
    from backend.ai.tools.registry import registered_tool_names

    live = set(registered_tool_names())
    result: set[str] = set()
    for descriptor in always_allowed or []:
        if not isinstance(descriptor, dict):
            continue
        candidates = descriptor.get("tools")
        names = list(candidates) if isinstance(candidates, list) else []
        capability = descriptor.get("capability")
        if isinstance(capability, str) and capability.strip():
            names.append(capability.strip())
        for name in names:
            if not isinstance(name, str):
                continue
            canonical = name.strip()
            if canonical in live:
                result.add(canonical)
    return frozenset(result)


def _progress_patch(
    request: AutomationRunnerRequest,
    summary: str,
    steps: list[dict],
    plan: list[dict],
    proposed_changes: list[dict],
    pending_tool_ids: list[str],
    usage: dict[str, Any] | None,
    failure: str | None,
    *,
    include_files: bool,
) -> dict:
    patch = {
        "session_id": request.session_id,
        "summary": summary,
        "error": failure[:2000] if failure else None,
        "steps": [dict(step) for step in steps],
        "plan": [dict(step) for step in plan],
        "proposed_changes": [dict(change) for change in proposed_changes],
        "pending_tool_ids": list(pending_tool_ids),
        "context": {
            "trigger": dict(request.trigger),
            "payload": dict(request.payload),
            **run_context.collected(),
        },
        "working_dir": _working_dir(request.execution),
        **_usage_fields(usage),
    }
    if include_files:
        patch["files"] = _artifact_filenames(request.session_id)
    return patch


def _merge_progress_patch(baseline: dict, patch: dict) -> dict:
    merged = dict(patch)
    merged["summary"] = patch.get("summary") or baseline.get("summary") or ""
    merged["steps"] = list(baseline.get("steps") or []) + list(patch.get("steps") or [])
    merged["context"] = {**(baseline.get("context") or {}), **(patch.get("context") or {})}
    if "files" in patch:
        merged["files"] = [*(baseline.get("files") or []), *(patch.get("files") or [])]
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
    ):
        if patch.get(key) is not None:
            merged[key] = int(baseline.get(key) or 0) + int(patch[key])
    if patch.get("cost_usd") is not None:
        merged["cost_usd"] = float(baseline.get("cost_usd") or 0) + float(patch["cost_usd"])
    return merged


def _pending_ids(session_id: str) -> list[str]:
    from backend.db.pending_plan_store import PendingPlanStore

    return sorted(
        str(row["tool_use_id"])
        for row in PendingPlanStore().list_awaiting()
        if row.get("session_id") == session_id and row.get("tool_use_id")
    )


async def _forward_event(
    event_sink: AutomationEventSink | None,
    event: dict,
) -> None:
    if event_sink is None:
        return
    try:
        result = event_sink(dict(event))
        if inspect.isawaitable(result):
            await result
    except Exception:
        # Progress reporting is observational. A disconnected observer must not
        # abort a turn after a tool may already have mutated durable user data.
        logger.warning("automation event sink failed", exc_info=True)


def _tool_label(event: dict) -> str:
    """An automation run-step's label — the same sentence the chat transcript shows.

    Was a local ``name.replace("_", " ").capitalize()``, which rendered an external MCP call
    as "Mcp  playwright  browser navigate". A run's step list and a chat transcript are the
    same events seen through two surfaces, so they resolve through the one labeler
    (``ai.tools.display``) rather than each inventing its own vocabulary.
    """
    from backend.ai.tools.display import describe_tool_call

    name = str(event.get("name") or event.get("tool") or "")
    display = event.get("display")
    if isinstance(display, dict) and display.get("label"):
        return str(display["label"])
    return describe_tool_call(name, event.get("input")).get("label") or "Tool"


def _today_line() -> str:
    """Same shape the shared prompt uses: date, weekday, local time and offset."""
    now = user_now()
    return now.strftime("%Y-%m-%d — %A %H:%M %Z (UTC%z)")


def _automation_message(request: AutomationRunnerRequest) -> dict:
    """Keep the authored instruction visible and persist trigger data as hidden model context."""

    context = json.dumps(
        {
            "trigger": request.trigger,
            "payload": request.payload,
            # Why it ran and since when: the instructions say what to do, never when.
            "configured_trigger": request.configured_trigger,
            "last_completed_run": request.last_completed_run,
        },
        sort_keys=True,
        default=str,
    )
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": request.instructions},
            {
                "type": "text",
                "text": (
                    "<turn_context>\n"
                    # The clock, stated HERE and not only in the shared per-turn tail. That
                    # tail is attached once to the last user message at turn start; an
                    # Automation then runs 20+ tool rounds with context folding active, so
                    # the date scrolls out of reach and can be summarised away entirely.
                    # A run then reasons about "the last day" with no idea what day it is —
                    # live 2026-08-11: "I don't know today's exact date. The turn context
                    # doesn't state it." It cannot go in the CACHED prefix (a value that
                    # changes every minute would rewrite the cache every turn — pinned by
                    # tests/test_prompt_clock.py), but the run's own opening message is
                    # re-read from history on every round, so it survives the whole run.
                    f"TODAY'S DATE: {_today_line()}\n\n"
                    "AUTOMATION RUN CONTEXT (data supplied by the validated trigger):\n"
                    f"{context}\n"
                    "\n"
                    "Nobody is watching this run as it happens; they read the run card "
                    "afterwards. Before your first other tool call, call add_run_tasks with "
                    "the steps you intend to take, in your own words — what each step "
                    "achieves, not which tool it uses. Then call update_run_task as each "
                    "task starts and again as it finishes, so the card tracks where you "
                    "are. Add more tasks later if the run turns out to need them.\n"
                    "\n"
                    "If something this run needs is not connected — a mailbox, a bank, a "
                    "service — say which one and that it can be set up in the Connectors tab, "
                    "then stop. Never report data you did not get from a tool in this run.\n"
                    "</turn_context>"
                ),
            },
        ],
    }


def _mark_running_steps(steps: list[dict], status: str) -> list[dict]:
    """Settle whatever was still in flight when the run ended, and hand the list back."""
    return settle_in_flight_steps(steps, status)


def _usage_fields(usage: dict[str, Any] | None) -> dict[str, Any]:
    if usage is None:
        return {}
    return {
        "model": str(usage.get("model") or ""),
        "input_tokens": _optional_int(usage.get("input_tokens")),
        "output_tokens": _optional_int(usage.get("output_tokens")),
        "cache_creation_tokens": _optional_int(usage.get("cache_creation_tokens")),
        "cache_read_tokens": _optional_int(usage.get("cache_read_tokens")),
        "cost_usd": _optional_float(usage.get("cost_usd")),
        "stats": {key: usage[key] for key in ("model", "cost_basis") if key in usage},
    }


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _working_dir(execution: dict) -> str | None:
    value = execution.get("working_dir") if isinstance(execution, dict) else None
    return str(value) if isinstance(value, str) and value else None


def _artifact_filenames(session_id: str) -> list[str]:
    try:
        from backend.services.artifacts import list_for_session

        return [
            str(row["filename"])
            for row in list_for_session(session_id)
            if isinstance(row, dict) and row.get("filename")
        ]
    except Exception:
        logger.warning("could not list Automation artifacts", exc_info=True)
        return []


def _delete_pending_for_sessions(session_ids: set[str]) -> None:
    if not session_ids:
        return
    from backend.db.pending_plan_store import PendingPlanStore

    store = PendingPlanStore()
    for row in store.list_awaiting():
        if row.get("session_id") not in session_ids:
            continue
        tool_use_id = str(row.get("tool_use_id") or "")
        if tool_use_id:
            store.pop_pending(tool_use_id)

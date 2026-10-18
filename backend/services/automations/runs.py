"""Automation run lifecycle and due-schedule execution."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from backend.core.automation_runner import (
    AutomationRunnerRequest,
    AutomationRunnerResult,
    materialize_automation_session,
    run_with_automation_runner,
    stopped_summary_note,
)
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.core.run_steps import settle_in_flight_steps
from backend.core.turn_cancel import take_cancel_reason
from backend.db.automation_run_store import AutomationRunStore, TransitionConflict
from backend.db.automation_runtime_store import AutomationRuntimeStore
from backend.db.automation_store import DELETING_STATUS, AutomationStore
from backend.db.session_store import SessionStore

from .run_context import configured_trigger, last_completed_run
from .schedules import compute_next_run, ran_slot_floor

logger = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()
_tasks_by_run: dict[str, asyncio.Task] = {}
_ALL_STATUSES = frozenset({"running", "waiting", "completed", "failed", "cancelled"})
_OWNED_STATUSES = frozenset({"running", "waiting"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_RESUMABLE_STATUSES = frozenset({"waiting", "completed", "failed", "cancelled"})
# Data-hygiene retention (owner's box: automation_runs + their transcript sessions grow
# forever otherwise). Mirrors RunHistoryStore.append's own per-entity cap, one level up: an
# AutomationRunStore row carries a real conversation (a `sessions` + `session_messages` row
# apiece, via `materialize_automation_session`), so trimming a run here also drops its
# session, not just the run record.
_MAX_RUNS_PER_AUTOMATION = 100


def _trim_runs_to_cap(automation_id: str) -> None:
    """Keep only the newest `_MAX_RUNS_PER_AUTOMATION` runs for `automation_id`, deleting
    each trimmed run's OWN dedicated session with it. Best-effort — called right after a new
    run starts, never allowed to block or fail it (see the caller's try/except).

    Only TERMINAL runs are ever trimmed, defensively — `for_automation` is newest-first, and
    an automation has at most one non-terminal run at a time (the runtime claim in
    `run_automation` serializes this), so a non-terminal row should never even reach the
    stale slice; the filter just makes that a proven property here rather than an assumption.
    Each run's session is deterministically its OWN (`materialize_automation_session` names
    it ``automation-run-{run_id}``, never shared), so no ownership proof is needed before
    deleting it — unlike the full `_hard_delete` automation-delete path, which tears down a
    live automation's still-referenced sessions/artifacts/pending-approvals via the
    registered cleanup adapter."""
    runs = AutomationRunStore().for_automation(automation_id, limit=1_000_000, offset=0)
    if len(runs) <= _MAX_RUNS_PER_AUTOMATION:
        return
    stale = [r for r in runs[_MAX_RUNS_PER_AUTOMATION:] if r.get("status") in _TERMINAL_STATUSES]
    if not stale:
        return
    run_store = AutomationRunStore()
    session_store = SessionStore()
    for run in stale:
        session_id = run.get("session_id")
        if session_id:
            session_store.delete(session_id)
        run_store.delete(run["id"])
    logger.info(
        "automation %s: retention trimmed %d run(s) past the %d-run cap",
        automation_id,
        len(stale),
        _MAX_RUNS_PER_AUTOMATION,
    )


async def run_automation(
    automation_id: str,
    *,
    trigger: str | dict,
    payload: dict | None = None,
    run_id: str | None = None,
) -> dict:
    automation = AutomationStore().find_by_id(automation_id)
    if automation is None:
        raise NotFoundError(f"automation {automation_id!r} not found")
    if automation.get("execution", {}).get("kind") != "lucius":
        raise ValidationError("automation execution must use Lucius")
    trigger_data = dict(trigger) if isinstance(trigger, dict) else {"kind": str(trigger)}
    run_id = run_id or new_id("run")
    claimed, acquired = _claim_runtime(automation_id, run_id)
    if not claimed:
        raise ConflictError(f"automation {automation_id!r} is already running")
    latest = AutomationStore().find_persisted_by_id(automation_id)
    if latest is None or latest.get("status") == DELETING_STATUS:
        _rollback_runtime_claim(automation_id, run_id, acquired=acquired)
        raise NotFoundError(f"automation {automation_id!r} not found")
    automation = latest
    if automation.get("execution", {}).get("kind") != "lucius":
        _rollback_runtime_claim(automation_id, run_id, acquired=acquired)
        raise ValidationError("automation execution must use Lucius")
    draft = {
        "id": run_id,
        "automation_id": automation_id,
        "session_id": f"automation-run-{run_id}",
        "status": "running",
        "trigger": trigger_data,
        "execution": automation.get("execution") or {},
        "model": automation.get("model") or "",
        "effort": automation.get("effort") or "medium",
        "context": dict(payload or {}),
    }
    try:
        # The conversation is durable before the row can be returned by this function. If
        # materialisation fails, no exposed run row is created.
        materialize_automation_session(draft, automation)
        run = AutomationRunStore().start(draft)
    except BaseException:
        _release_runtime(automation_id, run_id)
        raise
    try:
        _trim_runs_to_cap(automation_id)
    except Exception:
        logger.exception("automation %s: run retention trim failed", automation_id)
    try:
        task = asyncio.create_task(_execute_run(dict(automation), dict(run), dict(payload or {})))
    except BaseException as exc:
        failed, _ = AutomationRunStore().transition(
            run["id"],
            {"running"},
            "failed",
            {"summary": "Execution failed", "error": str(exc)[:2000]},
        )
        _finish_automation_once(automation, failed, "failed", dict(payload or {}))
        _release_runtime(automation_id, run["id"])
        raise
    _tasks.add(task)
    _tasks_by_run[run["id"]] = task
    task.add_done_callback(lambda completed, run_id=run["id"]: _task_done(run_id, completed))
    return run


def _task_done(run_id: str, task: asyncio.Task) -> None:
    _tasks.discard(task)
    _tasks_by_run.pop(run_id, None)
    if task.cancelled():
        _settle_cancelled_run(run_id)
        return
    try:
        task.result()
    except BaseException:
        logger.exception("automation background run crashed")


def _settle_cancelled_run(run_id: str) -> None:
    """Record a stopped run as terminal and release its runtime claim.

    Without this the row sits at ``running`` forever and the claim is never released, so the
    Automation can never be run again — the state a Stop button must not be able to create.

    Settles the run's own checklist too. The task that was mid-flight when the cancellation
    landed is never coming back to close itself, so leaving it ``in_progress`` left the card
    drawing a dead run as one still working — the reading the owner actually got on
    2026-08-21 for a run cancelled by a dev reload nineteen hours earlier.
    """
    run = AutomationRunStore().find_by_id(run_id)
    if run is None:
        return
    summary = str(run.get("summary") or "").strip()
    reason = take_cancel_reason(str(run.get("session_id") or ""))
    note = stopped_summary_note(reason)
    try:
        stopped, _ = AutomationRunStore().transition(
            run_id,
            {"running"},
            "cancelled",
            {
                "summary": f"{summary}\n\n{note}".strip(),
                # Deliberately not an `error`: a cancelled run did not fail, and the red
                # banner is for failures. The explanation rides on `summary`, which the run
                # card renders as a plain notice.
                "error": None,
                "plan": settle_in_flight_steps(list(run.get("plan") or []), "failed"),
                "steps": settle_in_flight_steps(list(run.get("steps") or []), "failed"),
            },
        )
    except TransitionConflict:
        return
    except Exception:
        logger.exception("failed to settle cancelled automation run %s", run_id)
        return
    automation_id = str(stopped.get("automation_id") or "")
    automation = AutomationStore().find_by_id(automation_id) if automation_id else None
    if automation is not None:
        # count_run=False: you stopped it, so it does not consume a budgeted run. The call
        # is still needed to reschedule `next_run_at`, which nothing else does.
        _finish_automation_once(
            automation, stopped, "cancelled", _lifecycle_payload(stopped), count_run=False
        )
    if automation_id:
        _release_runtime(automation_id, run_id)


async def _execute_run(automation: dict, run: dict, payload: dict) -> None:
    run_store = AutomationRunStore()
    result: AutomationRunnerResult
    try:
        if automation.get("execution", {}).get("kind") != "lucius":
            raise ValidationError("automation execution must use Lucius")
        request = request_for_run(automation, run, payload=payload)

        async def persist(event: dict) -> None:
            if event.get("type") == "automation_progress":
                persist_automation_progress(run["id"], event.get("patch") or {})

        result = await run_with_automation_runner(request, persist)
    except Exception as exc:
        result = AutomationRunnerResult(
            status="failed",
            session_id=run.get("session_id") or "",
            summary="Execution failed",
            error=str(exc)[:2000],
        )
    try:
        updated, _ = run_store.transition(
            run["id"], {"running"}, result.status, _result_patch(run, result, continuation=False)
        )
    except TransitionConflict:
        return
    if result.status != "waiting":
        _finish_automation_once(automation, updated, result.status, payload)
    # Released on EVERY outcome, ``waiting`` included. The runtime claim means "a run of this
    # Automation is EXECUTING", not "a run of it exists": a waiting run is parked on an
    # approval card doing nothing, and holding the slot for as long as the card goes untapped
    # freezes the schedule behind it. Live incident 2026-08-04: "Daily Trades Summary" parked
    # on a notify approval and every slot for the next five days was skipped as `waiting`,
    # with a manual run answering "automation is already running". Overlap protection is
    # unaffected — _claim_runtime still admits one EXECUTING run, and the continuation
    # re-acquires the claim before it resumes.
    _release_runtime(automation["id"], run["id"])


def request_for_run(
    automation: dict, run: dict, *, payload: Optional[dict] = None
) -> AutomationRunnerRequest:
    # The Automation's standing ``always_allowed`` list is the whole grant surface. The
    # per-conversation tier beside it was removed on 2026-08-02; a session_allowed_tools key
    # left on disk by the old code is deliberately NOT read, so it can never widen a run.
    always_allowed = [
        dict(item) for item in automation.get("always_allowed") or [] if isinstance(item, dict)
    ]
    return AutomationRunnerRequest(
        automation_id=automation["id"],
        run_id=run["id"],
        session_id=run["session_id"],
        title=automation.get("name") or automation["id"],
        instructions=automation.get("instructions") or "",
        trigger=run.get("trigger") or {},
        execution=automation.get("execution") or {},
        model=automation.get("model") or run.get("model") or "",
        effort=automation.get("effort") or run.get("effort") or "medium",
        payload=dict(payload if payload is not None else run.get("context") or {}),
        permission_mode=automation.get("permission_mode") or "manual",
        always_allowed=always_allowed,
        fetch_hosts=list(automation.get("fetch_hosts") or []),
        configured_trigger=configured_trigger(automation),
        last_completed_run=last_completed_run(automation["id"], run),
    )


def automation_run_for_session(session_id: str) -> tuple[dict, dict] | None:
    run = AutomationRunStore().find_by_session_id(session_id)
    if run is None:
        return None
    automation = AutomationStore().find_by_id(run.get("automation_id"))
    if automation is None:
        return None
    return dict(automation), dict(run)


def begin_automation_continuation(session_id: str) -> tuple[dict, dict]:
    owned = automation_run_for_session(session_id)
    if owned is None:
        raise NotFoundError("automation conversation not found")
    automation, run = owned
    if run.get("status") == "running":
        raise ConflictError("automation conversation is already processing a turn")
    runtime = AutomationRuntimeStore().get_for_automation(automation["id"]) or {}
    if run.get("status") in _TERMINAL_STATUSES and runtime.get("running_run_id") == run["id"]:
        raise ConflictError("the previous Automation turn is still finalizing")
    claimed, acquired = _claim_runtime(automation["id"], run["id"])
    if not claimed:
        raise ConflictError(f"automation {automation['id']!r} is already running")
    latest = AutomationStore().find_persisted_by_id(automation["id"])
    if latest is None or latest.get("status") == DELETING_STATUS:
        _rollback_runtime_claim(automation["id"], run["id"], acquired=acquired)
        raise NotFoundError("automation conversation not found")
    automation = latest
    try:
        started, _ = AutomationRunStore().transition(
            run["id"],
            set(_RESUMABLE_STATUSES),
            "running",
            {"error": None, "finished_at": None},
        )
    except TransitionConflict as exc:
        _rollback_runtime_claim(automation["id"], run["id"], acquired=acquired)
        raise ConflictError("automation conversation is already processing a turn") from exc
    except BaseException:
        _rollback_runtime_claim(automation["id"], run["id"], acquired=acquired)
        raise
    return automation, started


def persist_automation_progress(run_id: str, patch: dict) -> dict | None:
    """Write mid-run progress. Only lands while the run is RUNNING.

    The moment a run parks on an approval it becomes ``waiting``, and every progress patch
    is then rejected by the expected-status guard. That was silent — the checklist simply
    stopped moving with nothing said anywhere, which is indistinguishable from a hung run
    (owner: "0 of 6 while six update_run_task chips fired"). Still a no-op, because writing
    progress onto a parked run would race its resumption, but no longer an invisible one.
    """
    try:
        return AutomationRunStore().patch(run_id, {"running"}, patch)
    except TransitionConflict:
        logger.info(
            "progress patch dropped for run %s — run is not 'running' (parked or finished); "
            "the checklist will not advance until it resumes",
            run_id,
        )
        return None


def reconcile_automation_continuation(
    automation: dict,
    baseline: dict,
    result: AutomationRunnerResult,
) -> dict | None:
    try:
        updated, _ = AutomationRunStore().transition(
            baseline["id"],
            {"running"},
            result.status,
            _result_patch(baseline, result, continuation=True),
        )
    except TransitionConflict:
        return None
    if result.status != "waiting":
        _finish_automation_once(
            automation,
            updated,
            result.status,
            dict((baseline.get("context") or {}).get("payload") or {}),
            count_run=result.status != "cancelled",
        )
    # Same rule as _execute_run: parking on a second approval must not re-freeze the schedule.
    _release_runtime(automation["id"], baseline["id"])
    return updated


def _result_patch(baseline: dict, result: AutomationRunnerResult, *, continuation: bool) -> dict:
    patch = result.to_patch()
    if not continuation:
        return patch
    if not patch.get("summary"):
        patch["summary"] = baseline.get("summary") or ""
    patch["steps"] = _merge_steps(list(baseline.get("steps") or []), list(result.steps or []))
    # The plan is a WHOLE-LIST value, not an append-only trail: the continuation re-seeds
    # it from the run row and the model restates it, so the latest version is the truth.
    # Merging would resurrect steps the run has since dropped from its plan.
    patch["plan"] = list(result.plan or []) or list(baseline.get("plan") or [])
    patch["context"] = {**(baseline.get("context") or {}), **(result.context or {})}
    patch["files"] = _merge_files(list(baseline.get("files") or []), list(result.files))
    patch["stats"] = {**(baseline.get("stats") or {}), **(result.stats or {})}
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
    ):
        value = getattr(result, key)
        patch[key] = int(baseline.get(key) or 0) + int(value or 0)
    patch["cost_usd"] = float(baseline.get("cost_usd") or 0) + float(result.cost_usd or 0)
    return patch


def _merge_files(existing: list, current: list) -> list:
    result: list = []
    seen: set[str] = set()
    for value in [*existing, *current]:
        if isinstance(value, dict):
            key = json.dumps(value, sort_keys=True, default=str)
        else:
            key = str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _merge_steps(existing: list[dict], current: list[dict]) -> list[dict]:
    result = [dict(step) for step in existing]
    by_tool_id = {
        str(step.get("tool_use_id")): index
        for index, step in enumerate(result)
        if step.get("tool_use_id")
    }
    for step in current:
        item = dict(step)
        tool_id = str(item.get("tool_use_id") or "")
        if tool_id and tool_id in by_tool_id:
            index = by_tool_id[tool_id]
            result[index] = {**result[index], **item}
        else:
            if tool_id:
                by_tool_id[tool_id] = len(result)
            result.append(item)
    return result


# ``fetch_page`` and every ``<provider>_fetch_page`` (``ai/tools/_fetch_gate``) — named here because ``services`` cannot import ``ai``.
_FETCH_TOOL = "fetch_page"


def _is_fetch_tool(name: str) -> bool:
    return name == _FETCH_TOOL or name.endswith("_" + _FETCH_TOOL)


def _allow_pending_fetch_host(
    automation: dict, session_id: str, tool: str, tool_use_id: Optional[str]
) -> bool:
    """*Always* on a fetch card means "always THIS website", never "every website": the card
    is still awaiting when the grant lands (the client saves the grant before it confirms), so
    its URL names the host to add to ``fetch_hosts``. Only the TAPPED card's host — a second
    open fetch card in the same run is not consented to by this tap. False when that card
    cannot be identified; the caller then falls back to the whole-tool grant the owner tapped."""
    from backend.core.urls import normalize_host
    from backend.db.pending_plan_store import PendingPlanStore

    cards = [
        entry
        for entry in PendingPlanStore().list_awaiting()
        if entry.get("session_id") == session_id
        and entry.get("tool") == tool
        and (tool_use_id is None or entry.get("tool_use_id") == tool_use_id)
    ]
    if len(cards) != 1:
        return False
    host = normalize_host(str((cards[0].get("args") or {}).get("url") or ""))
    if not host:
        return False
    current = list(automation.get("fetch_hosts") or [])
    if host not in current:
        from .service import apply_automation_change

        apply_automation_change(
            {"op": "update", "id": automation["id"], "changes": {"fetch_hosts": current + [host]}}
        )
    return True


def grant_automation_tool(
    session_id: str, tool: str, scope: str, *, tool_use_id: Optional[str] = None
) -> bool:
    """Persist a grant locally when ``session_id`` belongs to an Automation run.

    Two scopes (2026-08-02): ``once`` stores nothing — approving the card IS the grant — and
    ``always`` appends to this Automation's standing ``always_allowed`` list, so the tool stops
    raising a card for it. Keeping the grant on the Automation rather than in the global
    table is what stops an Automation inheriting a permission granted in an unrelated chat.
    A fetch tool (``fetch_page``, ``<provider>_fetch_page``) is the one exception to the whole-tool grant: see
    ``_allow_pending_fetch_host``.
    """
    owned = automation_run_for_session(session_id)
    if owned is None:
        return False
    automation, _run = owned
    if scope == "always" and _is_fetch_tool(tool):
        if _allow_pending_fetch_host(automation, session_id, tool, tool_use_id):
            return True
    if scope == "always":
        always_allowed = [
            dict(item) for item in automation.get("always_allowed") or [] if isinstance(item, dict)
        ]
        if not any(tool in (item.get("tools") or []) for item in always_allowed):
            always_allowed.append(
                {
                    "src": "Open Net Worth",
                    "desc": f"Allow {tool.replace('_', ' ')}",
                    "tools": [tool],
                }
            )
            from .service import apply_automation_change

            apply_automation_change(
                {
                    "op": "update",
                    "id": automation["id"],
                    "changes": {"always_allowed": always_allowed},
                }
            )
        return True
    return scope == "once"


def assert_automation_delete_safe(automation_id: str) -> None:
    """Refuse deletion while this process still owns an executing Lucius turn."""
    for run in AutomationRunStore().for_automation(automation_id, limit=1_000_000, offset=0):
        task = _tasks_by_run.get(run["id"])
        live_task = task is not None and not task.done()
        if live_task or run.get("status") == "running":
            raise ConflictError(
                "automation is currently running; wait for the turn to finish before deleting it"
            )


def _claim_runtime(automation_id: str, run_id: str) -> tuple[bool, bool]:
    """Claim ownership, replacing only an owner proven stale by the run store."""
    store = AutomationRuntimeStore()
    runtime = store.get_for_automation(automation_id) or {}
    owner = runtime.get("running_run_id")
    reclaim_from = None
    if owner:
        owner_run = AutomationRunStore().find_by_id(owner)
        if owner_run is not None:
            finalized = bool((owner_run.get("stats") or {}).get("lifecycle_finalized"))
            status = owner_run.get("status")
            if status in _OWNED_STATUSES:
                return (True, False) if owner == run_id else (False, False)
            if not finalized and status in {"completed", "failed"}:
                automation = AutomationStore().find_by_id(automation_id)
                if automation is not None:
                    try:
                        _finish_automation_once(
                            automation,
                            owner_run,
                            status,
                            _lifecycle_payload(owner_run),
                        )
                    except Exception:
                        logger.exception(
                            "automation lifecycle retry failed before claim: %s", owner
                        )
                        return False, False
                    owner_run = AutomationRunStore().find_by_id(owner) or owner_run
                    finalized = bool((owner_run.get("stats") or {}).get("lifecycle_finalized"))
            if not finalized:
                return False, False
        if owner == run_id:
            return store.claim_run_with_result(automation_id, run_id)
        reclaim_from = owner
    return store.claim_run_with_result(automation_id, run_id, reclaim_from=reclaim_from)


def _rollback_runtime_claim(automation_id: str, run_id: str, *, acquired: bool) -> None:
    if not acquired:
        return
    latest = AutomationRunStore().find_by_id(run_id)
    if latest is not None and latest.get("status") == "running":
        return
    _release_runtime(automation_id, run_id)


def _release_runtime(automation_id: str, run_id: str) -> None:
    AutomationRuntimeStore().release_run(automation_id, run_id)


def _sweep_orphan_runtime_owners(run_store: AutomationRunStore) -> None:
    runtime_store = AutomationRuntimeStore()
    for automation_id, run_id in runtime_store.run_owners():
        if run_store.find_by_id(run_id) is None:
            runtime_store.release_run(automation_id, run_id)


def _lifecycle_payload(run: dict) -> dict:
    context = run.get("context") or {}
    if not isinstance(context, dict):
        context = {}
    nested = context.get("payload")
    payload = dict(nested) if isinstance(nested, dict) else dict(context)
    payload.pop("trigger", None)
    trigger = run.get("trigger") or {}
    if isinstance(trigger, dict) and trigger.get("scheduled_for"):
        payload.setdefault("scheduled_for", trigger["scheduled_for"])
    return payload


def ensure_run_session(
    run: dict,
    automation: Optional[dict] = None,
    *,
    materialize_session: Optional[Callable[[dict, Optional[dict]], str]] = None,
) -> dict:
    # A row that already carries its session_id needs NOTHING here. Materializing anyway
    # meant loading that run's whole conversation transcript off disk just to discover it
    # exists — and list_runs does this for every row while the UI polls it once a second, so
    # a 50-run history read 50 transcripts per second on the event loop. Measured at ~300ms
    # of pure compute per poll (db=0ms), which is ~30% of a single-threaded server spent on
    # one panel; everything else queued behind it and the app stopped responding
    # (live 2026-08-11, owner had to kill the process). Minting stays on the paths that
    # actually need it: starting a run, opening one, and boot recovery.
    if run.get("session_id"):
        return run
    materialize = materialize_session or materialize_automation_session
    session_id = materialize(run, automation)
    if run.get("session_id") == session_id:
        return run
    try:
        return AutomationRunStore().patch(run["id"], set(_ALL_STATUSES), {"session_id": session_id})
    except TransitionConflict:
        latest = AutomationRunStore().find_by_id(run["id"])
        return latest or run


def recover_automation_runs(
    *,
    materialize_session: Optional[Callable[[dict, Optional[dict]], str]] = None,
    pending_for_session: Optional[Callable[[str], list[str]]] = None,
) -> dict:
    """Idempotently backfill conversations and settle work no process can still own."""
    store = AutomationRunStore()
    run_rows = store.all()
    AutomationRuntimeStore().reconcile_event_batches(
        (row["automation_id"], row["id"])
        for row in run_rows
        if isinstance(row.get("automation_id"), str) and isinstance(row.get("id"), str)
    )
    _sweep_orphan_runtime_owners(store)
    automations = {row["id"]: row for row in AutomationStore().all()}
    backfilled = 0
    recovered = 0
    waiting_runs: list[dict] = []
    for original in run_rows:
        automation = automations.get(original.get("automation_id"))
        was_missing = not original.get("session_id")
        run = ensure_run_session(original, automation, materialize_session=materialize_session)
        if was_missing and run.get("session_id"):
            backfilled += 1
        status = run.get("status")
        pending = pending_for_session(run.get("session_id") or "") if pending_for_session else None
        stale = status == "running" or (status == "waiting" and pending == [])
        if stale:
            reason = (
                "The server restarted while this Automation was running; the interrupted "
                "turn was closed safely. No unconfirmed action was applied."
                if status == "running"
                else "This Automation was waiting for approvals that no longer exist; it was closed safely."
            )
            try:
                failed, _ = store.transition(
                    run["id"],
                    {status},
                    "failed",
                    {"error": reason, "summary": run.get("summary") or "Interrupted"},
                )
            except TransitionConflict:
                continue
            if automation is not None:
                _finish_automation_once(automation, failed, "failed", _lifecycle_payload(failed))
            _release_runtime(run.get("automation_id") or "", run["id"])
            recovered += 1
        elif status == "waiting":
            waiting_runs.append(run)
        elif status in {"completed", "failed"}:
            if automation is not None:
                _finish_automation_once(automation, run, status, _lifecycle_payload(run))
            _release_runtime(run.get("automation_id") or "", run["id"])
    # Waiting runs deliberately hold NO runtime claim (see _execute_run) — they are parked on
    # an approval card, not executing, and a claim would freeze the schedule behind them until
    # someone taps it. Boot used to re-claim them here, which would have restored exactly the
    # freeze on every restart, so release instead: whatever a crashed process left behind, a
    # run that comes back up `waiting` owns no execution slot.
    for run in waiting_runs:
        latest = store.find_by_id(run["id"])
        if latest is not None and latest.get("status") == "waiting":
            _release_runtime(run.get("automation_id") or "", run["id"])
    _sweep_orphan_runtime_owners(store)
    return {"backfilled": backfilled, "recovered": recovered}


def list_runs(automation_id: str, *, limit: int, offset: int) -> list[dict]:
    automation = AutomationStore().find_by_id(automation_id)
    return [
        ensure_run_session(row, automation)
        for row in AutomationRunStore().for_automation(automation_id, limit, offset)
    ]


def get_run(automation_id: str, run_id: str) -> dict | None:
    run = AutomationRunStore().find_by_id(run_id)
    if run is None or run.get("automation_id") != automation_id:
        return None
    return ensure_run_session(run, AutomationStore().find_by_id(automation_id))


def _finish_automation_once(
    automation: dict, run: dict, status: str, payload: dict, *, count_run: bool = True
) -> None:
    run_stats = dict(run.get("stats") or {})
    if run_stats.get("lifecycle_finalized"):
        return
    store = AutomationStore()
    current = store.find_by_id(automation["id"])
    if current is None:
        return
    automation_stats = dict(current.get("stats") or {})
    if automation_stats.get("last_finalized_run_id") == run["id"]:
        try:
            AutomationRunStore().patch(
                run["id"],
                set(_TERMINAL_STATUSES),
                {"stats": {**run_stats, "lifecycle_finalized": True}},
            )
        except TransitionConflict:
            pass
        return
    finished = _run_finished_at(run)
    prior_count = int(current.get("run_count") or 0)
    run_count = prior_count + 1 if count_run else prior_count
    patch = {
        "run_count": run_count,
        "last_run_at": finished.isoformat(),
        "last_status": status,
        "updated_at": finished.isoformat(),
        "stats": {**automation_stats, "last_finalized_run_id": run["id"]},
    }
    trigger = current.get("trigger") or {}
    if trigger.get("kind") == "schedule":
        scheduled_for = payload.get("scheduled_for")
        after = datetime.fromisoformat(scheduled_for) if scheduled_for else finished
        # ``after`` anchors the cadence grid (so a daily 09:00 job does not drift later by
        # each run's duration); ``not_before`` skips slots that are already gone. Without
        # the floor, a run that finishes days after it was scheduled advances by ONE
        # occurrence into a still-past next_run_at, and the scheduler replays the backlog
        # one paid, notifying run per missed day. See compute_next_run's docstring.
        #
        # The floor is the CLOCK SEAM, not the run's finished_at: a store-stamped timestamp
        # is real wall-clock even when the caller is driving a simulated now, which would
        # make the reschedule untestable and, on the recovery path, floor against when the
        # crashed run happened to stop rather than when we are actually rescheduling.
        # Imported locally because service imports runs lazily in the other direction.
        #
        # The slot that just ran is a floor too, placed on the CURRENT clock: a tz-less
        # trigger whose machine moved west would otherwise get the same slot back an hour
        # later and run it twice (see ran_slot_floor).
        from .service import now_utc

        not_before = now_utc()
        if scheduled_for:
            not_before = max(not_before, ran_slot_floor(trigger, after))
        next_run = compute_next_run(
            trigger,
            after,
            created_at=current.get("created_at"),
            not_before=not_before,
        )
        reached_cap = current.get("max_runs") is not None and run_count >= int(current["max_runs"])
        if next_run is None or reached_cap:
            # The schedule ran out — ``completed``, never ``paused`` (the user's own choice).
            patch.update({"status": "completed", "next_run_at": None})
        else:
            patch["next_run_at"] = next_run
    else:
        patch["next_run_at"] = None
    store.update_by_id(current["id"], patch)
    try:
        AutomationRunStore().patch(
            run["id"],
            {"completed", "failed"},
            {"stats": {**run_stats, "lifecycle_finalized": True}},
        )
    except TransitionConflict:
        pass


def _run_finished_at(run: dict) -> datetime:
    raw = run.get("finished_at")
    if isinstance(raw, str) and raw:
        try:
            finished = datetime.fromisoformat(raw)
            return (
                finished if finished.tzinfo is not None else finished.replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass
    return datetime.now(timezone.utc)


async def run_due_automations(now: datetime | None = None) -> dict:
    """Fire every Automation whose ``next_run_at`` has passed.

    An Automation already holding its runtime claim is skipped rather than attempted: its
    current run is either genuinely still executing, or PARKED waiting on the owner's
    approval — either way `run_automation` is guaranteed to raise "already running", and
    `next_run_at` cannot advance until that run settles (`_finish_automation_once` is the
    only thing that recomputes it), so a stale-in-the-past `next_run_at` makes the
    Automation "due" on literally every tick forever. Attempting it anyway turned a normal,
    already-visible state (the run row reads `waiting`, "Needs you" in the Automations tab)
    into a manufactured tick failure, logged as a scheduler error every 60 seconds — live
    incident: `task-cbcf7a0c` ("Daily Trades Summary") sat on a `notify` approval card for 5
    days, spamming a failed retry every minute the whole time. Skipped automations are
    reported separately from `errors` so a parked approval never reads as one.
    """
    now = now or datetime.now(timezone.utc)
    due = AutomationStore().due(now.isoformat())
    runtime_store = AutomationRuntimeStore()
    results = []
    ran = 0
    errors = 0
    waiting = 0
    for automation in due:
        claim = runtime_store.get_for_automation(automation["id"]) or {}
        if claim.get("running_run_id"):
            waiting += 1
            results.append({"id": automation["id"], "waiting": True})
            continue
        ran += 1
        try:
            scheduled_for = automation.get("next_run_at")
            run = await run_automation(
                automation["id"],
                trigger={"kind": "schedule", "scheduled_for": scheduled_for},
                payload={"scheduled_for": scheduled_for},
            )
            results.append({"id": automation["id"], "run_id": run["id"]})
        except Exception as exc:
            errors += 1
            results.append({"id": automation.get("id"), "error": str(exc)})
    return {"ran": ran, "errors": errors, "waiting": waiting, "results": results}

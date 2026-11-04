"""One reviewed delegation contract for conversational and expert workspace creation."""

from backend.core.errors import ConflictError, OpenNetWorthError
from backend.core.ids import new_id
from backend.core.report_redact import redact_text
from backend.db.trading_store import TradingStore, trading_transaction
from backend.models.trading import (
    DelegationActivation,
    DelegationCreate,
    MandateAmendment,
    WorkspaceCreate,
)
from backend.services.trading.common import event, fingerprint, now, validated
from backend.services.trading.lifecycle import apply_run_action
from backend.services.trading.workspaces import (
    create_run,
    create_workspace,
    public_run,
    update_runtime,
)


def executor_capabilities(workspace):
    kind = workspace["agent_kind"]
    managed = kind in {"lucius", "script"}
    return {
        "kind": kind,
        "label": workspace["agent_label"],
        "managed": managed,
        "topology": "Executor-defined",
        "capabilities": {
            "automatic_wake": managed,
            "persistent_session": kind == "lucius",
            "workspace_files": True,
            "tool_trace": True,
            "reasoning": "provider-dependent" if kind == "lucius" else "executor-reported",
            "child_activity": "executor-reported",
            "external_events": True,
        },
    }


def review_delegation(data):
    clean = validated(DelegationCreate, data)
    issues = []
    if clean["environment"]["mode"] == "live" and not clean["environment"]["live_authorized"]:
        issues.append(
            "Explicit account-specific human authorization is required to start live trading."
        )
    if clean["workspace"]["agent_kind"] == "script" and not clean["workspace"].get(
        "strategy_source"
    ):
        issues.append(
            "The executor must provide strategy code before automatic script work can start."
        )
    return {
        **clean,
        "ready": not issues,
        "issues": issues,
        "executor": executor_capabilities(clean["workspace"]),
        "review_notice": "Starting real trading requires verified account authority and execution prerequisites.",
    }


async def create_delegation(data):
    review = review_delegation(data)
    with trading_transaction() as conn:
        workspace = create_workspace(review["workspace"], conn=conn)
        run = create_run(workspace["id"], review["environment"], conn=conn)
    result = {
        "workspace": TradingStore("workspaces").required(workspace["id"]),
        "run": run,
        "started": False,
    }
    if review["start"]:
        try:
            result["run"] = await apply_run_action(run["id"], {"action": "start"})
            result["started"] = True
        except OpenNetWorthError as exc:
            result["activation_error"] = {"code": exc.code, "message": redact_text(exc.message)}
            result["run"] = update_runtime(
                run["id"], {"last_agent_error": redact_text(exc.message), "agent_state": "blocked"}
            )
    return result


async def activate_delegation(workspace_id, data):
    clean = validated(DelegationActivation, data)
    with trading_transaction() as conn:
        workspace = TradingStore("workspaces").required(workspace_id, conn=conn)
        if workspace["revision"] != clean["expected_revision"]:
            raise ConflictError("Mandate changed; review the latest revision before activating")
        runs = TradingStore("runs").list_scope(workspace_id, conn=conn)
        if any(run["state"] not in {"stopped", "completed", "failed"} for run in runs):
            raise ConflictError(
                "This workspace already has an unfinished environment; resume or end it first"
            )
        run = create_run(workspace_id, clean["environment"], conn=conn)
    result = {
        "workspace": TradingStore("workspaces").required(workspace_id),
        "run": run,
        "started": False,
    }
    if clean["start"]:
        try:
            result["run"] = await apply_run_action(run["id"], {"action": "start"})
            result["started"] = True
        except OpenNetWorthError as exc:
            result["activation_error"] = {"code": exc.code, "message": redact_text(exc.message)}
            result["run"] = update_runtime(
                run["id"], {"last_agent_error": redact_text(exc.message), "agent_state": "blocked"}
            )
    return result


def workspace_overview(workspace_id):
    workspace = TradingStore("workspaces").required(workspace_id)
    runs = TradingStore("runs").list_scope(workspace_id)
    current = next((r for r in runs if r["id"] == workspace.get("current_run_id")), None)
    if current is None and runs:
        current = runs[-1]
    events = TradingStore("events").list_scope(current["id"]) if current else []
    decisions = [r for r in events if r["kind"] in {"agent_decision", "agent_summary"}]
    summary = decisions[-1]["payload"].get("summary") if decisions else None
    state = _work_state(current)
    return {
        "workspace": workspace,
        "current_run": public_run(current) if current else None,
        "original_mandate": workspace.get("original_mandate"),
        "original_availability": "recorded"
        if workspace.get("original_mandate")
        else "not_recorded",
        "amendments": workspace.get("amendments", []),
        "executor": executor_capabilities(workspace),
        "current_work": {
            "state": state,
            "summary": ((current or {}).get("current_task") if state == "working" else None)
            or summary,
            "last_activity_at": events[-1]["created_at"] if events else None,
            "next_wake_at": (current or {}).get("next_wake_at"),
            "blocker": (current or {}).get("environment_error")
            or (current or {}).get("last_agent_error"),
            "executor": workspace["agent_label"],
        },
        "visibility": {
            "reasoning": (current or {}).get("reasoning_availability", "not_provided"),
            "legacy_trace": "Earlier events may not contain full tool arguments or results.",
            "child_activity": "Only activity reported by the executor is available.",
        },
    }


def _work_state(run):
    if run is None:
        return "not_started"
    if run["state"] != "running":
        if run["state"] == "ready" and run.get("last_agent_error"):
            return "blocked"
        return {
            "ready": "not_started",
            "completed": "ended",
            "stopped": "ended",
            "needs_reconciliation": "blocked",
            "stopping": "ending",
        }.get(run["state"], run["state"])
    state = run.get("agent_state", "idle")
    if run.get("environment_error"):
        return "blocked"
    if run.get("usage_state") == "unknown":
        return "blocked"
    if state in {"failed", "usage_unknown", "budget_exhausted"}:
        return "blocked"
    if run["workspace_snapshot"]["agent_kind"] not in {"lucius", "script"}:
        return "external_status_unknown"
    if state == "running":
        return "working"
    if run["workspace_snapshot"]["agent_kind"] == "script" and not run.get("agent_script_source"):
        return "awaiting_executor"
    return "waiting"


def amend_mandate(workspace_id, data):
    clean = validated(MandateAmendment, data)
    with trading_transaction() as conn:
        store = TradingStore("workspaces")
        workspace = store.required(workspace_id, conn=conn)
        if workspace["revision"] != clean["expected_revision"]:
            raise ConflictError("Mandate changed; review the latest revision before amending")
        runs = TradingStore("runs").list_scope(workspace_id, conn=conn)
        active = [r for r in runs if r["state"] not in {"stopped", "completed", "failed"}]
        if any(r["state"] not in {"paused", "ready"} or r.get("agent_lease_id") for r in active):
            raise ConflictError(
                "Pause the workspace and settle its current work before amending the mandate"
            )
        before = {key: workspace.get(key) for key in WorkspaceCreate.model_fields}
        after = {
            **before,
            **{key: clean[key] for key in ("instructions", "mandate") if clean[key] is not None},
        }
        revision = workspace["revision"] + 1
        amendment = {
            "id": new_id("tam"),
            "revision": revision,
            "author": "user",
            "created_at": now(),
            "effective_at": now(),
            "reason": clean["reason"],
            "before": before,
            "after": after,
        }
        updated = store.update_by_id(
            workspace_id,
            {
                **after,
                "revision": revision,
                "updated_at": now(),
                "amendments": [*workspace.get("amendments", []), amendment],
            },
            conn=conn,
        )
        for run in active:
            history = [
                *run.get("mandate_history", []),
                {
                    "revision": revision,
                    "environment_time": run["current_time"],
                    "instructions": after["instructions"],
                    "mandate": after["mandate"],
                },
            ]
            initial_hash = run.get("initial_scenario_hash", run["scenario_hash"])
            run = TradingStore("runs").update_by_id(
                run["id"],
                {
                    "initial_workspace_snapshot": run.get(
                        "initial_workspace_snapshot", run["workspace_snapshot"]
                    ),
                    "workspace_snapshot": updated,
                    "mandate_revision": revision,
                    "initial_scenario_hash": initial_hash,
                    "mandate_history": history,
                    "scenario_hash": fingerprint(
                        {"initial_scenario": initial_hash, "mandate_history": history}
                    ),
                },
                conn=conn,
            )
            event(conn, run, "mandate_amended", amendment)
    return {
        "workspace": updated,
        "amendment": amendment,
        "current_run": workspace_overview(workspace_id)["current_run"],
    }


async def workspace_action(workspace_id, data):
    overview = workspace_overview(workspace_id)
    if overview["current_run"] is None:
        raise ConflictError("Review the workspace's environment and allocation before starting")
    return await apply_run_action(overview["current_run"]["id"], data)

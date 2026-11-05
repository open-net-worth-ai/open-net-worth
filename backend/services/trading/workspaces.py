"""Workspace definitions and immutable trading runs."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import timedelta

from backend.core.errors import ConflictError, ValidationError
from backend.core.ids import new_id
from backend.db.trading_store import TradingStore, trading_transaction
from backend.models.trading import RunCreate, WorkspaceCreate
from backend.services.trading.common import (
    amount,
    collection,
    event,
    fingerprint,
    instant,
    now,
    uses_broker_execution,
    validated,
)


def list_workspaces(*, limit=100, offset=0):
    return collection(TradingStore("workspaces").all(), limit=limit, offset=offset)


def get_workspace(workspace_id):
    return TradingStore("workspaces").required(workspace_id)


def create_workspace(data, *, conn=None):
    clean = validated(WorkspaceCreate, data)
    row = {
        **clean,
        "id": new_id("tw"),
        "scope_id": "",
        "revision": 1,
        "created_at": now(),
        "updated_at": now(),
        "original_mandate": {**clean, "revision": 1, "created_at": now()},
        "amendments": [],
    }
    with nullcontext(conn) if conn is not None else trading_transaction() as conn:
        TradingStore("workspaces").insert(row, conn=conn)
        from backend.core.data_events import publish
        from backend.db.engine import on_commit

        on_commit(lambda: publish(("trading",)))
    return row


def update_workspace(workspace_id, changes):
    with trading_transaction() as conn:
        store = TradingStore("workspaces")
        current = store.required(workspace_id, conn=conn)
        clean = validated(
            WorkspaceCreate,
            {key: changes.get(key, current.get(key)) for key in WorkspaceCreate.model_fields},
        )
        unknown = set(changes) - set(WorkspaceCreate.model_fields)
        if unknown:
            raise ValidationError("Unknown workspace fields", fields=sorted(unknown))
        active = [
            r
            for r in TradingStore("runs").list_scope(workspace_id, conn=conn)
            if r["state"] in {"running", "paused", "needs_reconciliation", "stopping"}
        ]
        if active:
            raise ConflictError(
                "Amend the workspace mandate instead of editing an active definition"
            )
        stamp = now()
        amendment = {
            "id": new_id("tam"),
            "revision": current["revision"] + 1,
            "author": "user",
            "created_at": stamp,
            "reason": "Workspace definition edited",
            "before": {key: current.get(key) for key in WorkspaceCreate.model_fields},
            "after": clean,
        }
        return store.update_by_id(
            workspace_id,
            {
                **clean,
                "revision": current["revision"] + 1,
                "updated_at": stamp,
                "amendments": [*current.get("amendments", []), amendment],
            },
            conn=conn,
        )


def create_run(workspace_id, data, *, conn=None):
    clean = validated(RunCreate, data)
    external_execution = _execution_source(clean)
    _validate_observations(clean)
    with nullcontext(conn) if conn is not None else trading_transaction() as conn:
        workspace = TradingStore("workspaces").required(workspace_id, conn=conn)
        run = {
            **clean,
            "id": new_id("trun"),
            "scope_id": workspace_id,
            "workspace_id": workspace_id,
            "workspace_snapshot": workspace,
            "initial_workspace_snapshot": workspace,
            "mandate_revision": workspace["revision"],
            "state": "ready",
            "current_time": clean["start_at"],
            "clock_cursor": 0,
            "created_at": now(),
            "updated_at": now(),
            "model_cost_usd": "0",
            "agent_state": "idle",
            "agent_script_source": workspace.get("strategy_source")
            if workspace["agent_kind"] == "script"
            else None,
            "initial_capital": {},
            "external_execution": external_execution,
            "valuation_history": [],
        }
        for allocation in clean["accounts"]:
            ccy = allocation["currency"]
            if ccy not in clean["fx_rates"]:
                raise ValidationError("Missing allocation currency FX rate", currency=ccy)
            run["initial_capital"][ccy] = str(
                amount(run["initial_capital"].get(ccy, "0")) + amount(allocation["capital"])
            )
            account = {
                **allocation,
                "id": new_id("tacc"),
                "run_id": run["id"],
                "scope_id": run["id"],
                "cash": allocation["capital"],
                "positions": {},
                "reconciliation_status": "unverified"
                if uses_broker_execution(run)
                else "simulated",
            }
            TradingStore("accounts").insert(account, conn=conn)
        scenario = {
            key: clean[key]
            for key in (
                "mode",
                "start_at",
                "end_at",
                "observations",
                "data_version",
                "base_currency",
                "fx_rates",
            )
        }
        scenario["allocations"] = sorted(
            [
                {key: account[key] for key in ("currency", "capital", "venue")}
                for account in clean["accounts"]
            ],
            key=lambda row: (row["venue"], row["currency"], row["capital"]),
        )
        scenario["mandate"] = workspace["mandate"]
        scenario["external_execution"] = external_execution
        run["scenario_hash"] = fingerprint(scenario)
        TradingStore("runs").insert(run, conn=conn)
        TradingStore("workspaces").update_by_id(
            workspace_id, {"current_run_id": run["id"]}, conn=conn
        )
        event(conn, run, "run_created", {"mode": run["mode"]})
    return public_run(run)


def _execution_source(clean):
    native = [a for a in clean["accounts"] if a.get("connector_id") == "binance"]
    if native:
        raise ConflictError("Binance execution is not available until its integration is installed")
    return None


def _validate_observations(data):
    if data["mode"] == "backtest" and not data["observations"]:
        raise ValidationError("Backtests require a versioned observation dataset")
    identities = set()
    for observation in data["observations"]:
        observation_id = fingerprint(
            {"instrument": observation["instrument"], "timestamp": observation["timestamp"]}
        )
        if observation_id in identities:
            raise ValidationError("Dataset contains duplicate instrument observation timestamps")
        identities.add(observation_id)
        stamp = instant(observation["timestamp"])
        available = instant(observation["available_at"] or observation["timestamp"])
        if available < stamp:
            raise ValidationError("Observation cannot be available before its timestamp")
        if observation["instrument"]["currency"] not in data["fx_rates"]:
            raise ValidationError("Missing observation currency FX rate")
    data["observations"].sort(
        key=lambda row: (
            instant(row["available_at"] or row["timestamp"]),
            instant(row["timestamp"]),
        )
    )


def public_run(run):
    return {key: value for key, value in run.items() if key != "observations"}


def get_run(run_id):
    return public_run(TradingStore("runs").required(run_id))


def list_runs(workspace_id=None, *, limit=100, offset=0):
    return collection(
        [public_run(run) for run in TradingStore("runs").list_scope(workspace_id)],
        limit=limit,
        offset=offset,
    )


def list_active_runs():
    return [
        public_run(run)
        for run in TradingStore("runs").all()
        if run["state"] in {"running", "stopping", "needs_reconciliation"}
    ]


def update_runtime(run_id, patch):
    allowed = {
        "agent_session_id",
        "turn_id",
        "next_wake_at",
        "last_agent_error",
        "agent_state",
        "agent_script_source",
        "agent_script_sha256",
        "usage_state",
        "usage_turn_id",
        "last_activity_at",
        "current_task",
        "reasoning_availability",
    }
    if set(patch) - allowed:
        raise ValidationError("Unsupported trading runtime fields")
    with trading_transaction() as conn:
        TradingStore("runs").required(run_id, conn=conn)
        return public_run(TradingStore("runs").update_by_id(run_id, patch, conn=conn))


def claim_agent_cycle(run_id, lease_seconds=180, *, paid=False):
    from backend.services.trading.common import assert_running

    if not 1 <= lease_seconds <= 600:
        raise ValidationError("Agent lease must be between 1 and 600 seconds")
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        assert_running(run)
        if run.get("agent_lease_expires_at") and instant(run["agent_lease_expires_at"]) > instant(
            now()
        ):
            raise ConflictError("Another decision cycle holds this run")
        if paid and run.get("usage_state") in {"pending", "unknown"}:
            raise ConflictError("Previous model usage requires billing reconciliation")
        if paid and amount(run["model_cost_usd"]) >= amount(
            run["workspace_snapshot"]["mandate"]["max_model_cost_usd"]
        ):
            raise ConflictError("Agent model budget is exhausted")
        lease = {
            "lease_id": new_id("tlease"),
            "run_id": run_id,
            "lease_expires_at": (instant(now()) + timedelta(seconds=lease_seconds)).isoformat(),
        }
        patch = {
            "agent_lease_id": lease["lease_id"],
            "agent_lease_expires_at": lease["lease_expires_at"],
            "agent_state": "running",
        }
        if paid:
            patch.update(usage_state="pending", usage_turn_id=None)
        TradingStore("runs").update_by_id(
            run_id,
            patch,
            conn=conn,
        )
        return lease


def release_agent_cycle(run_id, lease_id):
    with trading_transaction() as conn:
        run = TradingStore("runs").required(run_id, conn=conn)
        if run.get("agent_lease_id") != lease_id:
            raise ConflictError("Decision lease does not belong to this cycle")
        state = "usage_unknown" if run.get("usage_state") in {"pending", "unknown"} else "waiting"
        if state != "usage_unknown" and run.get("agent_state") in {"budget_exhausted", "failed"}:
            state = run["agent_state"]
        TradingStore("runs").update_by_id(
            run_id,
            {"agent_lease_id": None, "agent_lease_expires_at": None, "agent_state": state},
            conn=conn,
        )

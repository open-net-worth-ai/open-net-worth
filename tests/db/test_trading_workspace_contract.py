"""The workspace preserves its mandate and keeps synthetic capital in its own book."""

import pytest

from backend.core.errors import ConflictError, ValidationError
from backend.db.account_store import AccountStore
from backend.db.trading_store import TradingStore
from backend.services.trading import workspaces

T0 = "2026-01-05T14:30:00+00:00"
T1 = "2026-01-05T15:30:00+00:00"
OBSERVATION = {"instrument": {"symbol": "ACME"}, "timestamp": T0, "price": "100"}


def create_run(workspace_id, **overrides):
    return workspaces.create_run(
        workspace_id,
        {
            "mode": "backtest",
            "start_at": T0,
            "end_at": T1,
            "accounts": [{"capital": "1000"}],
            "observations": [OBSERVATION],
            **overrides,
        },
    )


def test_workspace_edit_keeps_original_and_auditable_revision(db_isolated):
    workspace = workspaces.create_workspace({"name": "Synthetic", "instructions": "First mandate"})
    edited = workspaces.update_workspace(workspace["id"], {"instructions": "Amended mandate"})
    assert edited["revision"] == 2
    assert edited["original_mandate"]["instructions"] == "First mandate"
    assert edited["amendments"][0]["before"]["instructions"] == "First mandate"
    assert edited["amendments"][0]["after"]["instructions"] == "Amended mandate"


def test_run_snapshots_mandate_and_isolates_simulated_funds(db_isolated):
    before = AccountStore().all()
    workspace = workspaces.create_workspace({"name": "Synthetic", "instructions": "Original"})
    run = create_run(workspace["id"])
    assert "observations" not in run
    assert run["workspace_snapshot"]["instructions"] == "Original"
    account = TradingStore("accounts").list_scope(run["id"])[0]
    assert account["cash"] == "1000"
    assert account["reconciliation_status"] == "simulated"
    assert AccountStore().all() == before
    workspaces.update_workspace(workspace["id"], {"instructions": "Revised"})
    assert workspaces.get_run(run["id"])["workspace_snapshot"]["instructions"] == "Original"


def test_active_workspace_cannot_be_edited_directly(db_isolated):
    workspace = workspaces.create_workspace({"name": "Synthetic"})
    run = create_run(workspace["id"])
    TradingStore("runs").update_by_id(run["id"], {"state": "running"})
    with pytest.raises(ConflictError):
        workspaces.update_workspace(workspace["id"], {"instructions": "Change"})
    assert workspaces.get_workspace(workspace["id"])["revision"] == 1


def test_invalid_allocation_rolls_back_all_accounts(db_isolated):
    workspace = workspaces.create_workspace({"name": "Synthetic"})
    with pytest.raises(ValidationError):
        create_run(
            workspace["id"], accounts=[{"capital": "1000"}, {"capital": "100", "currency": "EUR"}]
        )
    assert TradingStore("accounts").all() == []
    assert TradingStore("runs").all() == []


def test_unavailable_binance_connector_fails_closed(db_isolated):
    workspace = workspaces.create_workspace({"name": "Synthetic"})
    with pytest.raises(ConflictError):
        create_run(workspace["id"], accounts=[{"capital": "1000", "connector_id": "binance"}])
    assert TradingStore("accounts").all() == []


def test_decision_lease_is_exclusive_and_unknown_usage_blocks_retry(db_isolated):
    workspace = workspaces.create_workspace({"name": "Synthetic"})
    run = create_run(workspace["id"])
    TradingStore("runs").update_by_id(run["id"], {"state": "running"})
    lease = workspaces.claim_agent_cycle(run["id"], paid=True)
    with pytest.raises(ConflictError):
        workspaces.claim_agent_cycle(run["id"], paid=True)
    workspaces.release_agent_cycle(run["id"], lease["lease_id"])
    assert workspaces.get_run(run["id"])["agent_state"] == "usage_unknown"
    with pytest.raises(ConflictError):
        workspaces.claim_agent_cycle(run["id"], paid=True)

"""Persistent mandate lifecycle and authentic work records use synthetic accounts only."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.trading_workspace_api import router
from backend.core.errors import ConflictError, ValidationError
from backend.core.trading_runtime import bind_trading_context
from backend.db.trading_store import TradingStore, trading_transaction
from backend.services import trading
from backend.services.trading import delegation
from backend.services.trading.work_records import (
    get_work_record,
    record_work,
    workspace_files,
    write_workspace_file,
)


@pytest.fixture(autouse=True)
def isolated(db_isolated):
    yield


def draft():
    return {
        "workspace": {
            "name": "Synthetic delegated research",
            "instructions": "Research ACME for one month.",
            "mandate": {"max_order_notional": "400"},
        },
        "environment": {
            "mode": "backtest",
            "start_at": "2025-01-01T00:00:00Z",
            "end_at": "2025-01-03T00:00:00Z",
            "accounts": [{"capital": "1000"}],
            "observations": [
                {
                    "instrument": {"symbol": "ACME"},
                    "timestamp": "2025-01-01T00:00:00Z",
                    "price": "75",
                }
            ],
        },
    }


def create():
    return asyncio.run(delegation.create_delegation(draft()))


def test_common_review_and_atomic_create_preserve_exact_original():
    data = draft()
    review = delegation.review_delegation(data)
    assert review["ready"] is True
    assert review["workspace"]["instructions"] == data["workspace"]["instructions"]
    result = create()
    assert result["started"] is True
    overview = delegation.workspace_overview(result["workspace"]["id"])
    assert overview["current_run"]["id"] == result["run"]["id"]
    assert overview["original_mandate"]["instructions"] == data["workspace"]["instructions"]
    assert overview["amendments"] == []


def test_invalid_environment_rolls_back_workspace_and_accounts():
    data = draft()
    data["environment"]["accounts"] = [{"capital": "1000", "currency": "EUR"}]
    with pytest.raises(ValidationError, match="FX"):
        asyncio.run(delegation.create_delegation(data))
    assert TradingStore("workspaces").all() == []
    assert TradingStore("accounts").all() == []


def test_mandate_amendment_is_explicit_and_retains_previous_authority():
    result = create()
    workspace, run = result["workspace"], result["run"]
    change = {
        "instructions": "Preserve cash while researching.",
        "reason": "New instruction",
        "expected_revision": 1,
    }
    with pytest.raises(ConflictError, match="Pause"):
        delegation.amend_mandate(workspace["id"], change)
    asyncio.run(trading.apply_run_action(run["id"], {"action": "pause"}))
    updated = delegation.amend_mandate(workspace["id"], change)
    assert updated["workspace"]["revision"] == 2
    assert (
        updated["workspace"]["original_mandate"]["instructions"]
        == draft()["workspace"]["instructions"]
    )
    assert updated["amendment"]["before"]["instructions"] == draft()["workspace"]["instructions"]
    assert updated["current_run"]["initial_workspace_snapshot"]["revision"] == 1
    assert updated["current_run"]["mandate_revision"] == 2
    with pytest.raises(ConflictError, match="latest revision"):
        delegation.amend_mandate(workspace["id"], change)


def test_open_ended_paper_workspace_continues_without_expiry():
    data = draft()
    data["environment"].update(
        mode="paper",
        start_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        end_at=None,
        observations=[],
    )
    result = asyncio.run(delegation.create_delegation(data))
    assert result["started"]
    asyncio.run(trading.apply_run_action(result["run"]["id"], {"action": "advance"}))
    assert asyncio.run(trading.expire_runs()) == {"expired": []}


def test_missing_live_authority_saves_reviewed_draft_without_activation():
    data = draft()
    data["environment"].update(
        mode="live",
        start_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        end_at=None,
        observations=[],
    )
    assert delegation.review_delegation(data)["ready"] is False
    result = asyncio.run(delegation.create_delegation(data))
    assert result["started"] is False
    assert result["run"]["state"] == "ready"
    assert "authorization" in result["activation_error"]["message"]


def test_files_are_versioned_and_scoped_to_host_identity():
    result = create()
    run_id = result["run"]["id"]
    write_workspace_file(run_id, "research/notes.md", "First observation")
    write_workspace_file(run_id, "research/notes.md", "Revised observation")
    assert (
        workspace_files(run_id, name="research/notes.md", version=1)["content"]
        == "First observation"
    )
    assert workspace_files(run_id)["items"][0]["version"] == 2
    with pytest.raises(ValidationError, match="relative"):
        write_workspace_file(run_id, "../outside", "No")
    other = create()["run"]["id"]
    with bind_trading_context(run_id, "test"), pytest.raises(ConflictError, match="another run"):
        workspace_files(other)


def test_safe_full_work_records_preserve_payload_and_explicit_redaction():
    run_id = create()["run"]["id"]
    record = record_work(
        run_id, "tool_result", {"result": '{"api_key":"fictional-private-value","answer":42}'}
    )
    assert record["payload"]["redacted"] is True
    assert "fictional-private-value" not in str(record)
    assert '"answer": 42' in record["payload"]["result"]
    large = {"result": [f"Synthetic observation {i}" for i in range(20000)]}
    chunked = record_work(run_id, "tool_result", large, source="host_observed")
    assert chunked["payload"]["availability"] == "chunked"
    assert chunked["payload"]["redacted"] is False
    assert get_work_record(run_id, chunked["id"])["payload"]["result"] == large["result"]


def test_unchanged_files_do_not_consume_versions_on_scheduled_wakes():
    run_id = create()["run"]["id"]
    first = write_workspace_file(run_id, "strategy.py", "limit_price = 75.00")
    for _ in range(205):
        repeated = write_workspace_file(run_id, "strategy.py", "limit_price = 75.00")
        assert repeated["id"] == first["id"]
    assert workspace_files(run_id, name="strategy.py")["version"] == 1
    changed = write_workspace_file(run_id, "strategy.py", "limit_price = 76.00")
    assert changed["payload"]["version"] == 2


def test_legacy_original_is_explicitly_absent_not_reconstructed():
    result = create()
    workspace_id = result["workspace"]["id"]
    with trading_transaction() as conn:
        TradingStore("workspaces").update_by_id(workspace_id, {"original_mandate": None}, conn=conn)
    overview = delegation.workspace_overview(workspace_id)
    assert overview["original_mandate"] is None
    assert overview["original_availability"] == "not_recorded"


def test_workspace_routes_share_creation_and_files_contract():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.post("/api/trading/delegations/review", json=draft()).status_code == 200
        result = client.post("/api/trading/delegations", json=draft())
        assert result.status_code == 201
        workspace, run = result.json()["workspace"], result.json()["run"]
        assert (
            client.get(f"/api/trading/workspaces/{workspace['id']}/overview").json()["current_run"][
                "id"
            ]
            == run["id"]
        )
        file = write_workspace_file(run["id"], "research.md", "Synthetic notes")
        assert (
            client.get(
                f"/api/trading/runs/{run['id']}/files", params={"name": "research.md"}
            ).json()["content"]
            == "Synthetic notes"
        )
        assert client.get(f"/api/trading/runs/{run['id']}/work/{file['id']}").status_code == 200


def test_existing_workspace_activation_preserves_identity_and_refuses_duplicate_active_scope():
    workspace = trading.create_workspace(draft()["workspace"])
    data = {"environment": draft()["environment"], "start": True, "expected_revision": 1}
    result = asyncio.run(delegation.activate_delegation(workspace["id"], data))
    assert result["workspace"]["id"] == workspace["id"]
    assert len(TradingStore("workspaces").all()) == 1
    with pytest.raises(ConflictError, match="unfinished"):
        asyncio.run(delegation.activate_delegation(workspace["id"], data))


def test_activation_refuses_stale_review_without_creating_environment():
    workspace = trading.create_workspace(draft()["workspace"])
    data = {"environment": draft()["environment"], "start": True, "expected_revision": 1}
    amended = delegation.amend_mandate(
        workspace["id"],
        {
            "instructions": "A newly reviewed synthetic mandate.",
            "reason": "Synthetic concurrent change",
            "expected_revision": 1,
        },
    )
    with pytest.raises(ConflictError, match="latest revision"):
        asyncio.run(delegation.activate_delegation(workspace["id"], data))
    assert TradingStore("runs").all() == []
    result = asyncio.run(
        delegation.activate_delegation(
            workspace["id"],
            {
                **data,
                "expected_revision": amended["workspace"]["revision"],
            },
        )
    )
    assert (
        result["run"]["workspace_snapshot"]["instructions"] == "A newly reviewed synthetic mandate."
    )


def test_recovery_retains_session_files_and_blocks_unknown_billing():
    result = create()
    run_id = result["run"]["id"]
    write_workspace_file(run_id, "research.md", "Persistent synthetic research")
    with trading_transaction() as conn:
        TradingStore("runs").update_by_id(
            run_id,
            {
                "agent_session_id": "synthetic-session",
                "agent_lease_id": "expired",
                "agent_lease_expires_at": "2024-01-01T00:00:00Z",
                "usage_state": "pending",
            },
            conn=conn,
        )
    trading.recover_runs()
    run = trading.get_run(run_id)
    assert run["agent_session_id"] == "synthetic-session"
    assert run["agent_state"] == "usage_unknown"
    assert workspace_files(run_id, name="research.md")["content"] == "Persistent synthetic research"
    with pytest.raises(ConflictError, match="billing reconciliation"):
        trading.claim_agent_cycle(run_id, paid=True)


def test_native_provider_work_is_recorded_without_inventing_or_leaking_payload(monkeypatch):
    from backend.ai import trading_runner_adapter as adapter

    run_id = create()["run"]["id"]

    async def provider(*args, **kwargs):
        yield {"type": "thinking_delta", "text": "Reported analysis for the synthetic allocation."}
        yield {
            "type": "tool_executing",
            "id": "call-one",
            "name": "read_trading_files",
            "input": {"name": "research.md"},
        }
        yield {
            "type": "tool_done",
            "id": "call-one",
            "result": '{"answer":"synthetic","password":"fictional-private-value"}',
        }
        yield {"type": "new_provider_event", "artifact_id": "synthetic-artifact"}
        yield {"type": "text_delta", "text": "Hold while researching."}
        yield {"type": "turn_done", "usage": {"cost_usd": 0.01}}

    async def supervised(session_id, kind, events, **kwargs):
        async for _ in events("synthetic-native-turn"):
            pass
        return "ok"

    monkeypatch.setattr(adapter.assistant, "run", provider)
    monkeypatch.setattr(adapter.assistant, "upsert_session_meta", lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter.turns, "run_inline", supervised)
    asyncio.run(adapter.run_lucius_cycle(run_id))
    records = trading.list_events(run_id)["items"]
    kinds = {row["kind"] for row in records}
    assert {
        "agent_input",
        "agent_reasoning",
        "agent_tool_call",
        "agent_tool_result",
        "agent_native_event",
        "agent_output",
    } <= kinds
    assert "fictional-private-value" not in str(records)
    assert any(row["payload"].get("artifact_id") == "synthetic-artifact" for row in records)
    decision = next(row for row in records if row["kind"] == "agent_decision")
    assert decision["payload"]["data_context"]["observation_ids"]


def test_amended_conditions_cannot_keep_an_old_comparison_ranking():
    first, second = create(), create()
    comparison = trading.create_comparison(
        {"name": "Synthetic comparison", "run_ids": [first["run"]["id"], second["run"]["id"]]}
    )
    assert comparison["comparable"] is True
    asyncio.run(trading.apply_run_action(first["run"]["id"], {"action": "pause"}))
    delegation.amend_mandate(
        first["workspace"]["id"],
        {
            "instructions": "Preserve capital.",
            "reason": "Revised objective",
            "expected_revision": 1,
        },
    )
    updated = trading.get_comparison(comparison["id"])
    assert updated["comparable"] is False
    assert "conditions changed" in str(updated["reasons"])
    with pytest.raises(ConflictError, match="different capital"):
        trading.create_comparison(
            {"name": "Cannot compare", "run_ids": [first["run"]["id"], second["run"]["id"]]}
        )


def test_script_source_is_reviewed_and_stored_for_automatic_execution():
    data = draft()
    data["workspace"].update(
        agent_kind="script",
        strategy_source='import json; print(json.dumps({"summary":"Hold","orders":[]}))',
    )
    assert delegation.review_delegation(data)["ready"] is True
    result = asyncio.run(delegation.create_delegation(data))
    assert result["run"]["agent_script_source"] == data["workspace"]["strategy_source"]


def test_credential_redaction_preserves_financial_values_ids_and_executable_code():
    from backend.services.trading.work_records import safe_work_payload

    source = 'limit_price = 75.00\naccount_id = "tacc_0123456789ab"\nprint(limit_price)\n'
    raw = (
        '{"account_id":"tacc_0123456789ab","quantity":"4","limit_price":"75.00","sha256":"'
        + "a" * 64
        + '"}'
    )
    assert safe_work_payload(source) == source
    assert safe_work_payload(raw) == raw
    run_id = create()["run"]["id"]
    write_workspace_file(run_id, "strategy.py", source)
    assert workspace_files(run_id, name="strategy.py")["content"] == source
    records = record_work(run_id, "tool_result", {"result": raw})
    assert records["payload"]["redacted"] is False
    assert records["payload"]["result"] == raw
    leaked = safe_work_payload(
        {
            "api_key": "fictional-private-value",
            "result": "Authorization: Bearer syntheticcredential123",
            "url": "https://example.com/?token=syntheticcredential123",
        }
    )
    assert "fictional-private-value" not in str(leaked)
    assert "syntheticcredential123" not in str(leaked)
    usage = {"input_tokens": 123, "output_tokens": 10, "cache_read_input_tokens": 20}
    assert safe_work_payload(usage) == usage
    assert safe_work_payload({"input_tokens": "not-a-counter"})["input_tokens"] == "***"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMtdGVzdCJ9.c3ludGhldGljLXNpZ25hdHVyZQ"
    assert jwt not in safe_work_payload(jwt)
    google = "AIza" + "Z" * 35
    telegram = "123456789:" + "Z" * 35
    assert google not in safe_work_payload(google)
    assert telegram not in safe_work_payload(telegram)

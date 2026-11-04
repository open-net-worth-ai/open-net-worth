"""Run scope, grant revocation and participant integration tests use a synthetic book."""

import asyncio
import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.api._deps import require_auth
from backend.api.trading_runtime_api import router as runtime_router
from backend.core.errors import ConflictError, ValidationError
from backend.core.trading_runtime import bind_trading_context, trading_context
from backend.mcp.trading_gateway import router as gateway_router
from backend.services import tool_permissions, trading
from backend.services.trading_access import authenticate_access, issue_access, revoke_access


@pytest.fixture
def run(db_isolated):
    workspace = trading.create_workspace({"name": "Synthetic comparison", "agent_kind": "lucius"})
    run = trading.create_run(
        workspace["id"],
        {
            "mode": "backtest",
            "start_at": "2025-01-01T00:00:00Z",
            "end_at": "2025-01-03T00:00:00Z",
            "accounts": [{"name": "Synthetic account", "capital": "1000"}],
            "observations": [
                {
                    "instrument": {"symbol": "ACME"},
                    "timestamp": "2025-01-01T00:00:00Z",
                    "price": "10",
                }
            ],
        },
    )
    asyncio.run(trading.apply_run_action(run["id"], {"action": "start"}))
    return trading.get_run(run["id"])


@pytest.fixture
def client():
    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(runtime_router)
    app.include_router(gateway_router)
    return TestClient(app)


def rpc(client, run_id, token, name, arguments=None):
    return client.post(
        f"/api/trading/gateway/{run_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )


def test_token_is_scoped_and_rotation_revokes_prior_token(run):
    first = issue_access(run["id"])
    assert authenticate_access(run["id"], first["token"])
    assert not authenticate_access("another-run", first["token"])
    second = issue_access(run["id"])
    assert not authenticate_access(run["id"], first["token"])
    assert authenticate_access(run["id"], second["token"])
    assert first["token"] not in json.dumps(trading.get_run(run["id"]))
    revoke_access(run["id"])
    assert not authenticate_access(run["id"], second["token"])


def test_gateway_requires_its_own_token_even_when_local_app_is_open(run, client):
    response = rpc(client, run["id"], "operator-or-invalid", "get_trading_environment")
    assert response.status_code == 401
    issued = issue_access(run["id"])
    response = rpc(client, run["id"], issued["token"], "get_trading_environment")
    assert response.status_code == 200
    result = json.loads(response.json()["result"]["content"][0]["text"])
    assert result["run_id"] == run["id"]
    assert "no-store" in response.headers["cache-control"]


def test_gateway_never_exposes_general_registry_or_control_plane(run, client):
    token = issue_access(run["id"])["token"]
    response = rpc(client, run["id"], token, "run_sql_query", {"query": "select 1"})
    assert response.json()["error"]["code"] == -32602
    response = client.post(
        f"/api/trading/runs/{run['id']}/access",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert response.status_code == 401


def test_gateway_arguments_cannot_rebind_run_or_execution_mode(run, client):
    token = issue_access(run["id"])["token"]
    response = rpc(
        client, run["id"], token, "get_trading_environment", {"run_id": "other", "mode": "live"}
    )
    assert response.json()["result"]["isError"] is True


def test_bound_dispatch_blocks_nontrading_native_tool(run):
    from backend.ai.tools import execute_tool

    with bind_trading_context(run["id"], "test"):
        result, proposed = asyncio.run(execute_tool("run_sql_query", {"query": "select 1"}))
    assert proposed is None
    assert "only its scoped trading tools" in json.loads(result)["error"]
    assert trading_context() is None


def test_trading_tools_fail_without_a_host_bound_run(run):
    from backend.ai.tools.trading import get_trading_environment

    with pytest.raises(ValidationError, match="server-owned"):
        asyncio.run(get_trading_environment({}))


def test_blocked_global_permission_still_refuses_gateway_call(run, client):
    token = issue_access(run["id"])["token"]
    tool_permissions.set_tool_permission("core", "get_trading_environment", "blocked")
    response = rpc(client, run["id"], token, "get_trading_environment")
    assert response.json()["result"]["isError"] is True


def test_agent_cycle_uses_supervised_turn_and_records_real_usage(run, monkeypatch):
    from backend.ai import trading_runner_adapter as adapter

    observed = {}

    async def fake_assistant(session_id, messages, system, **kwargs):
        observed.update(context=trading_context(), system=system, messages=messages)
        yield {"type": "text_delta", "text": "Hold the synthetic account."}
        yield {"type": "turn_done", "usage": {"cost_usd": 0.01, "input_tokens": 10}}

    async def supervised(session_id, kind, make_events, **kwargs):
        observed["supervisor"] = (kind, kwargs["cost_surface"])
        async for _ in make_events("synthetic-turn"):
            pass
        return "ok"

    monkeypatch.setattr(adapter.assistant, "run", fake_assistant)
    monkeypatch.setattr(adapter.assistant, "upsert_session_meta", lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter.turns, "run_inline", supervised)
    result = asyncio.run(adapter.run_lucius_cycle(run["id"]))
    assert result["status"] == "completed"
    assert observed["supervisor"] == ("trading", "automation")
    assert observed["context"].run_id == run["id"]
    assert "net worth" not in observed["system"]
    assert trading_context() is None
    assert trading.get_run(run["id"])["model_cost_usd"] == "0.01"
    events = trading.list_events(run["id"])["items"]
    assert any(event["kind"] == "agent_decision" for event in events)


def test_invalid_script_intents_cannot_partially_submit(run, monkeypatch):
    from backend.services import trading_scripts

    monkeypatch.setattr(
        trading_scripts,
        "run_trading_script",
        lambda *args, **kwargs: {
            "ok": True,
            "result": {"orders": [{"account_id": "missing-fields"}]},
        },
    )
    with pytest.raises(ValidationError):
        asyncio.run(trading_scripts.execute_strategy(run["id"], "synthetic-source"))
    assert trading.list_orders(run["id"])["total"] == 0
    assert any(
        event["kind"] == "script_failed" for event in trading.list_events(run["id"])["items"]
    )


def test_model_authored_strategy_reuses_its_cycle_and_submits_through_choke(run, monkeypatch):
    from backend.ai.tools import execute_tool
    from backend.services import trading_scripts

    account = trading.get_account(run["id"])["items"][0]
    intent = {
        "account_id": account["id"],
        "instrument": {"symbol": "ACME"},
        "side": "buy",
        "quantity": "1",
        "request_id": "model-strategy-1",
    }
    monkeypatch.setattr(
        trading_scripts,
        "run_trading_script",
        lambda *args, **kwargs: {
            "ok": True,
            "exit_code": 0,
            "result": {"orders": [intent], "summary": "Synthetic strategy"},
        },
    )
    for name in ("run_trading_strategy", "submit_trading_order"):
        tool_permissions.set_tool_permission("core", name, "always_allow")
    lease = trading.claim_agent_cycle(run["id"])
    try:
        with bind_trading_context(run["id"], "lucius", cycle_lease_id=lease["lease_id"]):
            result, proposed = asyncio.run(
                execute_tool("run_trading_strategy", {"source": "model-authored-code"})
            )
        assert proposed is None
        assert json.loads(result)["ok"] is True
        orders = trading.list_orders(run["id"])["items"]
        assert len(orders) == 1
        assert orders[0]["request_id"] == "model-strategy-1"
        assert trading.get_run(run["id"])["agent_lease_id"] == lease["lease_id"]
    finally:
        trading.release_agent_cycle(run["id"], lease["lease_id"])


def test_backtest_waits_for_decision_before_advancing_environment(run):
    from backend.ai.trading_runner_adapter import _advance_environment

    unchanged = asyncio.run(_advance_environment(run))
    assert unchanged["current_time"] == run["current_time"]
    assert unchanged["clock_cursor"] == 0
    trading.record_decision(run["id"], {"summary": "Hold until next observation"})
    advanced = asyncio.run(_advance_environment(trading.get_run(run["id"])))
    assert advanced["clock_cursor"] == 1
    assert advanced["state"] == "completed"


def test_agent_requested_wake_is_preserved_when_summary_is_recorded(run, monkeypatch):
    from backend.ai import trading_runner_adapter as adapter

    async def fake_assistant(*args, **kwargs):
        trading.record_decision(
            run["id"], {"summary": "Wait for market data", "wake_after_seconds": 60}
        )
        yield {"type": "text_delta", "text": "I will wait one minute."}
        yield {"type": "turn_done", "usage": {"cost_usd": 0}}

    async def supervised(session_id, kind, make_events, **kwargs):
        async for _ in make_events("synthetic-wake-turn"):
            pass
        return "ok"

    monkeypatch.setattr(adapter.assistant, "run", fake_assistant)
    monkeypatch.setattr(adapter.assistant, "upsert_session_meta", lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter.turns, "run_inline", supervised)
    asyncio.run(adapter.run_lucius_cycle(run["id"]))
    fresh = trading.get_run(run["id"])
    assert fresh["agent_wake_at"] == "2025-01-01T00:01:00+00:00"


def test_paper_wake_keeps_environment_timestamp_string():
    from backend.ai.trading_runner_adapter import _next_wake

    assert (
        _next_wake({"mode": "paper", "agent_wake_at": "2026-11-04T00:01:00+00:00"})
        == "2026-11-04T00:01:00+00:00"
    )


def test_timed_out_usage_blocks_further_paid_cycles_until_operator_reconciles(run, monkeypatch):
    from backend.ai import trading_runner_adapter as adapter
    from backend.services.trading_usage import reconcile_usage

    async def incomplete(*args, **kwargs):
        yield {"type": "text_delta", "text": "Provider started"}
        await asyncio.sleep(60)

    async def supervised(session_id, kind, make_events, **kwargs):
        async for _ in make_events("interrupted-turn"):
            pass
        return "ok"

    monkeypatch.setattr(adapter, "CYCLE_TIMEOUT", 0.02)
    monkeypatch.setattr(adapter.assistant, "run", incomplete)
    monkeypatch.setattr(adapter.assistant, "upsert_session_meta", lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter.turns, "run_inline", supervised)
    with pytest.raises(TimeoutError):
        asyncio.run(adapter.run_lucius_cycle(run["id"]))
    fresh = trading.get_run(run["id"])
    assert fresh["usage_state"] == "unknown"
    assert fresh["agent_state"] == "usage_unknown"
    with pytest.raises(ConflictError, match="billing reconciliation"):
        asyncio.run(adapter.run_lucius_cycle(run["id"]))
    reconcile_usage(
        run["id"],
        receipt_id="provider-receipt",
        additional_cost_usd="0.25",
        turn_id="interrupted-turn",
    )
    assert trading.get_run(run["id"])["usage_state"] == "settled"
    assert trading.get_run(run["id"])["model_cost_usd"] == "0.25"


def test_usage_observed_before_errored_turn_is_accounted_in_finally(run, monkeypatch):
    from backend.ai import trading_runner_adapter as adapter

    async def partial(*args, **kwargs):
        yield {"type": "turn_done", "usage": {"cost_usd": 0.25, "price_known": True}}
        raise RuntimeError("Synthetic persistence error after usage receipt")

    async def supervised(session_id, kind, make_events, **kwargs):
        async for _ in make_events("partial-receipt-turn"):
            pass

    monkeypatch.setattr(adapter.assistant, "run", partial)
    monkeypatch.setattr(adapter.assistant, "upsert_session_meta", lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter.turns, "run_inline", supervised)
    with pytest.raises(RuntimeError):
        asyncio.run(adapter.run_lucius_cycle(run["id"]))
    assert trading.get_run(run["id"])["model_cost_usd"] == "0.25"
    assert trading.get_run(run["id"])["usage_state"] == "settled"


def test_gateway_rate_limit_counts_valid_run_requests(run, client, monkeypatch):
    from backend.core.ratelimit import SlidingWindowLimiter
    from backend.mcp import trading_gateway

    monkeypatch.setattr(trading_gateway, "request_limiter", SlidingWindowLimiter(1, 60))
    token = issue_access(run["id"])["token"]
    assert rpc(client, run["id"], token, "get_trading_environment").status_code == 200
    response = rpc(client, run["id"], token, "get_trading_environment")
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


def test_gateway_stream_stops_before_reading_unbounded_body():
    from starlette.requests import Request

    from backend.mcp.trading_gateway import MAX_BODY, _bounded_body

    reads = []

    async def receive():
        reads.append(True)
        return {"type": "http.request", "body": b"x" * (MAX_BODY // 2 + 1), "more_body": True}

    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
    assert asyncio.run(_bounded_body(request)) is None
    assert len(reads) == 2


def test_gateway_oversized_content_length_is_refused_without_reading():
    from starlette.requests import Request

    from backend.mcp.trading_gateway import MAX_BODY, _bounded_body

    async def receive():
        raise AssertionError("Oversized request body should never be read")

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-length", str(MAX_BODY + 1).encode())],
        },
        receive,
    )
    assert asyncio.run(_bounded_body(request)) is None

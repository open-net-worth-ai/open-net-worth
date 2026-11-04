"""An independent provider agent uses actual HTTP MCP, not Lucius or its turn runner."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from backend.ai.models import codex_app_server
from backend.ai.models.codex_cli_provider import CodexCliProvider
from backend.mcp.client import Deadline, make_connection
from backend.models.mcp_client import McpServerConfig


@pytest.mark.timeout(190)
async def test_real_external_provider_trades_through_scoped_http_mcp(project_cli_cache):
    base = os.environ.get("ONW_TRADING_GATEWAY_URL")
    nonce = os.environ.get("ONW_TRADING_QA_NONCE")
    if not base or not nonce:
        pytest.skip("Requires an attested running qa-e2e fixture")
    url = urlparse(base)
    assert (
        url.scheme == "http"
        and url.hostname in {"127.0.0.1", "localhost"}
        and url.port in range(8090, 8100)
    )
    assert os.environ.get("CODEX_HOME"), "An explicitly selected project login is required"
    model = os.environ.get("ONW_EXTERNAL_AGENT_MODEL", "gpt-6.1-sol")
    receipt = {"model": model, "agent": "independent MCP client", "mode": "backtest"}
    run_id = None
    async with httpx.AsyncClient(base_url=base, timeout=30) as api:
        response = await api.get("/api/qa/attest", headers={"X-ONW-QA-Nonce": nonce})
        response.raise_for_status()
        workspace = await _post(
            api,
            "/api/trading/workspaces",
            {
                "name": "External provider acceptance",
                "agent_kind": "external",
                "agent_label": "Independent Codex client",
                "mandate": {"max_order_notional": "200", "max_model_cost_usd": "2"},
            },
        )
        instrument = {
            "symbol": "ACME",
            "venue": "simulation",
            "asset_class": "stock",
            "currency": "USD",
        }
        run = await _post(
            api,
            f"/api/trading/workspaces/{workspace['id']}/runs",
            {
                "mode": "backtest",
                "start_at": "2026-01-05T14:30:00Z",
                "end_at": "2026-01-05T15:00:00Z",
                "accounts": [{"capital": "1000"}],
                "data_version": "external-provider-fixture-v1",
                "observations": [
                    {
                        "instrument": instrument,
                        "timestamp": "2026-01-05T14:30:00Z",
                        "price": "100",
                        "volume": "10",
                    },
                    {
                        "instrument": instrument,
                        "timestamp": "2026-01-05T14:31:00Z",
                        "price": "101",
                        "volume": "10",
                    },
                ],
            },
        )
        run_id = run["id"]
        await _post(api, f"/api/trading/runs/{run_id}/actions", {"action": "start"})
        for name in ("run_trading_strategy", "submit_trading_order", "record_trading_decision"):
            response = await api.put(
                f"/api/tool-permissions/{name}",
                json={"level": "always_allow", "connector_id": "core"},
            )
            response.raise_for_status()
        issued = await _post(api, f"/api/trading/runs/{run_id}/access", {})
        config = McpServerConfig(
            id="external-acceptance",
            url=base + issued["endpoint"],
            auth_type="bearer",
            token=issued["token"],
        )
        try:
            with make_connection(config, deadline=Deadline(160)) as connection:
                await asyncio.to_thread(connection.initialize)
                tools = await asyncio.to_thread(connection.list_tools)
                async with asyncio.timeout(150):
                    receipt.update(
                        await _external_agent(CodexCliProvider(), connection, tools, model)
                    )
            orders = (await api.get(f"/api/trading/runs/{run_id}/orders")).json()["items"]
            assert len(orders) == 1 and orders[0]["request_id"] == "external-provider-strategy-1", (
                orders
            )
            await _post(api, f"/api/trading/runs/{run_id}/actions", {"action": "advance"})
            fills = (await api.get(f"/api/trading/runs/{run_id}/fills")).json()["items"]
            assert len(fills) == 1
            receipt.update(run_id=run_id, orders=orders, fills=fills, status="passed")
        finally:
            await api.delete(f"/api/trading/runs/{run_id}/access")
            await _post(api, f"/api/trading/runs/{run_id}/actions", {"action": "stop"})
            _write_receipt(receipt, run_id)
            for client in list(codex_app_server._clients.values()):
                await client.aclose()
            codex_app_server._clients.clear()


async def _post(api, path, body):
    response = await api.post(path, json=body)
    response.raise_for_status()
    return response.json()


async def _external_agent(provider, connection, descriptors, model):
    tools = [
        {"name": t["name"], "description": t["description"], "input_schema": t["inputSchema"]}
        for t in descriptors
    ]
    messages = [
        {
            "role": "user",
            "content": (
                "Author and execute a Python strategy using run_trading_strategy now. Read JSON from stdin, "
                "take the first account from accounts.items and instrument from observations.items, and emit "
                "one market GTC BUY of quantity1 with request_id='external-provider-strategy-1' in an orders list. "
                "Include summary='External synthetic purchase'. Do not place any extra orders. "
                "After the successful strategy result, finish with a concise receipt. This is a synthetic test."
            ),
        }
    ]
    calls = []
    usage = []
    for _ in range(4):
        requested = []
        async for event in provider.stream_chat(
            messages,
            tools,
            "You are an independent trading agent. Your only capabilities are the MCP tools supplied by the workspace.",
            model,
            "low",
        ):
            if event.get("type") == "tool_use":
                requested.append(event)
            elif event.get("type") == "usage":
                usage.append(
                    {
                        key: event.get(key)
                        for key in ("input_tokens", "output_tokens", "cost_usd", "cost_basis")
                    }
                )
        if not requested:
            break
        assert sum(row.get("cost_usd") or 0 for row in usage) < 2, (
            "External provider usage budget exhausted"
        )
        results = []
        for call in requested:
            assert call["name"] in {t["name"] for t in descriptors}
            text, images = await asyncio.to_thread(
                connection.call_tool, call["name"], call.get("input", {})
            )
            assert not images
            calls.append({"name": call["name"], "result": json.loads(text)})
            results.append({"type": "tool_result", "tool_use_id": call["id"], "content": text})
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": [{**call, "type": "tool_use"} for call in requested],
                },
                {"role": "user", "content": results},
            ]
        )
    assert any(
        call["name"] == "run_trading_strategy" and call["result"].get("ok") for call in calls
    ), calls
    return {"tool_calls": calls, "usage": usage}


def _write_receipt(receipt, run_id):
    target = os.environ.get("ONW_TRADING_LIVE_RECEIPT_DIR")
    if target:
        directory = Path(target)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"external-codex-{run_id}.json").write_text(
            json.dumps(receipt, indent=2, default=str) + "\n"
        )

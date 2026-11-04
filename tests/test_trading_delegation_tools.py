"""Conversational setup stages the human contract without creating authority."""

import asyncio
import json

from backend.ai.tools import execute_tool
from backend.core.trading_runtime import bind_trading_context
from backend.services import trading


def _draft():
    return {
        "workspace": {
            "name": "Synthetic delegated allocation",
            "instructions": "Research, write code or trade; manage my synthetic 1000 USD.",
            "mandate": {"max_order_notional": "200", "max_model_cost_usd": "1"},
        },
        "environment": {
            "mode": "paper",
            "start_at": "2026-11-05T00:00:00Z",
            "end_at": "2026-12-05T00:00:00Z",
            "accounts": [{"capital": "1000"}],
        },
        "start": True,
    }


def test_preview_is_unsaved_and_preserves_original_instruction(db_isolated):
    draft = _draft()
    text, proposed = asyncio.run(execute_tool("preview_trading_workspace", draft))
    result = json.loads(text)
    assert proposed is None
    assert result["saved"] is False
    assert result["delegation"]["workspace"]["instructions"] == draft["workspace"]["instructions"]
    assert result["command"] == "trading.review_draft"
    assert trading.list_workspaces()["items"] == []
    assert trading.list_runs()["items"] == []


def test_preview_cannot_stage_live_authorization_even_if_model_requests_it(
    db_isolated, monkeypatch
):
    from backend.ai.tools import trading_delegation as module

    received = {}

    def review(draft):
        received.update(draft)
        return {"ready": False, "blockers": ["Explicit human live authorization required"]}

    monkeypatch.setattr(module, "review_delegation", review)
    draft = _draft()
    draft["environment"].update(mode="live", live_authorized=True)
    text, _ = asyncio.run(execute_tool("preview_trading_workspace", draft))
    result = json.loads(text)
    assert received["environment"]["live_authorized"] is False
    assert result["delegation"]["environment"]["live_authorized"] is False
    assert trading.list_workspaces()["items"] == []


def test_workspace_discovery_is_paginated_and_does_not_start_work(db_isolated):
    trading.create_workspace({"name": "First synthetic workspace"})
    second = trading.create_workspace({"name": "Second synthetic workspace"})
    text, proposed = asyncio.run(execute_tool("list_trading_workspaces", {"limit": 1, "offset": 1}))
    result = json.loads(text)
    assert proposed is None
    assert [row["id"] for row in result["items"]] == [second["id"]]
    assert trading.list_runs()["items"] == []


def test_delegated_executor_cannot_redelegate_or_list_other_workspaces(db_isolated):
    with bind_trading_context("synthetic-run", "synthetic-executor"):
        for name, args in (
            ("list_trading_workspaces", {}),
            ("preview_trading_workspace", _draft()),
        ):
            text, _ = asyncio.run(execute_tool(name, args))
            assert "only its scoped trading tools" in json.loads(text)["error"]

"""Live checks for native tool calls on the Claude Code CLI (flag ``claude_cli_native_tools``).

Opt-in like the rest of ``tests/cli_live`` (``ONW_CLI_LIVE=1``): the real ``claude`` and the
owner's own login, through OUR ``ClaudeSdkProvider``. What they pin that the fake-client unit
tests cannot: the CLI really lists only our MCP tools, really makes a structured call of one,
and really keeps waiting on a parked call for as long as a person takes to approve it.

``ONW_CLI_LIVE_PARK_WAIT`` (seconds, default 20) is how long the parked call waits before its
result arrives; measured with 150 s and 700 s the same query carried on — docs/ai/CLI_REGRESSION.md.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from backend.ai.cli_probe import PROBE_QUESTION, PROBE_SYSTEM, PROBE_TOOL, probe_tool_reachability
from backend.ai.models import claude_sdk_live
from backend.ai.models.claude_sdk_provider import ClaudeSdkProvider
from tests.cli_live.conftest import binary_or_skip, models_for

_MODELS = models_for("claude_cli")


@pytest.mark.parametrize("model", _MODELS)
async def test_native_tools_reach_the_model(model):
    binary_or_skip("claude")
    result = await probe_tool_reachability(ClaudeSdkProvider(), model, effort="medium")
    assert result.ok, f"tool probe {result.status}: {result.detail}"


@pytest.mark.parametrize("model", _MODELS[:1])
async def test_the_cli_lists_only_our_tools(model):
    binary_or_skip("claude")
    provider = ClaudeSdkProvider()
    live = await claude_sdk_live.LiveQuery.start(
        system=PROBE_SYSTEM,
        tools=[PROBE_TOOL],
        prompt=[
            {
                "type": "user",
                "message": {"role": "user", "content": PROBE_QUESTION},
                "parent_tool_use_id": None,
            }
        ],
        model=model,
        effort="low",
        no_thinking=False,
        api_key="",
        cli_path=provider._resolve_cli(),
        key={},
    )
    try:
        init = None
        while init is None:
            message = await asyncio.wait_for(live.messages.get(), timeout=120)
            if getattr(message, "subtype", "") == "init":
                init = message.data
        assert init["tools"] == ["mcp__lucius__get_secret_number"], init["tools"]
        assert [s["name"] for s in init["mcp_servers"]] == ["lucius"]
    finally:
        live.close()


@pytest.mark.parametrize("model", _MODELS[:1])
async def test_a_parked_call_survives_the_wait_for_its_result(model, monkeypatch):
    binary_or_skip("claude")
    wait = float(os.environ.get("ONW_CLI_LIVE_PARK_WAIT") or 20)
    monkeypatch.setenv("ONW_CLAUDE_SDK_PARK_SECONDS", str(int(wait) + 600))
    starts = []
    original = claude_sdk_live.LiveQuery.start.__func__

    async def counting(cls, **kwargs):
        starts.append(1)
        return await original(cls, **kwargs)

    monkeypatch.setattr(claude_sdk_live.LiveQuery, "start", classmethod(counting))
    provider = ClaudeSdkProvider()
    nonce = f"onw-{uuid.uuid4().hex[:12]}"
    messages = [{"role": "user", "content": PROBE_QUESTION}]

    async def round_() -> tuple[str, list[dict]]:
        text, calls = "", []
        async for event in provider.stream_chat(messages, [PROBE_TOOL], PROBE_SYSTEM, model, "low"):
            if event["type"] == "text_delta":
                text += event["text"]
            elif event["type"] == "tool_use":
                calls.append(event)
        return text, calls

    _, calls = await asyncio.wait_for(round_(), timeout=180)
    assert [c["name"] for c in calls] == ["get_secret_number"]
    await asyncio.sleep(wait)
    messages += [
        {"role": "assistant", "content": [{**calls[0], "type": "tool_use"}]},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": calls[0]["id"], "content": nonce}],
        },
    ]
    answer, more = await asyncio.wait_for(round_(), timeout=180)
    assert not more and nonce in answer
    assert len(starts) == 1, "the second round started a new query instead of continuing"


@pytest.mark.parametrize("model", _MODELS[:1])
async def test_a_large_tool_set_is_still_callable(model):
    """Claude Code hides many MCP tools behind a search tool unless told not to; ours is off."""
    binary_or_skip("claude")
    padding = [
        {
            "name": f"get_report_{i:03d}",
            "description": f"Report number {i}. " + "Returns a long synthetic listing. " * 12,
            "input_schema": {"type": "object", "properties": {"month": {"type": "integer"}}},
        }
        for i in range(160)
    ]
    calls: list[dict] = []
    async for event in ClaudeSdkProvider().stream_chat(
        [{"role": "user", "content": PROBE_QUESTION}],
        [*padding, PROBE_TOOL],
        PROBE_SYSTEM,
        model,
        "low",
    ):
        if event["type"] == "tool_use":
            calls.append(event)
    assert [c["name"] for c in calls] == ["get_secret_number"]

"""Live checks for native tool calls on the Claude Code CLI (flag ``claude_cli_native_tools``).

Opt-in like the rest of ``tests/cli_live`` (``ONW_CLI_LIVE=1``): the real ``claude`` and the
owner's own login, through OUR ``ClaudeCliProvider``. What they pin that the fake-CLI unit tests
cannot: the CLI really lists only our MCP tools, really makes a structured call of one, answers
from the host's result, and really keeps waiting on a parked call for as long as a person takes to
approve it.

``ONW_CLI_LIVE_PARK_WAIT`` (seconds, default 20) is how long the parked call waits before its
result arrives; measured at 1000 s and 3700 s the same query carried on — docs/ai/CLI_REGRESSION.md.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from backend.ai.cli_probe import PROBE_QUESTION, PROBE_SYSTEM, PROBE_TOOL, probe_tool_reachability
from backend.ai.models import claude_live_session
from backend.ai.models.claude_cli_provider import ClaudeCliProvider
from backend.core.feature_flags import override
from tests.cli_live.conftest import binary_or_skip, models_for

_MODELS = models_for("claude_cli")


@pytest.fixture(autouse=True)
def _native():
    with override(claude_cli_native_tools=True):
        yield


async def _round(provider, messages, tools, model) -> tuple[str, list[dict]]:
    text, calls = "", []
    async for event in provider.stream_chat(messages, tools, PROBE_SYSTEM, model, "low"):
        if event["type"] == "text_delta":
            text += event["text"]
        elif event["type"] == "tool_use":
            calls.append(event)
    return text, calls


@pytest.mark.parametrize("model", _MODELS)
async def test_native_tools_reach_the_model(model):
    binary_or_skip("claude")
    result = await probe_tool_reachability(ClaudeCliProvider(), model, effort="medium")
    assert result.ok, f"tool probe {result.status}: {result.detail}"


@pytest.mark.parametrize("model", _MODELS[:1])
async def test_a_parked_call_survives_the_wait_and_is_answered_by_the_host(model):
    binary_or_skip("claude")
    wait = float(os.environ.get("ONW_CLI_LIVE_PARK_WAIT") or 20)
    provider = ClaudeCliProvider()
    nonce = f"onw-{uuid.uuid4().hex[:12]}"
    messages = [{"role": "user", "content": PROBE_QUESTION}]

    _, calls = await asyncio.wait_for(_round(provider, messages, [PROBE_TOOL], model), timeout=180)
    assert [c["name"] for c in calls] == [PROBE_TOOL["name"]]
    sessions = claude_live_session.pool().all()
    assert len(sessions) == 1, "the call is parked on one live process"
    await asyncio.sleep(wait)
    messages += [
        {"role": "assistant", "content": [{**calls[0], "type": "tool_use"}]},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": calls[0]["id"], "content": nonce}],
        },
    ]
    answer, more = await asyncio.wait_for(_round(provider, messages, [PROBE_TOOL], model), 180)
    assert not more and nonce in answer, "the model answered from the host's result"
    assert claude_live_session.pool().all() == sessions, "the same process carried on"


@pytest.mark.parametrize("model", _MODELS[:1])
async def test_a_tool_loaded_later_is_called_through_use_tool(model):
    binary_or_skip("claude")
    provider = ClaudeCliProvider()
    loaded = {
        "name": "get_account_code",
        "description": "Returns the account code. Takes no arguments.",
        "input_schema": {"type": "object", "properties": {}},
    }
    first = [{"role": "user", "content": PROBE_QUESTION}]
    _, calls = await asyncio.wait_for(_round(provider, first, [PROBE_TOOL], model), timeout=180)
    answered = [
        *first,
        {"role": "assistant", "content": [{**calls[0], "type": "tool_use"}]},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": calls[0]["id"], "content": "7"}],
        },
    ]
    reply, _ = await asyncio.wait_for(_round(provider, answered, [PROBE_TOOL], model), timeout=180)
    followup = [
        *answered,
        {"role": "assistant", "content": reply},
        {"role": "user", "content": "Now call the newly added get_account_code tool."},
    ]
    sessions = claude_live_session.pool().all()
    _, more = await asyncio.wait_for(_round(provider, followup, [PROBE_TOOL, loaded], model), 180)
    assert [c["name"] for c in more] == ["get_account_code"], "the host sees the real tool name"
    assert claude_live_session.pool().all() == sessions, "no restart for a tool added later"

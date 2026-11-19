"""TradingAgents bridge × CLI transports — the one-shot tool-call proof for each CLI.

For Claude Code, Codex and Antigravity the REAL provider ``stream_chat`` runs against a recorded
stand-in CLI (synthetic content, ticker ACME) behind the real bridge, with the tool schema
TradingAgents sends. Each case shows that: a tool call comes back as an OpenAI ``tool_calls``
message; nothing was run; the CLI turn was a fresh one that is closed afterwards (no pooled
session, no parked Codex turn — ADR 2026-10-05-codex-turns-stay-open-across-tool-rounds is not
touched); and exactly one cost row exists, labelled ``trading_agents`` and ``plan_included``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.ai.models import antigravity_live_session as als
from backend.ai.models import claude_live_session as cls
from backend.ai.models import codex_app_server as cas
from backend.ai.models import codex_cli_provider as codex
from backend.ai.models import codex_live_turn as clt
from backend.ai.models.antigravity_cli_provider import AntigravityCliProvider
from backend.ai.models.claude_cli_provider import ClaudeCliProvider
from backend.ai.trading_agents.llm_bridge import ALIAS_QUICK, BridgeModel, LlmBridge
from backend.db.cost_log_store import CostLogStore
from tests.test_antigravity_cli_provider import _fake_agy
from tests.test_claude_cli_provider import _fake_cli, _init_event, _result_event, _text_deltas
from tests.test_codex_native_tools import _call, _delta, _done, _rows, _server, _usage

RUN_ID = "tar_feedfacecafe"
TOOL_NAME = "get_stock_data"
TOOL_ARGS = {"ticker": "ACME", "start_date": "2026-01-01", "end_date": "2026-01-31"}
# What TradingAgents sends: a function tool, no tool_choice, no response_format.
TA_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Price history for a ticker",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
            },
            "required": ["ticker"],
        },
    },
}
TOOL_CALL_TEXT = (
    "Fetching prices.\n<tool_call>\n"
    + json.dumps({"id": "call_1", "name": TOOL_NAME, "input": TOOL_ARGS})
    + "\n</tool_call>"
)


@pytest.fixture(autouse=True)
def _clean_transports(monkeypatch):
    monkeypatch.setattr(codex, "_APP_SERVER_RETRY_DELAY", 0, raising=False)
    monkeypatch.setattr(cas, "_PARALLEL_CALL_GRACE_SECONDS", 0.15)
    for name in ("ONW_CODEX_NATIVE_TOOLS", "ONW_CODEX_LIVE_TURNS", "ONW_CLAUDE_CLI_SESSIONS"):
        monkeypatch.delenv(name, raising=False)
    cas._clients.clear()
    cas._semaphores.clear()
    clt._parked.clear()
    cls._pools.clear()
    als._pools.clear()
    yield
    clt._parked.clear()
    cas._clients.clear()
    cas._semaphores.clear()


def _open_sessions() -> int:
    pools = [*cls._pools.values(), *als._pools.values()]
    return sum(len(p.all()) for p in pools)


async def _ask_through_the_bridge(provider, transport: str, content="Quote ACME."):
    models = {
        ALIAS_QUICK: BridgeModel(ALIAS_QUICK, "cli:model", "model", "CLI", transport, provider)
    }
    bridge = LlmBridge(run_id=RUN_ID, models=models, concurrency=2)
    http = httpx.ASGITransport(app=bridge.app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(
        transport=http,
        base_url="http://bridge",
        headers={"Authorization": f"Bearer {bridge.token}"},
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": ALIAS_QUICK,
                "messages": [
                    {"role": "system", "content": "You are a market analyst."},
                    {"role": "user", "content": content},
                ],
                "tools": [TA_TOOL],
            },
        )
    return response, bridge


def _assert_tool_call_and_one_plan_included_row(response, args=TOOL_ARGS, name=TOOL_NAME):
    assert response.status_code == 200, response.text
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    (call,) = choice["message"]["tool_calls"]
    assert call["function"]["name"] == name
    assert json.loads(call["function"]["arguments"]) == args
    rows = CostLogStore().all()
    assert len(rows) == 1, rows
    row = rows[0]
    assert (row["source"], row["surface"], row["session_id"]) == (
        "trading_agents",
        "trading_agents",
        RUN_ID,
    )
    assert row["cost_basis"] == "plan_included"
    assert row["input_tokens"] > 0 or row["output_tokens"] > 0


# ── Claude Code ──────────────────────────────────────────────────────────────────────────


async def test_claude_code_returns_a_tool_call_in_a_fresh_one_shot_turn(db_isolated, tmp_path):
    cli = _fake_cli(
        tmp_path,
        [
            _init_event("none"),
            *_text_deltas(*[TOOL_CALL_TEXT[i : i + 24] for i in range(0, len(TOOL_CALL_TEXT), 24)]),
            _result_event(TOOL_CALL_TEXT, cost=0.07),
        ],
    )
    response, _ = await _ask_through_the_bridge(ClaudeCliProvider(cli_path=str(cli)), "claude_cli")
    _assert_tool_call_and_one_plan_included_row(response)
    row = CostLogStore().all()[0]
    assert row["cost_usd"] == 0.07 and (row["input_tokens"], row["output_tokens"]) == (11, 22)
    # The tool manifest reached the CLI, and no live session was kept for a next round.
    sent = json.loads((tmp_path / "argv.json").read_text())
    assert TOOL_NAME in (sent["system_file"] or "") + sent["stdin"]
    assert _open_sessions() == 0


async def test_claude_code_text_answer_without_a_call_is_a_plain_message(db_isolated, tmp_path):
    cli = _fake_cli(
        tmp_path,
        [_init_event("none"), *_text_deltas("Hold ACME."), _result_event("Hold ACME.")],
    )
    response, _ = await _ask_through_the_bridge(ClaudeCliProvider(cli_path=str(cli)), "claude_cli")
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "stop" and choice["message"]["content"] == "Hold ACME."
    assert len(CostLogStore().all()) == 1 and _open_sessions() == 0


# ── Codex ────────────────────────────────────────────────────────────────────────────────


async def test_codex_returns_a_native_tool_call_and_closes_the_turn(db_isolated, tmp_path):
    cli, record = _server(
        tmp_path, [[_delta("Fetching prices. "), _usage(), _call("exec-1", TOOL_NAME, TOOL_ARGS)]]
    )
    response, _ = await _ask_through_the_bridge(codex.CodexCliProvider(cli_path=cli), "codex_cli")
    _assert_tool_call_and_one_plan_included_row(response)
    # One-shot: Codex was told to stop (interrupt-and-capture), and no turn is left parked
    # waiting for a result the bridge will never send.
    assert len(_rows(record, "turn/interrupt")) == 1
    assert clt._parked == {}
    (start,) = _rows(record, "thread/start")
    assert [t["name"] for t in start["dynamicTools"]] == [TOOL_NAME]


async def test_without_one_shot_the_same_codex_call_would_park_the_turn(db_isolated, tmp_path):
    """The contrast that makes the test above mean something: Lucius's own path parks."""
    cli, record = _server(
        tmp_path, [[_delta("x"), _usage(), _call("exec-1", TOOL_NAME, TOOL_ARGS)]]
    )
    provider = codex.CodexCliProvider(cli_path=cli)
    tool = {"name": TOOL_NAME, "description": "d", "input_schema": {"type": "object"}}
    events = [
        e
        async for e in provider.stream_chat(
            messages=[{"role": "user", "content": "Quote ACME."}],
            tools=[tool],
            system="sys",
            model="model",
        )
    ]
    assert any(e["type"] == "tool_use" for e in events)
    assert len(clt._parked) == 1 and _rows(record, "turn/interrupt") == []


async def test_codex_text_answer_without_a_call_is_a_plain_message(db_isolated, tmp_path):
    cli, _ = _server(tmp_path, [[_delta("Hold ACME."), _usage(), _done("Hold ACME.")]])
    response, _ = await _ask_through_the_bridge(codex.CodexCliProvider(cli_path=cli), "codex_cli")
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "stop" and choice["message"]["content"] == "Hold ACME."
    assert len(CostLogStore().all()) == 1 and clt._parked == {}


async def test_codex_on_the_text_protocol_also_returns_the_call(db_isolated, tmp_path, monkeypatch):
    """A Codex build without native tools falls back to the text protocol — same result."""
    monkeypatch.setenv("ONW_CODEX_NATIVE_TOOLS", "0")
    cli, record = _server(tmp_path, [[_delta(TOOL_CALL_TEXT), _usage(), _done(TOOL_CALL_TEXT)]])
    response, _ = await _ask_through_the_bridge(codex.CodexCliProvider(cli_path=cli), "codex_cli")
    _assert_tool_call_and_one_plan_included_row(response)
    assert clt._parked == {}
    assert _rows(record, "thread/start")[0].get("dynamicTools") in (None, [])


# ── Antigravity ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def agy(tmp_path, monkeypatch):
    script = _fake_agy(tmp_path)
    monkeypatch.setattr(
        "backend.db.model_config_store.cli_binary_path", lambda *_a, **_k: str(script)
    )
    return script


async def test_antigravity_returns_a_tool_call_with_one_row_and_no_pooled_session(db_isolated, agy):
    response, _ = await _ask_through_the_bridge(
        AntigravityCliProvider(), "antigravity_cli", content="Quote ACME. TOOLCALL"
    )
    # The stand-in's scripted call; it is the transport's text protocol that surfaces it.
    _assert_tool_call_and_one_plan_included_row(
        response, args={"account_id": "acc_123"}, name="get_balance"
    )
    assert _open_sessions() == 0
    await als.close_all()


async def test_antigravity_pooling_is_what_one_shot_switches_off(db_isolated, agy):
    provider = AntigravityCliProvider()
    messages = [{"role": "user", "content": "Quote ACME. TOOLCALL"}]
    _ = [
        e
        async for e in provider.stream_chat(
            messages=messages, tools=[], system="sys", model="gemini-2.5-flash"
        )
    ]
    assert _open_sessions() == 1  # Lucius's path keeps the process for the next round
    await als.close_all()

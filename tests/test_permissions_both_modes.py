# ruff: noqa: F811  (fixtures imported from another test module, then requested by name)
"""What MUST be identical with ``permissions_v2`` off and on (docs/FEATURE_FLAGS.md § Test both ways).

Every test here runs twice, ``[permissions_v2=off]`` and ``[permissions_v2=on]``, through
``ai.permissions.engine()`` and the real dispatch chain. The premise is always an explicit STORED
row (or a stored flag), so the two modes start from the same facts and only the behaviour they
share is asserted:

  (a) the dispatch gate — Block refuses, Always allow runs, a stored Ask in manual mode stages a
      card and runs nothing, and redeeming it runs the STAGED arguments;
  (b) an external MCP call at stored Block / Always allow / Ask;
  (c) an Automation run in manual mode that parks on an Ask tool, and runs it when the
      Automation's own Always allowed list names it;
  (d) the card's resolve API — approve fires the staged call, deny returns the declined result;
  (f) the inbound ``/mcp`` surface, which cannot show a card: a stored Ask runs, a stored Block
      refuses;
  (e) ``run_code`` and ``run_script``: the kill switch refuses, ``code_execution_auto_run`` with
      no row runs ``run_code``, and a run with nobody watching is still gated by the kill switch.

What differs between the modes (an Automation's Blocked list, the YOLO and unattended switches,
the card labels) is pinned in ``tests/test_permissions_v2_on_off_on.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.chat as chat_mod
from backend.ai import assistant, live_sessions, permissions, turns
from backend.ai.tools import _skill_scope, execute_tool, registry
from backend.ai.tools._confirm_gate import CONFIRM_TOKEN_KEY
from backend.core import chat_session, mcp_external
from backend.core.automation_runner import AutomationRunnerRequest
from backend.core.tool_use_context import set_tool_use_id
from backend.db.app_config_store import (
    CODE_EXEC_AUTO_RUN_FLAG,
    CODE_EXEC_ENABLED_FLAG,
    SCRIPT_SKILLS_FLAG,
    AppConfigStore,
)
from backend.db.mcp_client_store import McpClientStore
from backend.db.pending_plan_store import PendingPlanStore
from backend.mcp.client_manager import McpClientManager
from backend.models.mcp_client import McpServerConfig, mcp_connector_id
from backend.services import payee_registry
from backend.services import tool_permissions as perms
from tests.test_mcp import (  # noqa: F401 — fixtures and helpers of the inbound MCP tests
    _bearer,
    _client,
    _rpc,
    mcp_env,
    seeded_positions,
)

pytestmark = pytest.mark.flag_both("permissions_v2")

TOOL = "create_payees"
OWNER = perms.CORE_CONNECTOR_ID


def _payees() -> list[str]:
    return [row.get("name") for row in payee_registry.list_registry()]


def _set(level: str, tool: str = TOOL, owner: str = OWNER) -> None:
    perms.set_tool_permission(owner, tool, level)


async def _drain(gen) -> list[dict]:
    return [event async for event in gen]


class _ScriptedProvider:
    """``first_calls`` as tool_use blocks on the first model call, plain text after."""

    def __init__(self, first_calls):
        self._first_calls = first_calls
        self.calls = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self.calls += 1
        if self.calls == 1:
            for i, (name, inp) in enumerate(self._first_calls):
                yield {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}
            yield {"type": "done", "stop_reason": "tool_use"}
        else:
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "stop_reason": "end_turn"}


@pytest.fixture
def env(monkeypatch, db_isolated):
    """A card-capable web surface, an empty permission table, the whole tool offer."""
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    yield
    set_tool_use_id(None)


def _install(monkeypatch, provider) -> None:
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)


async def _stage_through_a_turn(monkeypatch, session_id: str, name: str) -> str:
    """Run a real turn whose model calls ``create_payees`` once; return the card's tool_use_id."""
    _install(monkeypatch, _ScriptedProvider([(TOOL, {"name": name})]))
    tool_use_id = None
    for event in await _drain(
        assistant.run(session_id, [{"role": "user", "content": f"add {name}"}], "s")
    ):
        if event.get("type") == "proposed_change":
            tool_use_id = event["tool_use_id"]
    assert tool_use_id == "t0"
    return tool_use_id


# ── (a) the dispatch gate ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_stored_block_refuses_and_nothing_runs(env):
    _set("blocked")

    text, proposed = await registry.dispatch(TOOL, {"name": "Blocked Co"}, tool_use_id="t_block")

    assert proposed is None
    assert json.loads(text)["blocked_by"] == "tool_permission"
    assert "Blocked Co" not in _payees()
    assert PendingPlanStore().get_pending("t_block") is None


@pytest.mark.asyncio
async def test_a_stored_always_allow_runs_without_a_card(env):
    _set("always_allow")

    text, proposed = await registry.dispatch(TOOL, {"name": "Allowed Co"}, tool_use_id="t_allow")

    assert proposed is None
    assert "Allowed Co" in _payees()
    assert PendingPlanStore().get_pending("t_allow") is None
    assert text is not None


@pytest.mark.asyncio
async def test_a_stored_ask_in_manual_mode_stages_a_card_and_runs_nothing(env):
    _set("needs_approval")

    text, proposed = await registry.dispatch(TOOL, {"name": "Asked Co"}, tool_use_id="t_ask")

    assert text is None
    assert proposed is not None and proposed["tool_use_id"] == "t_ask"
    assert "Asked Co" not in _payees()
    pending = PendingPlanStore().get_pending("t_ask")
    assert pending is not None and pending["args"] == {"name": "Asked Co"}


@pytest.mark.asyncio
async def test_a_redemption_runs_the_staged_args_not_the_ones_the_model_resends(env, monkeypatch):
    _set("needs_approval")
    session_id = "both-redeem"
    tool_use_id = await _stage_through_a_turn(monkeypatch, session_id, "Staged Co")
    assert "Staged Co" not in _payees()

    await _drain(assistant.resolve_pending_tool_use(session_id, tool_use_id, "approve", "s"))

    assert "Staged Co" in _payees()
    assert PendingPlanStore().get_pending(tool_use_id) is None


@pytest.mark.asyncio
async def test_a_model_cannot_book_resent_args_by_naming_a_staged_card(env):
    """A fresh call that carries the key of a card waiting elsewhere never runs ITS arguments."""
    _set("needs_approval")
    await registry.dispatch(TOOL, {"name": "Waiting Co"}, tool_use_id="t_wait")

    await registry.dispatch(
        TOOL, {"name": "Tampered Co", "confirm_token": "t_wait"}, tool_use_id="t_fresh"
    )

    assert "Tampered Co" not in _payees()


@pytest.mark.asyncio
async def test_a_redeemed_card_cannot_be_redeemed_twice(env, monkeypatch):
    _set("needs_approval")
    session_id = "both-single-use"
    tool_use_id = await _stage_through_a_turn(monkeypatch, session_id, "Once Co")

    await _drain(assistant.resolve_pending_tool_use(session_id, tool_use_id, "approve", "s"))
    events = await _drain(
        assistant.resolve_pending_tool_use(session_id, tool_use_id, "approve", "s")
    )

    assert any(e.get("type") == "error" for e in events)
    assert _payees().count("Once Co") == 1


@pytest.mark.asyncio
async def test_a_tool_blocked_while_its_card_waited_is_refused_on_redemption(env, monkeypatch):
    _set("needs_approval")
    session_id = "both-blocked-while-waiting"
    tool_use_id = await _stage_through_a_turn(monkeypatch, session_id, "Late Block Co")

    _set("blocked")
    await _drain(assistant.resolve_pending_tool_use(session_id, tool_use_id, "approve", "s"))

    assert "Late Block Co" not in _payees()


# ── (b) an external MCP call ────────────────────────────────────────────────────────────

EXTERNAL = "mcp__drive__transfer"
EXTERNAL_ARGS = {"to": "cash", "amount": 1}


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body
        self._pos = 0
        self.headers = {}

    def read(self, size: int = -1):
        chunk = (
            self._body[self._pos :]
            if size is None or size < 0
            else self._body[self._pos : self._pos + size]
        )
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(captured: dict):
    def fake(request, timeout=None):
        body = json.loads(request.data)
        captured["call_args"] = body["params"]
        result = {"content": [{"type": "text", "text": "moved"}], "isError": False}
        return _FakeResp(
            json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
        )

    return fake


@pytest.fixture
def external(env, monkeypatch):
    """A real ``McpClientManager`` on the external-dispatch seam, with a fake transport."""
    store = McpClientStore()
    store.create(
        McpServerConfig(
            id="drive",
            name="Drive",
            transport="http",
            url="http://fake/mcp",
            auth_type="bearer",
            token="secret-token-xyz",
            enabled=True,
            read_only=True,
        )
    )
    store.record_discovery("drive", [{"name": "transfer"}], "ok", None)
    captured: dict = {}
    monkeypatch.setattr("backend.mcp.client._urlopen", _fake_urlopen(captured))
    mcp_external.register_external_dispatcher(McpClientManager(store=store))
    monkeypatch.setattr(
        assistant,
        "_active_tools",
        lambda: list(assistant.TOOL_SCHEMAS) + assistant._external_tool_schemas(),
    )
    yield captured
    mcp_external.register_external_dispatcher(None)


async def _call_external(tool_use_id: str = "t_ext"):
    set_tool_use_id(tool_use_id)
    return await permissions.engine().dispatch_external(
        mcp_external.get_external_dispatcher(), EXTERNAL, dict(EXTERNAL_ARGS)
    )


@pytest.mark.asyncio
async def test_an_external_tool_at_block_is_refused_and_never_sent(external):
    _set("blocked", "transfer", mcp_connector_id("drive"))

    text, change, _images = await _call_external()

    assert change is None
    assert "blocked" in text.lower()
    assert "call_args" not in external


@pytest.mark.asyncio
async def test_an_external_tool_at_always_allow_fires(external):
    _set("always_allow", "transfer", mcp_connector_id("drive"))

    text, change, _images = await _call_external()

    assert change is None
    assert external["call_args"]["arguments"] == EXTERNAL_ARGS
    assert "moved" in text


@pytest.mark.asyncio
async def test_an_external_tool_at_ask_returns_a_card_and_sends_nothing(external):
    _set("needs_approval", "transfer", mcp_connector_id("drive"))

    text, change, _images = await _call_external()

    assert text is None
    assert change is not None and change["type"] == "external_mcp_call"
    assert change["args"] == EXTERNAL_ARGS
    assert "call_args" not in external


@pytest.mark.asyncio
async def test_an_external_tool_with_no_row_asks(external):
    text, change, _images = await _call_external()

    assert text is None and change is not None
    assert "call_args" not in external


@pytest.mark.asyncio
async def test_approving_an_external_card_fires_the_stored_args(external, monkeypatch):
    _set("needs_approval", "transfer", mcp_connector_id("drive"))
    session_id = "both-external-approve"
    _install(monkeypatch, _ScriptedProvider([(EXTERNAL, {"to": "cash", "amount": 100})]))
    tool_use_id = None
    for event in await _drain(
        assistant.run(session_id, [{"role": "user", "content": "transfer $100"}], "s")
    ):
        if event.get("type") == "proposed_change":
            tool_use_id = event["tool_use_id"]
    assert tool_use_id == "t0" and "call_args" not in external

    await _drain(assistant.resolve_pending_tool_use(session_id, tool_use_id, "approve", "s"))

    assert external["call_args"]["arguments"] == {"to": "cash", "amount": 100}


# ── (c) an Automation run in manual mode that waits on an Ask tool ──────────────────────


def _automation_request(session_id: str, **over) -> AutomationRunnerRequest:
    return AutomationRunnerRequest(
        automation_id="auto_both",
        run_id=f"run_{session_id}",
        session_id=session_id,
        title="Both modes",
        instructions="Add the payee.",
        permission_mode="manual",
        **over,
    )


@pytest.fixture
def automation_surface(env, monkeypatch):
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "automation")


@pytest.mark.asyncio
async def test_a_manual_automation_run_parks_on_an_ask_tool_and_runs_nothing(
    automation_surface, monkeypatch
):
    from backend.ai.automation_runner_adapter import automation_runner

    _set("needs_approval")
    session_id = "automation-run-both-wait"
    _install(monkeypatch, _ScriptedProvider([(TOOL, {"name": "Parked Co"})]))

    result = await automation_runner(_automation_request(session_id))

    assert result.status == "waiting"
    assert result.pending_tool_ids == ["t0"]
    assert "Parked Co" not in _payees()
    assert PendingPlanStore().get_pending("t0") is not None


@pytest.mark.asyncio
async def test_a_manual_automation_run_runs_a_tool_on_its_own_always_allowed_list(
    automation_surface, monkeypatch
):
    from backend.ai.automation_runner_adapter import automation_runner

    _set("needs_approval")
    session_id = "automation-run-both-allowed"
    _install(monkeypatch, _ScriptedProvider([(TOOL, {"name": "Granted Co"})]))

    result = await automation_runner(
        _automation_request(session_id, always_allowed=[{"tools": [TOOL]}])
    )

    assert result.status == "completed"
    assert "Granted Co" in _payees()
    assert PendingPlanStore().get_pending("t0") is None


@pytest.mark.asyncio
async def test_a_stored_block_refuses_inside_an_automation_run_too(automation_surface, monkeypatch):
    from backend.ai.automation_runner_adapter import automation_runner

    _set("blocked")
    session_id = "automation-run-both-blocked"
    _install(monkeypatch, _ScriptedProvider([(TOOL, {"name": "Never Co"})]))

    result = await automation_runner(
        _automation_request(session_id, always_allowed=[{"tools": [TOOL]}])
    )

    assert result.status != "waiting"
    assert "Never Co" not in _payees()


# ── (d) the card's resolve API ──────────────────────────────────────────────────────────


class _TextOnlyProvider:
    async def stream_chat(self, *a, **kw):
        yield {"type": "text_delta", "text": "done"}
        yield {"type": "done", "stop_reason": "end_turn"}


@pytest.fixture
def resolve_client(env, monkeypatch):
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _TextOnlyProvider())
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(True)
    app = FastAPI()
    app.include_router(chat_mod.router)
    yield TestClient(app)
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()


def _park(session_id: str, tool_use_id: str, args: dict) -> None:
    """The session and the pending row exactly as a real pause leaves them."""
    assistant.SESSIONS[session_id] = [
        {"role": "user", "content": "add a payee"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": TOOL, "input": args}],
        },
    ]
    PendingPlanStore().stage_pending(tool_use_id, TOOL, args, session_id=session_id)


def _tool_results(session_id: str, tool_use_id: str) -> list[str]:
    _, history = assistant.get_or_create_session(session_id)
    return [
        str(block.get("content"))
        for message in history
        if message.get("role") == "user"
        for block in (message.get("content") or [])
        if isinstance(block, dict)
        and block.get("type") == "tool_result"
        and block.get("tool_use_id") == tool_use_id
    ]


def test_the_resolve_route_approve_fires_the_staged_tool(resolve_client):
    _set("needs_approval")
    _park("both-api-approve", "t0", {"name": "Approved Co"})

    response = resolve_client.post(
        "/api/chat/both-api-approve/resolve", json={"tool_use_id": "t0", "decision": "approve"}
    )

    assert response.status_code == 202
    assert "Approved Co" in _payees()
    assert PendingPlanStore().get_pending("t0") is None
    assert any("Approved Co" in text for text in _tool_results("both-api-approve", "t0"))


def test_the_resolve_route_deny_returns_the_declined_result_and_writes_nothing(resolve_client):
    _set("needs_approval")
    _park("both-api-deny", "t0", {"name": "Denied Co"})

    response = resolve_client.post(
        "/api/chat/both-api-deny/resolve", json={"tool_use_id": "t0", "decision": "deny"}
    )

    assert response.status_code == 202
    assert "Denied Co" not in _payees()
    assert PendingPlanStore().get_pending("t0") is None
    (result,) = _tool_results("both-api-deny", "t0")
    declined = json.loads(result)
    assert declined["ok"] is False
    assert "THE USER DECLINED" in declined["error"]


def test_the_resolve_route_refuses_an_id_that_is_not_pending_and_writes_nothing(resolve_client):
    response = resolve_client.post(
        "/api/chat/both-api-unknown/resolve", json={"tool_use_id": "never", "decision": "approve"}
    )

    assert response.status_code in (202, 404, 409)
    assert _payees() == []


# ── (e) run_code and run_script ─────────────────────────────────────────────────────────

_ECHO = (
    "import json, os\n"
    "data = json.load(open(os.environ['SKILL_INPUT']))\n"
    "print(json.dumps({'doubled': data.get('n', 0) * 2}))\n"
)
_RUN_CODE_ID = "toolu_both_run_code"


def _run_code(**inp):
    text, proposed = asyncio.run(execute_tool("run_code", inp, tool_use_id=_RUN_CODE_ID))
    return (json.loads(text) if text is not None else None), proposed


@pytest.fixture
def code_env(env, monkeypatch):
    cfg = AppConfigStore()
    cfg.set_flag(CODE_EXEC_ENABLED_FLAG, False)
    cfg.set_flag(CODE_EXEC_AUTO_RUN_FLAG, False)
    chat_session.set_session_id("both-code")
    # A bare test surface cannot show a card, so an ungated call would simply run; this one can.
    monkeypatch.setattr(
        registry, "should_preview_write", lambda: not registry.is_redeeming_approval()
    )
    yield cfg
    cfg.set_flag(CODE_EXEC_ENABLED_FLAG, False)
    cfg.set_flag(CODE_EXEC_AUTO_RUN_FLAG, False)
    chat_session.set_session_id(None)


def test_run_code_with_the_kill_switch_off_refuses(code_env):
    res, proposed = _run_code(source="print('{}')")

    assert proposed is None
    message = res["error"].lower()
    assert "code execution" in message and "off" in message


def test_run_code_with_no_row_and_no_auto_run_returns_a_card(code_env):
    code_env.set_flag(CODE_EXEC_ENABLED_FLAG, True)

    res, proposed = _run_code(source=_ECHO, input={"n": 2}, purpose="double n")

    assert res is None
    assert proposed["tool_use_id"] == _RUN_CODE_ID and proposed["grant_tool"] == "run_code"
    staged = PendingPlanStore().get_pending(_RUN_CODE_ID)
    assert staged is not None and staged["args"]["source"] == _ECHO


def test_run_code_with_auto_run_on_and_no_row_runs_without_a_card(code_env):
    code_env.set_flag(CODE_EXEC_ENABLED_FLAG, True)
    code_env.set_flag(CODE_EXEC_AUTO_RUN_FLAG, True)

    res, proposed = _run_code(source=_ECHO, input={"n": 5})

    assert proposed is None
    assert res["ok"] is True and res["data"] == {"doubled": 10}


def test_run_code_with_auto_run_on_still_obeys_a_stored_block(code_env):
    code_env.set_flag(CODE_EXEC_ENABLED_FLAG, True)
    code_env.set_flag(CODE_EXEC_AUTO_RUN_FLAG, True)
    _set("blocked", "run_code")

    res, proposed = _run_code(source=_ECHO, input={"n": 5})

    assert proposed is None
    assert res["blocked_by"] == "tool_permission"


def test_approving_the_run_code_card_runs_the_staged_source(code_env):
    code_env.set_flag(CODE_EXEC_ENABLED_FLAG, True)
    _res, proposed = _run_code(source=_ECHO, input={"n": 50})
    assert proposed is not None

    text, again = asyncio.run(
        execute_tool(
            "run_code",
            {CONFIRM_TOKEN_KEY: _RUN_CODE_ID, "source": "print('tampered')"},
            tool_use_id=_RUN_CODE_ID,
        )
    )

    assert again is None
    assert json.loads(text)["data"] == {"doubled": 100}


def _script_call(**inp):
    text, proposed = asyncio.run(execute_tool("run_script", inp))
    return json.loads(text), proposed


@pytest.fixture
def script_env(env, monkeypatch, tmp_path):
    from backend.db import skill_store
    from backend.services import skill_bundles

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    monkeypatch.setattr(skill_bundles, "SKILLS_DIR", skills_dir)
    _skill_scope.clear_scope()
    skill = skill_store.SkillStore().create(
        {
            "name": "Both Modes Script Skill",
            "description": "Use to test a bundled script.",
            "body": "Run the echo script.",
            "allowed_tools": [],
        }
    )
    _skill_scope.set_active_bundle(
        {
            "skill_id": skill["id"],
            "manifest": {
                "scripts": [
                    {
                        "id": "echo",
                        "interpreter": "python",
                        "entry": "scripts/echo.py",
                        "timeout_s": 30,
                        "network": False,
                    }
                ]
            },
            "bundle_dir": Path("/tmp/nonexistent"),
            "bundle_sha": "",
            "allowed_script_ids": {"echo"},
        }
    )
    _skill_scope.set_offer_scope(["run_script"])
    yield
    _skill_scope.clear_scope()
    AppConfigStore().set_flag(SCRIPT_SKILLS_FLAG, False)


def test_run_script_with_the_kill_switch_off_refuses(script_env):
    AppConfigStore().set_flag(SCRIPT_SKILLS_FLAG, False)

    res, proposed = _script_call(script_id="echo", input={})

    assert proposed is None
    assert "Script-bundling skills are disabled" in res.get("error", "")


def test_run_script_with_the_kill_switch_on_gets_past_it(script_env):
    AppConfigStore().set_flag(SCRIPT_SKILLS_FLAG, True)

    res, _proposed = _script_call(script_id="no-such-script", input={})

    assert "disabled" not in res.get("error", "")


def test_run_script_outside_a_script_skill_turn_is_unreachable(script_env):
    AppConfigStore().set_flag(SCRIPT_SKILLS_FLAG, True)
    _skill_scope.clear_scope()

    res, proposed = _script_call(script_id="echo", input={})

    assert proposed is None
    assert "not available outside a script-bundling skill turn" in res.get("error", "")


# ── (f) the inbound /mcp surface ────────────────────────────────────────────────────────


def test_the_inbound_surface_runs_a_stored_ask_and_refuses_a_stored_block(
    seeded_positions, mcp_env
):
    """Inbound MCP is not card-capable (a Bearer caller is the operator), so a tool stored as Ask
    has no card to stage and runs. A tool stored as Block is refused: the one value every surface
    honours."""
    from backend.db.position_store import PositionStore

    configure, _ = mcp_env
    configure()
    inst, _instruments, _positions = seeded_positions
    _set("needs_approval", "add_positions")
    call = {"name": "add_positions", "arguments": {"instrument_id": inst["id"], "quantity": 5}}

    ran = _client().post("/mcp", json=_rpc("tools/call", call), headers=_bearer()).json()["result"]
    assert ran["isError"] is False and "proposedChange" not in ran
    assert len(PositionStore().all()) == 1

    _set("blocked", "add_positions")
    refused = (
        _client().post("/mcp", json=_rpc("tools/call", call), headers=_bearer()).json()["result"]
    )
    assert "BLOCKED by tool permissions" in refused["content"][0]["text"]
    assert len(PositionStore().all()) == 1

"""Tests for the run_code tool (ad-hoc-code-execution).

OFFLINE + ISOLATED — no LLM, no network; all config I/O is the conftest-redirected DATA_DIR.

``run_code`` is an ORDINARY tool as far as permissions go (ADR
``2026-10-11-permissions-are-one-value-per-tool``): it declares ``external_effect=True`` so it is
seeded **Ask**, and the one permission value plus the approval mode decide — a card in Manual, the
Auto judge in Auto, runs in Skip, always runs at Always allow, refused at Block. It has NO approval
logic of its own any more (no ``code_run`` card, no "first run in a conversation" grant, no
instance ``auto_run`` flag). What stays in the handler is the KILL SWITCH — the feature's
enable/disable — which refuses whatever the permission value is.

Tests that actually execute the sandbox run on POSIX and Windows.

Run:
    .venv/bin/pytest tests/test_run_code_tool.py -v
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.ai.tools import _skill_scope, execute_tool
from backend.ai.tools import code as code_mod
from backend.ai.tools._confirm_gate import CONFIRM_TOKEN_KEY
from backend.core import chat_session
from backend.core.conversation_approval import (
    ConversationApprovalContext,
    bind_conversation_approval,
    reset_conversation_approval,
)
from backend.db.app_config_store import CODE_EXEC_ENABLED_FLAG, AppConfigStore
from backend.db.pending_plan_store import PendingPlanStore
from backend.services import tool_permissions as perms
from tests.auto_judge_support import stub_judge

_ECHO = (
    "import json, os\n"
    "data = json.load(open(os.environ['SKILL_INPUT']))\n"
    "print(json.dumps({'doubled': data.get('n', 0) * 2}))\n"
)


_TOOL_USE_ID = "toolu_run_code_test"


def call(tool: str, **inp: Any):
    """Dispatch as a card-capable turn would: a real ``tool_use_id`` to stage under.

    A staged (PAUSED) call returns ``text is None``; callers that expect a card read
    ``proposed`` and must not try to parse the result.
    """
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


@pytest.fixture(autouse=True)
def clean_state(db_isolated):
    """Reset the kill switch and the session binding before AND after each test so nothing
    leaks across cases."""
    cfg = AppConfigStore()
    cfg.set_flag(CODE_EXEC_ENABLED_FLAG, False)
    _skill_scope.clear_scope()
    chat_session.set_session_id(None)
    yield
    cfg.set_flag(CODE_EXEC_ENABLED_FLAG, False)
    _skill_scope.clear_scope()
    chat_session.set_session_id(None)


@pytest.fixture(autouse=True)
def card_capable(monkeypatch):
    """A surface that CAN show a card, with the gate armed — so an Ask raises one and a test
    that expects a run is not passing because nothing could ever be staged."""
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")


def _enable():
    AppConfigStore().set_flag(CODE_EXEC_ENABLED_FLAG, True)


# ── Gate: kill switch ──────────────────────────────────────────────────────────


def test_kill_switch_off_refuses():
    perms.set_tool_permission("core", "run_code", "always_allow")  # nothing left to ask
    res, proposed = call("run_code", source="print('{}')")
    assert proposed is None
    # Off by default → refusal names the Code execution toggle (not a permission card).
    err_l = res["error"].lower()
    assert "code execution" in err_l and "off" in err_l


def test_the_kill_switch_refuses_whatever_the_permission_value_is():
    """An enable/disable, not an approval layer: Always allow on a switched-off feature still
    refuses, and the handler never reaches the sandbox."""
    perms.set_tool_permission("core", "run_code", "always_allow")
    res, proposed = call("run_code", source=_ECHO, input={"n": 1})
    assert proposed is None
    assert "code execution is off" in res["error"].lower()


@pytest.mark.parametrize("mode", ["manual", "auto", "skip"])
def test_a_switched_off_sandbox_refuses_before_any_card_or_judge_call(monkeypatch, mode):
    """A card for something that cannot run is noise. With the kill switch off and the tool at
    its seeded Ask, a fresh call is refused at dispatch: no card is staged and the judge is never
    consulted, in every mode."""
    judged: list[str] = []
    stub_judge(monkeypatch, "allow", judged)
    chat_session.set_session_id("sess-off")
    token = bind_conversation_approval(ConversationApprovalContext(session_id="s", mode=mode))
    try:
        res, proposed = call("run_code", source=_ECHO, input={"n": 1})
    finally:
        reset_conversation_approval(token)

    assert proposed is None and res is not None
    assert "code execution is off" in res["error"].lower()
    assert PendingPlanStore().get_pending(_TOOL_USE_ID) is None
    assert judged == []


def test_run_script_is_refused_before_any_card_while_its_switch_is_off(monkeypatch):
    """The same early refusal for the other sandbox tool: bundled script skills switched off."""
    judged: list[str] = []
    stub_judge(monkeypatch, "allow", judged)
    token = bind_conversation_approval(ConversationApprovalContext(session_id="s", mode="auto"))
    try:
        res, proposed = call("run_script", script_id="echo", input={})
    finally:
        reset_conversation_approval(token)

    assert proposed is None
    assert "Script-bundling skills are disabled" in res["error"]
    assert judged == []


def test_only_the_two_sandbox_tools_declare_a_feature_switch():
    from backend.ai.tools.registry import _REGISTRY

    assert {t.name for t in _REGISTRY.values() if t.unavailable is not None} == {
        "run_code",
        "run_script",
    }


def test_the_apply_twin_is_gone():
    """``apply_run_code`` was the second confirm mechanism: a nonce the MODEL had to re-send.
    It is deleted, so the only way to run staged code is the server redeeming the card."""
    from backend.ai.tools.registry import _REGISTRY

    assert "apply_run_code" not in _REGISTRY
    res, proposed = call("apply_run_code", nonce="whatever")
    assert proposed is None
    assert "unknown tool" in json.dumps(res).lower()


# ── One permission value, decided by the ordinary gate ───────────────────────────


def test_run_code_and_run_script_are_seeded_ask_by_a_declaration_not_a_name():
    from backend.ai.tools.registry import has_external_effect, is_write_tool

    for tool in ("run_code", "run_script"):
        assert has_external_effect(tool) is True, tool
        assert is_write_tool(tool) is False, tool  # they write no store: the seed is not mutates=
        assert perms.seed_level(tool) == "needs_approval", tool


def test_no_value_stored_means_the_seed_ask_and_a_generic_card():
    _enable()
    chat_session.set_session_id("sess-1")
    res, proposed = call("run_code", source=_ECHO, input={"n": 2}, purpose="double n")
    # A gated call PAUSES: no result yet, the turn waits on the card.
    assert res is None
    assert proposed is not None
    # The SAME generic permission card every other gated tool gets — no bespoke code_run card.
    assert proposed["type"] == "tool_permission"
    assert proposed["grant_tool"] == "run_code"
    assert proposed["tool_use_id"] == _TOOL_USE_ID
    assert proposed["display"]["arguments"]["source"] == _ECHO
    assert proposed["display"]["arguments"]["purpose"] == "double n"
    # The caller's OWN arguments are staged — approval re-runs the handler with them, so a
    # different source cannot be substituted between the card and the run.
    staged = PendingPlanStore().pop_pending(_TOOL_USE_ID)
    assert staged is not None and staged["args"]["source"] == _ECHO


def test_a_session_binding_alone_never_grants():
    """There is no per-conversation tier: being in a conversation that previously ran code does
    not skip the card."""
    _enable()
    chat_session.set_session_id("sess-granted")
    _res, proposed = call("run_code", source=_ECHO, input={"n": 21})
    assert proposed is not None and proposed["type"] == "tool_permission"


def test_always_allow_runs_immediately_with_no_second_prompt():
    _enable()
    perms.set_tool_permission("core", "run_code", "always_allow")
    chat_session.set_session_id("any-session")
    res, proposed = call("run_code", source=_ECHO, input={"n": 5})
    assert proposed is None
    assert res["ok"] is True
    assert res["data"] == {"doubled": 10}


def test_block_refuses_before_the_handler_runs():
    _enable()
    perms.set_tool_permission("core", "run_code", "blocked")
    res, proposed = call("run_code", source=_ECHO, input={"n": 5})
    assert proposed is None
    assert res["blocked_by"] == "tool_permission"


@pytest.mark.parametrize(
    ("mode", "expected"), [("manual", "card"), ("auto", "card"), ("skip", "ran")]
)
def test_the_mode_decides_what_ask_means(mode, expected):
    """Manual: a card. Auto with no verdict in hand: a card (fails closed). Skip: it runs."""
    _enable()
    token = bind_conversation_approval(ConversationApprovalContext(session_id="s", mode=mode))
    try:
        res, proposed = call("run_code", source=_ECHO, input={"n": 3})
    finally:
        reset_conversation_approval(token)
    if expected == "card":
        assert res is None and proposed["grant_tool"] == "run_code"
    else:
        assert proposed is None and res["data"] == {"doubled": 6}


@pytest.mark.parametrize(("verdict", "ran"), [("allow", True), ("ask", False)])
def test_in_auto_the_judge_decides(monkeypatch, verdict, ran):
    _enable()
    judged: list[str] = []
    stub_judge(monkeypatch, verdict, judged)
    token = bind_conversation_approval(ConversationApprovalContext(session_id="s", mode="auto"))
    try:
        res, proposed = call("run_code", source=_ECHO, input={"n": 4})
    finally:
        reset_conversation_approval(token)
    assert judged == ["run_code"]
    assert (proposed is None) is ran
    if ran:
        assert res["data"] == {"doubled": 8}


def test_the_handler_holds_no_approval_logic_of_its_own():
    """The hidden grant is gone: nothing in the module can stage a card or consult an instance
    flag, so the ONE value plus the mode are the only thing that can ask."""
    from backend.db import app_config_store

    assert not hasattr(code_mod, "_standing_grant")
    assert not hasattr(code_mod, "_auto_run_on")
    assert not hasattr(app_config_store, "CODE_EXEC_AUTO_RUN_FLAG")
    assert not hasattr(app_config_store, "code_execution_auto_run")


def test_empty_source_refused():
    _enable()
    perms.set_tool_permission("core", "run_code", "always_allow")
    res, proposed = call("run_code", source="   ")
    assert proposed is None
    assert "source is required" in res["error"]


def test_oversize_source_refused():
    _enable()
    perms.set_tool_permission("core", "run_code", "always_allow")
    res, proposed = call("run_code", source="x = 1\n" * 30_000)
    assert proposed is None
    assert "cap" in res["error"].lower()


# ── The confirm path ───────────────────────────────────────────────────────────


def test_approving_the_card_runs_the_staged_source():
    """The confirm path end to end: stage, then redeem by tool_use_id the way
    ``assistant.resolve_pending_tool_use`` does. The model never re-sends the source, and the
    redemption does not draw a second card."""
    _enable()
    chat_session.set_session_id("sess-2")
    _res, proposed = call("run_code", source=_ECHO, input={"n": 50})
    assert proposed is not None

    text, again = asyncio.run(
        execute_tool("run_code", {CONFIRM_TOKEN_KEY: _TOOL_USE_ID}, tool_use_id=_TOOL_USE_ID)
    )
    assert again is None, "a redeeming call must not draw a SECOND card"
    res = json.loads(text)
    assert res["ok"] is True
    assert res["data"] == {"doubled": 100}


def test_the_kill_switch_is_rechecked_when_the_approved_card_is_redeemed():
    _enable()
    call("run_code", source=_ECHO, input={"n": 1})
    AppConfigStore().set_flag(CODE_EXEC_ENABLED_FLAG, False)

    text, _ = asyncio.run(
        execute_tool("run_code", {CONFIRM_TOKEN_KEY: _TOOL_USE_ID}, tool_use_id=_TOOL_USE_ID)
    )

    assert "code execution is off" in json.loads(text)["error"].lower()


def test_a_staged_call_is_single_use():
    """Redeeming pops the staging, so replaying the same id cannot re-run the script."""
    _enable()
    chat_session.set_session_id("sess-3")
    call("run_code", source=_ECHO, input={"n": 1})

    first, _ = asyncio.run(
        execute_tool("run_code", {CONFIRM_TOKEN_KEY: _TOOL_USE_ID}, tool_use_id=_TOOL_USE_ID)
    )
    assert json.loads(first)["ok"] is True

    second, proposed = asyncio.run(
        execute_tool("run_code", {CONFIRM_TOKEN_KEY: _TOOL_USE_ID}, tool_use_id=_TOOL_USE_ID)
    )
    assert json.loads(second)["ok"] is not True or proposed is not None


# ── The retired surfaces ───────────────────────────────────────────────────────


def test_code_grant_store_is_gone():
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backend.db.code_grant_store")


def test_the_config_route_carries_only_the_kill_switch():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    assert client.get("/api/config/code-execution").json() == {"enabled": False}
    # A stale client's retired field is ignored, never stored.
    assert client.put("/api/config/code-execution", json={"auto_run": True}).json() == {
        "enabled": False
    }
    assert AppConfigStore().get_flag("code_execution_auto_run", default=False) is False


def test_the_code_grants_routes_are_gone():
    from fastapi.testclient import TestClient

    from backend.main import app

    # The SPA catch-all claims unknown GET paths, so a retired POST/DELETE surfaces as 405
    # rather than 404. What matters is that neither can still record a grant.
    client = TestClient(app)
    assert client.post("/api/code-grants", json={"session_id": "s1"}).status_code in (404, 405)
    assert client.delete("/api/code-grants/s1").status_code in (404, 405)

"""Tests for the run_code / apply_run_code tools (ad-hoc-code-execution).

OFFLINE + ISOLATED — no LLM, no network; all config I/O is the conftest-redirected DATA_DIR.
Covers the gates (RFC §4): kill switch, interactive-only, the no-grant permission card, and
the instance "always allow" grant. Tests that actually execute the sandbox run on POSIX and Windows.

There is no ``apply_run_code`` and no nonce any more (ADR
``2026-08-25-one-confirm-mechanism-for-every-tool``). ``run_code`` stages its own arguments
under the ``tool_use_id`` of the call being gated and returns the PAUSE sentinel; approving
the card re-runs ``run_code`` itself server-side, so the model is never in the
approve-then-execute path and cannot substitute a different source.

Run:
    .venv/bin/pytest tests/test_run_code_tool.py -v
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.ai.tools import _skill_scope, execute_tool
from backend.ai.tools import code as code_mod  # noqa: F401  (kept: monkeypatched below)
from backend.ai.tools._confirm_gate import CONFIRM_TOKEN_KEY
from backend.core import chat_session
from backend.db.app_config_store import (
    CODE_EXEC_AUTO_RUN_FLAG,
    CODE_EXEC_ENABLED_FLAG,
    AppConfigStore,
)
from backend.db.pending_plan_store import PendingPlanStore

# The OLD permission gate's behaviour; permissions_v2 defaults on, and the on path is the
# test_run_code_tool_v2.py twin. Drop this pin with the flag.
pytestmark = pytest.mark.flags(permissions_v2=False)

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
def clean_state():
    """Reset both instance flags and the session binding before AND after each test so
    nothing leaks across cases."""
    cfg = AppConfigStore()
    cfg.set_flag(CODE_EXEC_ENABLED_FLAG, False)
    cfg.set_flag(CODE_EXEC_AUTO_RUN_FLAG, False)
    _skill_scope.clear_scope()
    chat_session.set_session_id(None)
    yield
    cfg.set_flag(CODE_EXEC_ENABLED_FLAG, False)
    cfg.set_flag(CODE_EXEC_AUTO_RUN_FLAG, False)
    _skill_scope.clear_scope()
    chat_session.set_session_id(None)


@pytest.fixture(autouse=True)
def card_capable(monkeypatch):
    """A surface that CAN show a card.

    ``should_preview_write`` consults the real dispatch surface, which in a bare test is not
    card-capable — so an ungated call would simply RUN and every gate assertion below would
    silently pass for the wrong reason. Yielding to a redemption keeps the approve path
    honest: ``is_redeeming_approval()`` is True only while the server re-runs an approved
    call, and that one must write. Same shape as ``tests/test_batch_path_is_gated.py``.
    """
    from backend.ai.tools import registry

    monkeypatch.setattr(
        registry, "should_preview_write", lambda: not registry.is_redeeming_approval()
    )


def _enable():
    AppConfigStore().set_flag(CODE_EXEC_ENABLED_FLAG, True)


# ── Gate: kill switch ──────────────────────────────────────────────────────────


def test_kill_switch_off_refuses():
    res, proposed = call("run_code", source="print('{}')")
    assert proposed is None
    # Off by default → refusal names the Code execution toggle (not a permission card).
    err_l = res["error"].lower()
    assert "code execution" in err_l and "off" in err_l


def test_the_apply_twin_is_gone():
    """``apply_run_code`` was the second confirm mechanism: a nonce the MODEL had to re-send.
    It is deleted, so the only way to run staged code is the server redeeming the card."""
    from backend.ai.tools.registry import _REGISTRY

    assert "apply_run_code" not in _REGISTRY
    res, proposed = call("apply_run_code", nonce="whatever")
    assert proposed is None
    assert "unknown tool" in json.dumps(res).lower()


# ── Gate: interactive-only (headless refused) ──────────────────────────────────


# ── No grant → permission card (proposed_change) ───────────────────────────────


def test_no_grant_returns_permission_card():
    _enable()
    chat_session.set_session_id("sess-1")
    res, proposed = call("run_code", source=_ECHO, input={"n": 2}, purpose="double n")
    # A gated call PAUSES: no result yet, the turn waits on the card.
    assert res is None
    assert proposed is not None
    assert proposed["type"] == "code_run"
    assert proposed["session_id"] == "sess-1"
    assert proposed["display"]["source"] == _ECHO.strip()
    assert proposed["display"]["purpose"] == "double n"
    # Resolved SERVER-side by this id; the card says which tool an Always would grant.
    assert proposed["tool_use_id"] == _TOOL_USE_ID
    assert proposed["grant_tool"] == "run_code"
    # The caller's OWN arguments are staged — approval re-runs the handler with them, so a
    # different source cannot be substituted between the card and the run.
    staged = PendingPlanStore().pop_pending(_TOOL_USE_ID)
    assert staged is not None and staged["args"]["source"] == _ECHO


def test_empty_source_refused():
    _enable()
    res, proposed = call("run_code", source="   ")
    assert proposed is None
    assert "source is required" in res["error"]


def test_oversize_source_refused():
    _enable()
    res, proposed = call("run_code", source="x = 1\n" * 30_000)
    assert proposed is None
    assert "cap" in res["error"].lower()


# ── Standing grants → run immediately (actually execute) ─────────────────────────


def test_a_session_binding_alone_never_grants(tmp_path, monkeypatch):
    """The per-conversation grant tier was removed (2026-08-02): being in a conversation
    that previously allowed code must NOT skip the card."""
    _enable()
    chat_session.set_session_id("sess-granted")
    _res, proposed = call("run_code", source=_ECHO, input={"n": 21})
    assert proposed is not None
    assert proposed["type"] == "code_run"


def test_instance_auto_run_runs_immediately():
    _enable()
    AppConfigStore().set_flag(CODE_EXEC_AUTO_RUN_FLAG, True)
    chat_session.set_session_id("any-session")
    res, proposed = call("run_code", source=_ECHO, input={"n": 5})
    assert proposed is None
    assert res["ok"] is True
    assert res["data"] == {"doubled": 10}


# ── apply_run_code (the confirm path) ──────────────────────────────────────────


def test_approving_the_card_runs_the_staged_source():
    """The confirm path end to end: stage, then redeem by tool_use_id the way
    ``assistant.resolve_pending_tool_use`` does. The model never re-sends the source."""
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


# ── The conversation grant tier is gone (2026-08-02) ──────────────────────────


def test_code_grant_store_is_gone():
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backend.db.code_grant_store")


def test_run_code_without_auto_run_still_prompts(monkeypatch):
    """Removing the conversation tier must not turn an ungranted call into an auto-run."""
    from backend.ai.tools import code as code_tools

    monkeypatch.setattr(code_tools, "_auto_run_on", lambda: False)
    assert code_tools._standing_grant() is False


def test_run_code_with_auto_run_needs_no_card(monkeypatch):
    from backend.ai.tools import code as code_tools

    monkeypatch.setattr(code_tools, "_auto_run_on", lambda: True)
    assert code_tools._standing_grant() is True


def test_the_code_grants_routes_are_gone():
    from fastapi.testclient import TestClient

    from backend.main import app

    # The SPA catch-all claims unknown GET paths, so a retired POST/DELETE surfaces as 405
    # rather than 404. What matters is that neither can still record a grant.
    client = TestClient(app)
    assert client.post("/api/code-grants", json={"session_id": "s1"}).status_code in (404, 405)
    assert client.delete("/api/code-grants/s1").status_code in (404, 405)

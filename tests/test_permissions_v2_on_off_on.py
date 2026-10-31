# ruff: noqa: F811  (fixtures imported from another test module, then requested by name)
"""``permissions_v2`` on, then off, then on again, over ONE set of stored data.

The owner turns the flag on in Settings, changes his mind, and turns it on again. Nothing may be
rewritten along the way, so each phase observes the same data through the rules of its own mode:

  * a Block that exists only on an Automation's Blocked list refuses in that Automation while the
    flag is on, is ignored (the tool runs under the old rules) while it is off, and is back on the
    next time;
  * stored permission rows mean the same thing in both modes (a Block refuses, an Always allow
    runs, an Ask asks);
  * the card's three-answer labels are recorded and shown only while the flag is on, and a label
    recorded earlier is still there when it comes back;
  * a stored YOLO row is the old switch while the flag is off (listed with the ``yolo`` field and
    as an ordinary row) and a stale "no longer available" row while it is on;
  * the search provider's own row reaches ``web_search`` only while it is on;
  * the "Unattended script runs" switch is enforced only while the flag is off.

The test takes the whole set of observations in each phase and compares it with what the phase
should see; the table, the instance flags and the Automation are compared between phases and must
be identical.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.chat as chat_mod
from backend.ai import assistant, live_sessions, permissions, turns
from backend.ai.automation_runner_adapter import automation_runner
from backend.ai.tools import execute_tool, registry
from backend.core.automation_runner import AutomationRunnerRequest
from backend.core.unattended import reset_unattended, set_unattended
from backend.db.app_config_store import AppConfigStore
from backend.db.automation_store import AutomationStore
from backend.db.pending_plan_store import PendingPlanStore
from backend.db.tool_permission_store import ToolPermissionStore
from backend.services import payee_registry, tool_resolutions
from backend.services import tool_permissions as perms
from tests.flags import flags
from tests.test_permissions_v2_read_time_rules import (  # noqa: F401 — fixtures the world needs
    script_scope,
    surface,
)

CORE = perms.CORE_CONNECTOR_ID
WRITE = "create_payees"
SESSION = "onoff-labels"

_ANSWERED = [
    {"role": "user", "content": "do the thing"},
    {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "call_a", "name": WRITE, "input": {"name": "Label A"}},
            {"type": "tool_use", "id": "call_b", "name": WRITE, "input": {"name": "Label B"}},
            {"type": "tool_use", "id": "call_c", "name": WRITE, "input": {"name": "Label C"}},
        ],
    },
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "call_a", "content": "{}"},
            {"type": "tool_result", "tool_use_id": "call_b", "content": "{}"},
            {"type": "tool_result", "tool_use_id": "call_c", "content": "declined"},
        ],
    },
    {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
]


class _ScriptedProvider:
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


class _TextOnlyProvider:
    async def stream_chat(self, *a, **kw):
        yield {"type": "text_delta", "text": "done"}
        yield {"type": "done", "stop_reason": "end_turn"}


def _payees() -> list[str]:
    return [row.get("name") for row in payee_registry.list_registry()]


def _snapshot(automation_id: str) -> dict:
    """Everything the phases must not rewrite: the table (timestamps included), the instance
    flags the rules read, and the Automation with its Blocked list."""
    cfg = AppConfigStore()
    return {
        "rows": sorted(
            (dict(r) for r in ToolPermissionStore().all()),
            key=lambda r: (r["connector_id"], r["tool"]),
        ),
        "flags": {
            name: cfg.get_flag(name, default=False)
            for name in ("code_execution_auto_run", "script_skills_unattended_enabled")
        },
        "automation": AutomationStore().find_by_id(automation_id),
    }


@pytest.fixture
def world(db_isolated, monkeypatch, script_scope):
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    monkeypatch.setattr(assistant, "_load_session", lambda _sid: [dict(m) for m in _ANSWERED])
    monkeypatch.setattr(assistant, "_read_index", lambda: [])
    monkeypatch.setattr(assistant, "_rehydrated_turn_usage", lambda *a, **k: {})
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(True)
    chat_app = FastAPI()
    chat_app.include_router(chat_mod.router)
    from backend.main import app

    yield monkeypatch, TestClient(chat_app), TestClient(app)
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()


async def _automation_outcome(monkeypatch, automation_id: str, tag: str) -> str:
    """A ``skip`` Automation run whose model calls a write tool the Automation itself blocked."""
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "automation")
    monkeypatch.setattr(
        assistant,
        "get_provider",
        lambda *a, **k: _ScriptedProvider([(WRITE, {"name": f"Auto {tag}"})]),
    )
    automation = AutomationStore().find_by_id(automation_id)
    await automation_runner(
        AutomationRunnerRequest(
            automation_id=automation_id,
            run_id=f"run_{tag}",
            session_id=f"automation-run-{tag}",
            title=automation["name"],
            instructions=automation["instructions"],
            permission_mode=automation["permission_mode"],
            always_allowed=automation["always_allowed"],
            blocked=automation["blocked"],
        )
    )
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    return "ran" if f"Auto {tag}" in _payees() else "refused"


async def _dispatch_outcome(tool: str, args: dict, tool_use_id: str) -> str:
    text, proposed = await registry.dispatch(tool, args, tool_use_id=tool_use_id)
    if proposed is not None:
        return "card"
    if text is not None and json.loads(text).get("blocked_by") == "tool_permission":
        return "refused"
    return "ran"


async def _unattended_script_outcome() -> str:
    """A ``run_script`` call with nobody watching, the kill switch on and the second switch off."""
    token = set_unattended(True)
    try:
        text, _ = await execute_tool("run_script", {"script_id": "no-such-script"})
    finally:
        reset_unattended(token)
    return "refused" if "unattended" in json.loads(text).get("error", "").lower() else "passes"


def _answer(monkeypatch, client, call_id: str, **body) -> None:
    """Answer a card through the resolve route, with the call parked exactly as a pause leaves it."""
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _TextOnlyProvider())
    args = {"name": f"Answered {call_id}"}
    assistant.SESSIONS[SESSION] = [
        {"role": "user", "content": "add a payee"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": call_id, "name": WRITE, "input": args}],
        },
    ]
    PendingPlanStore().stage_pending(call_id, WRITE, args, session_id=SESSION)
    response = client.post(f"/api/chat/{SESSION}/resolve", json={"tool_use_id": call_id, **body})
    assert response.status_code == 202


def _rehydrated_labels() -> dict[str, str]:
    messages = assistant.rehydrate_messages(SESSION)
    return {c["id"]: c["resolution"] for m in messages for c in (m.get("proposedChanges") or [])}


async def _observe(world, automation_id: str, tag: str) -> dict:
    monkeypatch, _chat, api = world
    engine = permissions.engine()
    listing = api.get("/api/tool-permissions").json()
    yolo_row = next(r for r in listing["permissions"] if r["tool"] == perms.YOLO_TOOL)
    return {
        "automation_only_block": await _automation_outcome(monkeypatch, automation_id, tag),
        "stored_block": await _dispatch_outcome("get_net_worth", {}, f"t_block_{tag}"),
        "stored_allow": await _dispatch_outcome("add_transactions", {}, f"t_allow_{tag}"),
        "stored_ask": await _dispatch_outcome("create_watchlists", {"name": tag}, f"t_ask_{tag}"),
        "yolo_covers_an_undecided_write": engine.effective_level("update_payees"),
        "yolo_field_in_the_list": "yolo" in listing,
        "yolo_row_tool_exists": yolo_row["tool_exists"],
        "provider_row_reaches_web_search": engine.effective_level("web_search"),
        "unattended_script_run": await _unattended_script_outcome(),
        "rehydrated_labels": _rehydrated_labels(),
    }


@pytest.mark.asyncio
async def test_on_then_off_then_on_changes_the_rules_and_never_the_data(world):
    monkeypatch, chat, api = world

    # The owner's data, set once. ``create_payees`` itself has NO row: an Automation that skips
    # approval runs it unless the Automation blocks it.
    automation = AutomationStore().create(
        {
            "name": "On off on",
            "instructions": "Add the payee.",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
            "permission_mode": "skip",
            "blocked": [WRITE],
        }
    )
    perms.set_tool_permission(CORE, "get_net_worth", "blocked")
    perms.set_tool_permission(CORE, "add_transactions", "always_allow")
    perms.set_tool_permission(CORE, "create_watchlists", "needs_approval")
    perms.set_tool_permission(CORE, perms.YOLO_TOOL, "always_allow")
    perms.set_tool_permission("web_search:duckduckgo", "duckduckgo_search", "blocked")
    AppConfigStore().set_flag("script_skills_unattended_enabled", False)
    before = _snapshot(automation["id"])

    with flags(permissions_v2=True):
        _answer(monkeypatch, chat, "call_a", decision="approve")
        on = await _observe(world, automation["id"], "one")
    after_on = _snapshot(automation["id"])

    with flags(permissions_v2=False):
        _answer(monkeypatch, chat, "call_b", decision="approve", always=True)
        off = await _observe(world, automation["id"], "two")
    after_off = _snapshot(automation["id"])

    with flags(permissions_v2=True):
        _answer(monkeypatch, chat, "call_c", decision="deny")
        on_again = await _observe(world, automation["id"], "three")
    after_on_again = _snapshot(automation["id"])

    assert on == {
        "automation_only_block": "refused",
        "stored_block": "refused",
        "stored_allow": "ran",
        "stored_ask": "card",
        "yolo_covers_an_undecided_write": "always_allow",
        "yolo_field_in_the_list": False,
        "yolo_row_tool_exists": False,
        "provider_row_reaches_web_search": "blocked",
        "unattended_script_run": "passes",
        "rehydrated_labels": {"call_a": "approved"},
    }
    assert off == {
        **on,
        "automation_only_block": "ran",
        "yolo_field_in_the_list": True,
        "yolo_row_tool_exists": True,
        "provider_row_reaches_web_search": "always_allow",
        "unattended_script_run": "refused",
        "rehydrated_labels": {},
    }
    assert on_again == {**on, "rehydrated_labels": {"call_a": "approved", "call_c": "denied"}}

    # Nothing stored was rewritten by switching: the table, the flags and the Automation (with its
    # Blocked list) are byte-for-byte what the owner left.
    assert before == after_on == after_off == after_on_again
    assert tool_resolutions.for_session(SESSION) == {"call_a": "approved", "call_c": "denied"}
    assert perms.stored_tool_level(CORE, "get_net_worth") == "blocked"
    assert perms.yolo_enabled() is True

"""The rules ``permissions_v2`` applies WHEN A VALUE IS READ, and the data it never writes
(docs/FEATURE_FLAGS.md; ``docs/launch/pr9-flag-design.md`` § Data).

There are no migrations, no ``source`` column and no seed rows: the table holds only what the owner
decided, and everything else is worked out at read time. One group of tests per rule:

  rule 1  a tool with no row takes its SEED; nothing is ever written as a default; Reset DELETES
  rule 2  ``web_search`` / ``fetch_page`` take their value from the ACTIVE search provider's tool
  rule 3  ``run_code`` with no row and ``code_execution_auto_run`` on is Always allow
  rule 4  a stored YOLO row makes a registry tool with no row that would ask Always allow
  rule 5  the "Unattended script runs" switch is ignored
  rule 6  additive data only: ``tool_resolutions`` labels and the Automation ``blocked`` list

Plus the two properties that hold them together: after exercising the gate, the list API and Reset
the table holds only rows the test itself wrote, and turning the flag off gives back main's API.
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
from backend.ai.tools import _confirm_gate_v2 as gate
from backend.ai.tools import _skill_scope, execute_tool, registry
from backend.core.unattended import reset_unattended, set_unattended
from backend.db.app_config_store import (
    CODE_EXEC_AUTO_RUN_FLAG,
    CODE_EXEC_ENABLED_FLAG,
    SCRIPT_SKILLS_FLAG,
    SCRIPT_SKILLS_UNATTENDED_FLAG,
    AppConfigStore,
)
from backend.db.automation_store import AutomationStore
from backend.db.pending_plan_store import PendingPlanStore
from backend.db.search_config_store import SearchConfigStore
from backend.db.tool_permission_store import ToolPermissionStore
from backend.services import payee_registry, tool_resolutions
from backend.services import tool_permissions_v2 as perms
from tests.flags import flags

pytestmark = pytest.mark.flags(permissions_v2=True)

CORE = perms.CORE_CONNECTOR_ID
WRITE = "create_payees"
READ = "get_net_worth"
SEND = "email_send_message"


def _stored() -> dict[tuple[str, str], str]:
    return {(r["connector_id"], r["tool"]): r["level"] for r in ToolPermissionStore().all()}


@pytest.fixture
def surface(db_isolated, monkeypatch):
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    yield
    from backend.core.tool_use_context import set_tool_use_id

    set_tool_use_id(None)


@pytest.fixture
def client(surface):
    from backend.main import app

    return TestClient(app)


def _list(client) -> dict:
    return client.get("/api/tool-permissions").json()


def _row(client, tool: str, owner: str | None = None) -> dict:
    return next(
        r
        for r in _list(client)["permissions"]
        if r["tool"] == tool and (owner is None or r["connector_id"] == owner)
    )


# ── rule 1: a tool with no row takes its seed; nothing is written as a default ────────────


def test_rule1_a_tool_with_no_row_takes_its_seed(surface):
    assert gate.effective_level(WRITE) == "needs_approval"
    assert gate.effective_level(SEND) == "needs_approval"
    assert gate.effective_level(READ) == "always_allow"
    assert gate.effective_level("plaid_get_status") == "always_allow"
    assert _stored() == {}


def test_rule1_a_workspace_write_is_seeded_allow_and_a_read_shaped_writer_is_a_read(surface):
    from backend.services.tool_permissions import GATE_EXEMPT_READ_SHAPED

    assert gate.effective_level("remember_fact") == "always_allow"
    exempt = [t for t in sorted(GATE_EXEMPT_READ_SHAPED) if registry.is_write_tool(t)]
    assert exempt
    for tool in exempt:
        assert gate.effective_level(tool) == "always_allow", tool


def test_rule1_an_external_mcp_tool_with_no_row_asks(surface):
    assert gate.effective_level("mcp__drive__transfer") == "needs_approval"
    assert perms.get_tool_permission("mcp:drive", "transfer") == "needs_approval"
    assert _stored() == {}


def test_rule1_the_stored_row_always_beats_the_seed(surface):
    perms.set_tool_permission(CORE, READ, "blocked")
    perms.set_tool_permission(CORE, WRITE, "always_allow")

    assert gate.effective_level(READ) == "blocked"
    assert gate.effective_level(WRITE) == "always_allow"


def test_rule1_the_gate_the_list_and_reset_write_no_default_row(client):
    perms.set_tool_permission(CORE, "add_transactions", "always_allow")
    mine = dict(_stored())

    for tool in (WRITE, READ, SEND, "plaid_get_status", "remember_fact", "run_code"):
        gate.effective_level(tool)
        assert gate.should_gate_dispatch(tool) in (True, False)
    assert _list(client)["total"] > 150
    assert client.delete(f"/api/tool-permissions/{WRITE}").status_code == 200
    assert client.delete("/api/tool-permissions/get_net_worth").status_code == 200
    assert client.get("/api/connectors/plaid/permissions").status_code == 200
    assert client.get("/api/connectors/mcp:drive/permissions").status_code == 200

    assert _stored() == mine == {(CORE, "add_transactions"): "always_allow"}


@pytest.mark.asyncio
async def test_rule1_dispatching_tools_writes_no_default_row(surface):
    await registry.dispatch(READ, {}, tool_use_id="t_read")
    await registry.dispatch(WRITE, {"name": "Seeded Co"}, tool_use_id="t_write")

    assert _stored() == {}
    assert "Seeded Co" not in [p.get("name") for p in payee_registry.list_registry()]


def test_rule1_reset_deletes_the_row_and_answers_the_seed(client):
    client.put(f"/api/tool-permissions/{WRITE}", json={"level": "always_allow"})
    client.put(f"/api/tool-permissions/{READ}", json={"level": "blocked"})
    assert _stored() == {(CORE, WRITE): "always_allow", (CORE, READ): "blocked"}

    write_reset = client.delete(f"/api/tool-permissions/{WRITE}")
    read_reset = client.delete(f"/api/tool-permissions/{READ}")

    assert write_reset.status_code == read_reset.status_code == 200
    assert write_reset.json() == {"reset": WRITE, "level": "needs_approval"}
    assert read_reset.json() == {"reset": READ, "level": "always_allow"}
    assert _stored() == {}
    assert _row(client, WRITE)["level"] == _row(client, WRITE)["default_level"]


def test_rule1_reset_of_a_tool_that_no_longer_exists_deletes_its_row_and_answers_none(client):
    client.put(
        "/api/tool-permissions/a_tool_that_no_longer_exists",
        json={"level": "always_allow", "connector_id": CORE},
    )

    response = client.delete(
        f"/api/tool-permissions/a_tool_that_no_longer_exists?connector_id={CORE}"
    )

    assert response.json() == {"reset": "a_tool_that_no_longer_exists", "level": None}
    assert _stored() == {}


def test_rule1_a_reset_never_answers_404(client):
    assert client.delete(f"/api/tool-permissions/{WRITE}").status_code == 200
    assert client.delete(f"/api/tool-permissions/{WRITE}").status_code == 200


def test_rule1_the_list_carries_the_seed_and_the_description_of_every_tool(client):
    body = _list(client)
    assert body["total"] == len(body["permissions"])
    assert "yolo" not in body
    for row in body["permissions"]:
        if row["tool_exists"]:
            assert row["default_level"] in {"always_allow", "needs_approval"}, row["tool"]
    assert _row(client, WRITE)["description"]


def test_rule1_a_connector_reset_deletes_its_rows_and_each_tool_reads_its_seed(client):
    client.put(
        "/api/connectors/plaid/permissions",
        json={"tools": ["plaid_get_status", "plaid_fetch_transactions"], "level": "blocked"},
    )
    assert len(_stored()) == 2

    body = client.delete("/api/connectors/plaid/permissions").json()

    assert body["permissions"] == {}
    assert body["tool_defaults"]["plaid_get_status"] == "always_allow"
    assert _stored() == {}


# ── rule 2: web_search / fetch_page belong to the ACTIVE search provider ──────────────────

GENERIC = (("web_search", "duckduckgo_search"), ("fetch_page", "duckduckgo_fetch_page"))


@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
def test_rule2_the_generic_tool_takes_the_active_providers_row(surface, generic, provider_tool):
    assert gate.effective_level(generic) == "always_allow"

    perms.set_tool_permission("web_search:duckduckgo", provider_tool, "blocked")
    assert gate.effective_level(generic) == "blocked"

    perms.set_tool_permission("web_search:duckduckgo", provider_tool, "needs_approval")
    assert gate.effective_level(generic) == "needs_approval"


@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
def test_rule2_a_row_on_a_provider_that_is_not_active_does_not_reach_the_generic_tool(
    surface, generic, provider_tool
):
    perms.set_tool_permission("web_search:tavily", "tavily_search", "blocked")
    perms.set_tool_permission("web_search:tavily", "tavily_fetch_page", "blocked")

    assert gate.effective_level(generic) == "always_allow"


@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
def test_rule2_switching_the_active_provider_switches_the_row_that_applies(
    surface, generic, provider_tool
):
    perms.set_tool_permission(
        "web_search:tavily", provider_tool.replace("duckduckgo", "tavily"), "blocked"
    )
    store = SearchConfigStore()
    store.update_provider("tavily", {"api_key": "tvly-test", "enabled": True})
    store.set_default("tavily")
    assert store.default_provider() == "tavily"

    assert gate.effective_level(generic) == "blocked"
    assert gate.effective_level(provider_tool) == "always_allow"


@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
def test_rule2_a_restrictive_core_row_applies_while_the_provider_has_no_row(
    surface, generic, provider_tool
):
    perms.set_tool_permission(CORE, generic, "blocked")
    assert gate.effective_level(generic) == "blocked"

    perms.set_tool_permission(CORE, generic, "needs_approval")
    assert gate.effective_level(generic) == "needs_approval"


@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
def test_rule2_an_always_allow_core_row_never_lifts_anything(surface, generic, provider_tool):
    perms.set_tool_permission(CORE, generic, "always_allow")
    assert gate.effective_level(generic) == "always_allow"

    perms.set_tool_permission("web_search:duckduckgo", provider_tool, "blocked")
    assert gate.effective_level(generic) == "blocked"


@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
def test_rule2_the_providers_always_allow_does_not_lift_a_restrictive_core_row(
    surface, generic, provider_tool
):
    perms.set_tool_permission(CORE, generic, "blocked")
    perms.set_tool_permission("web_search:duckduckgo", provider_tool, "always_allow")

    assert gate.effective_level(generic) == "blocked"


@pytest.mark.asyncio
async def test_rule2_blocking_the_providers_tool_blocks_web_search_at_dispatch(surface):
    perms.set_tool_permission("web_search:duckduckgo", "duckduckgo_search", "blocked")

    text, proposed = await registry.dispatch("web_search", {"query": "x"}, tool_use_id="t_ws")

    assert proposed is None
    assert json.loads(text)["blocked_by"] == "tool_permission"


@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
def test_rule2_setting_a_level_on_the_generic_tool_writes_the_providers_row(
    client, generic, provider_tool
):
    assert (
        client.put(f"/api/tool-permissions/{generic}", json={"level": "blocked"}).status_code == 200
    )

    assert _stored() == {
        ("web_search:duckduckgo", provider_tool): "blocked",
        (CORE, generic): "blocked",
    }
    assert client.delete(f"/api/tool-permissions/{generic}").status_code == 200
    assert _stored() == {}


def test_rule2_the_generic_tools_are_not_listed_but_the_providers_are(client):
    tools = {r["tool"] for r in _list(client)["permissions"]}

    assert not {"web_search", "fetch_page"} & tools
    assert {"duckduckgo_search", "duckduckgo_fetch_page"} <= tools


# ── rule 3: run_code with no row and the old auto-run flag on is Always allow ─────────────


@pytest.fixture
def auto_run(surface):
    cfg = AppConfigStore()
    cfg.set_flag(CODE_EXEC_AUTO_RUN_FLAG, False)
    yield cfg
    cfg.set_flag(CODE_EXEC_AUTO_RUN_FLAG, False)


def test_rule3_run_code_with_no_row_asks_while_the_flag_is_off(auto_run):
    assert gate.effective_level("run_code") == "needs_approval"
    assert perms.unrowed_level("run_code") == "needs_approval"


def test_rule3_run_code_with_no_row_is_always_allow_while_the_flag_is_on(auto_run):
    auto_run.set_flag(CODE_EXEC_AUTO_RUN_FLAG, True)

    assert gate.effective_level("run_code") == "always_allow"
    assert gate.should_gate_dispatch("run_code") is False
    assert _stored() == {}


def test_rule3_the_flag_stays_stored_and_is_never_consumed(auto_run, client):
    auto_run.set_flag(CODE_EXEC_AUTO_RUN_FLAG, True)

    gate.effective_level("run_code")
    _list(client)
    client.delete("/api/tool-permissions/run_code")

    assert auto_run.get_flag(CODE_EXEC_AUTO_RUN_FLAG, default=False) is True


@pytest.mark.parametrize("level", ["needs_approval", "blocked"])
def test_rule3_a_stored_row_beats_the_flag(auto_run, level):
    auto_run.set_flag(CODE_EXEC_AUTO_RUN_FLAG, True)
    perms.set_tool_permission(CORE, "run_code", level)

    assert gate.effective_level("run_code") == level


def test_rule3_the_flag_covers_run_code_alone(auto_run):
    auto_run.set_flag(CODE_EXEC_AUTO_RUN_FLAG, True)

    assert gate.effective_level("run_script") == "needs_approval"
    assert gate.effective_level(WRITE) == "needs_approval"


def test_rule3_the_list_shows_the_value_beside_the_seed(auto_run, client):
    auto_run.set_flag(CODE_EXEC_AUTO_RUN_FLAG, True)

    row = _row(client, "run_code")

    assert row["level"] == "always_allow"
    assert row["default_level"] == "needs_approval"


def test_rule3_reset_deletes_the_row_and_the_flag_applies_again(auto_run, client):
    auto_run.set_flag(CODE_EXEC_AUTO_RUN_FLAG, True)
    perms.set_tool_permission(CORE, "run_code", "needs_approval")
    assert gate.effective_level("run_code") == "needs_approval"

    assert client.delete("/api/tool-permissions/run_code").status_code == 200

    assert gate.effective_level("run_code") == "always_allow"


def test_rule3_the_kill_switch_still_refuses_whatever_the_value(auto_run):
    auto_run.set_flag(CODE_EXEC_AUTO_RUN_FLAG, True)
    AppConfigStore().set_flag(CODE_EXEC_ENABLED_FLAG, False)

    text, proposed = asyncio.run(
        execute_tool("run_code", {"source": "print(1)"}, tool_use_id="t_code")
    )

    assert proposed is None and "code execution" in json.loads(text)["error"].lower()


# ── rule 4: a stored YOLO row is Always allow for a registry tool that would ask ──────────


@pytest.fixture
def yolo(surface):
    perms.set_tool_permission(CORE, perms.YOLO_TOOL, "always_allow")
    assert perms.yolo_enabled() is True
    yield


def test_rule4_yolo_makes_a_registry_tool_with_no_row_that_would_ask_always_allow(yolo):
    assert gate.effective_level(WRITE) == "always_allow"
    assert gate.effective_level(SEND) == "always_allow"
    assert gate.effective_level("run_code") == "always_allow"
    assert gate.should_gate_dispatch(WRITE) is False


@pytest.mark.asyncio
async def test_rule4_yolo_runs_a_write_at_dispatch_with_no_card(yolo):
    text, proposed = await registry.dispatch(WRITE, {"name": "Yolo Co"}, tool_use_id="t_yolo")

    assert proposed is None and text is not None
    assert "Yolo Co" in [p.get("name") for p in payee_registry.list_registry()]
    assert PendingPlanStore().get_pending("t_yolo") is None


def test_rule4_external_mcp_tools_are_not_covered(yolo):
    assert gate.effective_level("mcp__drive__transfer") == "needs_approval"
    assert perms.get_tool_permission("mcp:drive", "transfer") == "needs_approval"


def test_rule4_a_stored_row_always_wins(yolo):
    perms.set_tool_permission(CORE, WRITE, "needs_approval")
    perms.set_tool_permission(CORE, SEND, "always_allow")

    assert gate.effective_level(WRITE) == "needs_approval"
    assert gate.effective_level(SEND) == "always_allow"


def test_rule4_a_block_is_never_overridden(yolo):
    perms.set_tool_permission(CORE, WRITE, "blocked")

    assert gate.effective_level(WRITE) == "blocked"


@pytest.mark.asyncio
async def test_rule4_a_block_refuses_at_dispatch_under_yolo(yolo):
    perms.set_tool_permission(CORE, WRITE, "blocked")

    text, proposed = await registry.dispatch(WRITE, {"name": "Never Co"}, tool_use_id="t_blk")

    assert proposed is None and json.loads(text)["blocked_by"] == "tool_permission"
    assert "Never Co" not in [p.get("name") for p in payee_registry.list_registry()]


def test_rule4_yolo_writes_no_rows_for_the_tools_it_covers(yolo):
    gate.effective_level(WRITE)
    gate.effective_level(SEND)

    assert _stored() == {(CORE, perms.YOLO_TOOL): "always_allow"}


def test_rule4_the_yolo_row_is_listed_as_a_stale_row_and_reset_deletes_it(yolo, client):
    row = _row(client, perms.YOLO_TOOL)
    assert row["connector_id"] == CORE
    assert row["tool_exists"] is False
    assert row["level"] == "always_allow"

    response = client.delete(f"/api/tool-permissions/{perms.YOLO_TOOL}?connector_id={CORE}")

    assert response.status_code == 200
    assert response.json() == {"reset": perms.YOLO_TOOL, "level": None}
    assert _stored() == {}
    assert perms.yolo_enabled() is False
    assert gate.effective_level(WRITE) == "needs_approval"


def test_rule4_the_list_has_no_yolo_field(yolo, client):
    assert "yolo" not in _list(client)


# ── rule 5: the "Unattended script runs" switch is ignored ────────────────────────────────


def test_rule5_the_engine_ignores_the_unattended_switch_only_when_on(surface):
    with flags(permissions_v2=True):
        assert permissions.engine().unattended_script_switch is False
    with flags(permissions_v2=False):
        assert permissions.engine().unattended_script_switch is True


@pytest.fixture
def script_scope(surface, monkeypatch, tmp_path):
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
            "name": "Unattended Rule Skill",
            "description": "Use to test the unattended rule.",
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
    cfg = AppConfigStore()
    cfg.set_flag(SCRIPT_SKILLS_FLAG, True)
    cfg.set_flag(SCRIPT_SKILLS_UNATTENDED_FLAG, False)
    yield cfg
    _skill_scope.clear_scope()
    cfg.set_flag(SCRIPT_SKILLS_FLAG, False)
    cfg.set_flag(SCRIPT_SKILLS_UNATTENDED_FLAG, False)


def _unattended_script_error() -> str:
    token = set_unattended(True)
    try:
        text, _ = asyncio.run(execute_tool("run_script", {"script_id": "no-such-script"}))
    finally:
        reset_unattended(token)
    return json.loads(text).get("error", "")


def test_rule5_a_run_with_nobody_watching_is_not_refused_for_the_switch(script_scope):
    assert "unattended" not in _unattended_script_error().lower()


def test_rule5_the_stored_switch_is_untouched(script_scope):
    script_scope.set_flag(SCRIPT_SKILLS_UNATTENDED_FLAG, True)

    _unattended_script_error()

    assert script_scope.get_flag(SCRIPT_SKILLS_UNATTENDED_FLAG, default=False) is True


def test_rule5_the_kill_switch_still_refuses_a_run_with_nobody_watching(script_scope):
    script_scope.set_flag(SCRIPT_SKILLS_FLAG, False)

    assert "Script-bundling skills are disabled" in _unattended_script_error()


def test_rule5_main_gate_three_still_applies_when_the_flag_is_off(script_scope):
    with flags(permissions_v2=False):
        assert "unattended" in _unattended_script_error().lower()


# ── rule 6: additive data only ────────────────────────────────────────────────────────────


class _TextOnlyProvider:
    async def stream_chat(self, *a, **kw):
        yield {"type": "text_delta", "text": "done"}
        yield {"type": "done", "stop_reason": "end_turn"}


@pytest.fixture
def resolve_client(surface, monkeypatch):
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
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


def _park(session_id: str, tool_use_id: str, name: str) -> None:
    args = {"name": name}
    assistant.SESSIONS[session_id] = [
        {"role": "user", "content": "add a payee"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": WRITE, "input": args}],
        },
    ]
    PendingPlanStore().stage_pending(tool_use_id, WRITE, args, session_id=session_id)


@pytest.mark.parametrize(
    ("body", "label"),
    [
        ({"decision": "approve"}, "approved"),
        ({"decision": "approve", "always": True}, "always_allowed"),
        ({"decision": "deny"}, "denied"),
    ],
)
def test_rule6_each_answer_of_a_card_is_labelled_on_the_session(resolve_client, body, label):
    session_id = f"rule6-{label}"
    _park(session_id, "t0", "Labelled Co")

    response = resolve_client.post(
        f"/api/chat/{session_id}/resolve", json={"tool_use_id": "t0", **body}
    )

    assert response.status_code == 202
    assert tool_resolutions.for_session(session_id) == {"t0": label}


def test_rule6_an_answer_for_a_call_that_is_not_waiting_leaves_no_label(resolve_client):
    session_id = "rule6-stale"
    _park(session_id, "t0", "Stale Co")

    resolve_client.post(
        f"/api/chat/{session_id}/resolve", json={"tool_use_id": "never", "decision": "approve"}
    )

    assert tool_resolutions.for_session(session_id) == {}


def test_rule6_no_label_is_written_with_the_flag_off(resolve_client):
    session_id = "rule6-off"
    _park(session_id, "t0", "Unlabelled Co")

    with flags(permissions_v2=False):
        response = resolve_client.post(
            f"/api/chat/{session_id}/resolve", json={"tool_use_id": "t0", "decision": "approve"}
        )

    assert response.status_code == 202
    assert tool_resolutions.for_session(session_id) == {}


def _create_automation(client, **over) -> dict:
    body = {
        "name": "Rule six",
        "instructions": "Do the thing.",
        "trigger": {"kind": "manual"},
        "permission_mode": "manual",
        **over,
    }
    response = client.post("/api/automations", json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_rule6_the_automation_blocked_list_is_stored_and_read_back(client):
    created = _create_automation(client, blocked=["create_payees", "run_code"])

    assert created["blocked"] == ["create_payees", "run_code"]
    assert AutomationStore().find_by_id(created["id"])["blocked"] == ["create_payees", "run_code"]


def test_rule6_the_blocked_list_is_stored_with_the_flag_off_too(client):
    with flags(permissions_v2=False):
        created = _create_automation(client, blocked=["create_payees"])

    assert AutomationStore().find_by_id(created["id"])["blocked"] == ["create_payees"]


def test_rule6_a_tool_cannot_be_both_always_allowed_and_blocked(client):
    response = client.post(
        "/api/automations",
        json={
            "name": "Both lists",
            "instructions": "Do the thing.",
            "trigger": {"kind": "manual"},
            "always_allowed": [{"tools": ["create_payees"]}],
            "blocked": ["create_payees"],
        },
    )

    assert response.status_code == 422


def test_rule6_the_blocked_list_reaches_the_approval_context_only_when_the_flag_is_on(surface):
    from backend.ai.automation_runner_adapter import _approval_tool_lists
    from backend.core.automation_runner import AutomationRunnerRequest

    request = AutomationRunnerRequest(
        automation_id="a1",
        run_id="r1",
        session_id="automation-run-r1",
        title="t",
        instructions="i",
        always_allowed=[{"tools": [SEND]}],
        blocked=[WRITE],
    )

    with flags(permissions_v2=True):
        on = _approval_tool_lists(request)
    with flags(permissions_v2=False):
        off = _approval_tool_lists(request)

    assert on["blocked_tools"] == frozenset({WRITE})
    assert on["always_allowed_tools"] == frozenset({SEND})
    assert "blocked_tools" not in off
    assert off["always_allowed_tools"] == frozenset({SEND})


# ── the two properties that hold the rules together ───────────────────────────────────────


def test_the_table_holds_only_rows_the_test_itself_wrote(client):
    """Exercise the gate, the list API and Reset; every default stays a read, never a write."""
    CODE = CODE_EXEC_AUTO_RUN_FLAG
    AppConfigStore().set_flag(CODE, True)
    try:
        perms.set_tool_permission(CORE, "add_transactions", "always_allow")
        perms.set_tool_permission("mcp:drive", "transfer", "blocked")
        mine = dict(_stored())

        for tool in (WRITE, READ, SEND, "run_code", "run_script", "web_search", "fetch_page"):
            gate.effective_level(tool)
        asyncio.run(registry.dispatch(READ, {}, tool_use_id="t_a"))
        asyncio.run(registry.dispatch("web_search", {"query": "x"}, tool_use_id="t_b"))
        _list(client)
        client.get("/api/connectors/email/permissions")
        client.delete(f"/api/tool-permissions/{SEND}")
        client.delete("/api/tool-permissions/run_code")
        client.delete("/api/connectors/plaid/permissions")

        assert _stored() == mine
    finally:
        AppConfigStore().set_flag(CODE, False)


def test_turning_the_flag_off_returns_mains_list_shape_exactly(client):
    perms.set_tool_permission(CORE, WRITE, "always_allow")

    with flags(permissions_v2=False):
        body = client.get("/api/tool-permissions").json()

    assert set(body) == {"permissions", "total", "yolo"}
    assert body["total"] == 1 and body["yolo"] is False
    (row,) = body["permissions"]
    assert set(row) == {"tool", "connector_id", "level", "updated_at", "tool_exists"}
    assert (row["tool"], row["connector_id"], row["level"]) == (WRITE, CORE, "always_allow")


def test_the_flag_off_list_is_empty_when_nothing_is_stored_and_reports_yolo(client):
    with flags(permissions_v2=False):
        assert client.get("/api/tool-permissions").json() == {
            "permissions": [],
            "total": 0,
            "yolo": False,
        }
        perms.set_tool_permission(CORE, perms.YOLO_TOOL, "always_allow")
        body = client.get("/api/tool-permissions").json()

    assert body["yolo"] is True and body["total"] == 1


def test_the_flag_off_reset_is_mains_clear(client):
    perms.set_tool_permission(CORE, WRITE, "always_allow")

    with flags(permissions_v2=False):
        response = client.delete(f"/api/tool-permissions/{WRITE}")
        missing = client.delete(f"/api/tool-permissions/{WRITE}?connector_id={CORE}")

    assert response.json() == {"cleared": WRITE}
    assert missing.status_code == 404
    assert _stored() == {}

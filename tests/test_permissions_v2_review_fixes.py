"""Review fixes for ``permissions_v2``: what the flag leaves alone when it goes off, search
permissions, an external MCP card inside an Automation run, one flag read per call, and the
Antigravity judge.

Each group names the fix it pins.
"""

# ruff: noqa: F811  (fixtures imported from another test module, then requested by name)
from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import _confirm_gate_v2 as gate
from backend.ai.tools import registry
from backend.core.errors import ValidationError
from backend.db.automation_store import AutomationStore
from backend.db.tool_permission_store import ToolPermissionStore
from backend.services import tool_permissions_v2 as perms
from backend.services.automations.runs import grant_automation_tool
from backend.services.automations.service import (
    apply_automation_change,
    register_automation_cleanup,
)
from tests.flags import flags
from tests.test_mcp_client import _cfg, manager, store  # noqa: F401 — fixtures and helper
from tests.test_tool_permission_card_scope import (  # noqa: F401 — fixture and helper
    _automation_with_run,
    client,
)

pytestmark = pytest.mark.flags(permissions_v2=True)

CORE = "core"
DDG = "web_search:duckduckgo"


def _stored() -> dict[tuple[str, str], str]:
    return {
        (r["connector_id"], r["tool"]): r["level"]
        for r in ToolPermissionStore().all()
        if r["tool"] != "*"
    }


@pytest.fixture()
def surface(db_isolated, monkeypatch):
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    yield


# ── fix 1: turning the flag off must not loosen or destroy v2 settings ───────────────────────


def _automation_blocking(tool: str) -> dict:
    return apply_automation_change(
        {
            "op": "create",
            "automation": {
                "name": "Blocked list",
                "description": "d",
                "instructions": "Do the thing.",
                "trigger": {"kind": "manual"},
                "execution": {"kind": "lucius"},
                "always_allowed": [],
                "blocked": [tool],
            },
        }
    )


def test_fix1_an_edit_made_with_the_flag_off_keeps_and_ignores_the_blocked_list(db_isolated):
    automation = _automation_blocking("delete_payees")
    allow = [{"src": "chat", "desc": "Delete payees", "tools": ["delete_payees"]}]

    with flags(permissions_v2=False):
        apply_automation_change(
            {"op": "update", "id": automation["id"], "changes": {"always_allowed": allow}}
        )

    stored = AutomationStore().find_by_id(automation["id"])
    assert stored["blocked"] == ["delete_payees"]
    assert stored["always_allowed"][0]["tools"] == ["delete_payees"]


def test_fix1_setting_the_blocked_list_still_refuses_a_tool_that_is_also_allowed(db_isolated):
    automation = _automation_blocking("delete_payees")
    allow = [{"src": "chat", "desc": "Delete payees", "tools": ["delete_payees"]}]

    with pytest.raises(ValidationError, match="both always allowed and blocked"):
        apply_automation_change(
            {
                "op": "update",
                "id": automation["id"],
                "changes": {"always_allowed": allow, "blocked": ["delete_payees"]},
            }
        )


def test_fix1_a_card_grant_takes_the_tool_off_the_blocked_list_only_when_asked_to(db_isolated):
    automation, session_id = _automation_with_run()
    apply_automation_change(
        {"op": "update", "id": automation["id"], "changes": {"blocked": ["add_transactions"]}}
    )

    assert grant_automation_tool(session_id, "add_transactions", "always") is True
    assert AutomationStore().find_by_id(automation["id"])["blocked"] == ["add_transactions"]

    assert (
        grant_automation_tool(session_id, "add_transactions", "always", clear_blocked=True) is True
    )
    assert AutomationStore().find_by_id(automation["id"])["blocked"] == []


def test_fix1_the_cards_always_allow_through_the_api_clears_the_blocked_list(client):
    automation, session_id = _automation_with_run()
    apply_automation_change(
        {"op": "update", "id": automation["id"], "changes": {"blocked": ["add_transactions"]}}
    )

    response = client.put(
        "/api/tool-permissions/add_transactions",
        json={"level": "always_allow", "session_id": session_id},
    )

    assert response.status_code == 200
    assert AutomationStore().find_by_id(automation["id"])["blocked"] == []


@pytest.mark.parametrize("generic", ["web_search", "fetch_page"])
def test_fix1_a_search_level_set_with_the_flag_on_still_blocks_with_it_off(client, generic):
    provider_tool = {"web_search": "duckduckgo_search", "fetch_page": "duckduckgo_fetch_page"}[
        generic
    ]
    assert (
        client.put(f"/api/tool-permissions/{generic}", json={"level": "blocked"}).status_code == 200
    )
    assert _stored() == {(DDG, provider_tool): "blocked", (CORE, generic): "blocked"}

    from backend.ai.tools import _confirm_gate as main_gate

    with flags(permissions_v2=False):
        assert main_gate.effective_level(generic) == "blocked"


def test_fix1_a_level_set_on_the_providers_page_reaches_the_row_main_reads(client):
    response = client.put(
        f"/api/connectors/{DDG}/permissions/duckduckgo_search", json={"level": "needs_approval"}
    )

    assert response.status_code == 200
    assert _stored() == {
        (DDG, "duckduckgo_search"): "needs_approval",
        (CORE, "web_search"): "needs_approval",
    }
    from backend.ai.tools import _confirm_gate as main_gate

    with flags(permissions_v2=False):
        assert main_gate.effective_level("web_search") == "needs_approval"


def test_fix1_a_group_stamp_on_the_providers_page_is_mirrored_too(client):
    client.put(
        f"/api/connectors/{DDG}/permissions",
        json={"tools": ["duckduckgo_search", "duckduckgo_fetch_page"], "level": "blocked"},
    )

    assert _stored()[(CORE, "web_search")] == "blocked"
    assert _stored()[(CORE, "fetch_page")] == "blocked"


def test_fix1_a_level_on_a_provider_that_is_not_active_is_not_mirrored(client):
    client.put(
        "/api/connectors/web_search:tavily/permissions/tavily_search", json={"level": "blocked"}
    )

    assert _stored() == {("web_search:tavily", "tavily_search"): "blocked"}


def test_fix1_resetting_the_providers_tool_removes_the_copy_with_it(client):
    client.put("/api/tool-permissions/web_search", json={"level": "blocked"})

    response = client.delete(f"/api/tool-permissions/duckduckgo_search?connector_id={DDG}")

    assert response.status_code == 200
    assert _stored() == {}


def test_fix1_resetting_the_whole_provider_connector_removes_the_copies(client):
    client.put("/api/tool-permissions/web_search", json={"level": "blocked"})
    client.put("/api/tool-permissions/fetch_page", json={"level": "needs_approval"})

    assert client.delete(f"/api/connectors/{DDG}/permissions").status_code == 200
    assert _stored() == {}


def test_fix1_the_core_copies_are_not_listed_as_stale_rows(client):
    client.put("/api/tool-permissions/web_search", json={"level": "blocked"})

    rows = client.get("/api/tool-permissions").json()["permissions"]

    assert not [r for r in rows if r["connector_id"] == CORE and r["tool"] == "web_search"]
    shown = next(r for r in rows if r["tool"] == "duckduckgo_search")
    assert shown["level"] == "blocked"


# ── fix 2: Reset with an explicit owner deletes exactly that row ─────────────────────────────


def test_fix2_reset_with_an_explicit_owner_deletes_that_row_not_the_providers(client):
    ToolPermissionStore().set_level(CORE, "web_search", "blocked")
    ToolPermissionStore().set_level(DDG, "duckduckgo_search", "needs_approval")

    response = client.delete(f"/api/tool-permissions/web_search?connector_id={CORE}")

    assert response.status_code == 200
    assert response.json()["reset"] == "web_search"
    assert _stored() == {(DDG, "duckduckgo_search"): "needs_approval"}


def test_fix2_reset_with_an_explicit_owner_and_no_row_falls_back_to_resolving(client):
    ToolPermissionStore().set_level(DDG, "duckduckgo_search", "blocked")

    response = client.delete(f"/api/tool-permissions/web_search?connector_id={CORE}")

    assert response.status_code == 200
    assert response.json()["reset"] == "duckduckgo_search"
    assert _stored() == {}


# ── fix 3: the most restrictive wins; a failed provider lookup asks ───────────────────────────


@pytest.mark.parametrize(
    ("core", "provider", "expected"),
    [
        ("blocked", "always_allow", "blocked"),
        ("needs_approval", "always_allow", "needs_approval"),
        ("always_allow", "needs_approval", "needs_approval"),
        ("needs_approval", "blocked", "blocked"),
        ("always_allow", "always_allow", "always_allow"),
    ],
)
def test_fix3_the_effective_level_is_the_stricter_of_the_two_rows(
    surface, core, provider, expected
):
    perms.set_tool_permission(CORE, "web_search", core)
    perms.set_tool_permission(DDG, "duckduckgo_search", provider)

    assert gate.effective_level("web_search") == expected


def test_fix3_a_failed_provider_lookup_asks_and_never_allows(surface, monkeypatch):
    def boom(generic: str):
        if generic in ("web_search", "fetch_page"):
            raise RuntimeError("search config unreadable")
        return None

    monkeypatch.setattr("backend.ai.tools.web_search_providers.active_provider_tool", boom)

    assert gate.effective_level("web_search") == "needs_approval"
    assert gate.effective_level("fetch_page") == "needs_approval"
    assert gate.effective_level("get_net_worth") == "always_allow"


def test_fix3_the_provider_page_shows_the_effective_level(client):
    ToolPermissionStore().set_level(CORE, "web_search", "blocked")

    rows = client.get("/api/tool-permissions").json()["permissions"]

    assert next(r for r in rows if r["tool"] == "duckduckgo_search")["level"] == "blocked"
    assert next(r for r in rows if r["tool"] == "duckduckgo_fetch_page")["level"] == "always_allow"


# ── fix 4: an external MCP card in an Automation run carries the Automation ───────────────────


@pytest.mark.asyncio
async def test_fix4_the_engine_hands_the_run_and_session_to_the_mcp_card(surface, monkeypatch):
    from backend.ai.permissions import engine
    from backend.core.automation_runner import (
        AutomationApprovalContext,
        bind_automation_approval,
        reset_automation_approval,
    )

    seen: dict = {}

    class _Dispatcher:
        async def call(self, name, args, **kwargs):
            seen.update(kwargs)
            return None, {"type": "external_mcp_call"}, []

        async def fire_allowed(self, name, args):  # pragma: no cover - not reached
            raise AssertionError("an Ask call must not fire")

    monkeypatch.setattr("backend.core.chat_session.get_session_id", lambda: "automation-run-r1")
    token = bind_automation_approval(
        AutomationApprovalContext(
            automation_id="auto_1", run_id="r1", session_id="automation-run-r1", mode="manual"
        )
    )
    try:
        perms.set_tool_permission("mcp:drive", "search", "needs_approval")
        await engine().dispatch_external(_Dispatcher(), "mcp__drive__search", {})
    finally:
        reset_automation_approval(token)

    assert seen["card_extras"] == {"automation_id": "auto_1", "session_id": "automation-run-r1"}


def test_fix4_the_manager_puts_the_extras_on_the_card_and_on_the_stored_card(manager):
    from backend.ai.assistant import _staged_card

    mgr, store = manager
    store.create(_cfg())
    store.record_discovery("drive", [{"name": "create_file"}], "ok", None)
    extras = {"automation_id": "auto_1", "session_id": "automation-run-r1"}

    _text, proposed, _images = asyncio.run(
        mgr.call_external_tool("mcp__drive__create_file", {"name": "f"}, card_extras=extras)
    )

    assert proposed["type"] == "external_mcp_call"
    assert {k: proposed[k] for k in extras} == extras
    reloaded = _staged_card(proposed["tool_use_id"])
    assert {k: reloaded[k] for k in extras} == extras


def test_fix4_without_extras_the_card_is_the_one_main_stages(manager):
    mgr, store = manager
    store.create(_cfg())
    store.record_discovery("drive", [{"name": "create_file"}], "ok", None)

    _text, proposed, _images = asyncio.run(
        mgr.call_external_tool("mcp__drive__create_file", {"name": "f"})
    )

    assert "automation_id" not in proposed and "session_id" not in proposed


def test_fix4_always_allow_on_an_mcp_card_lands_on_the_automations_own_list(client):
    automation, session_id = _automation_with_run()

    response = client.put(
        "/api/tool-permissions/mcp__drive__create_file",
        json={"level": "always_allow", "connector_id": "mcp:drive", "session_id": session_id},
    )

    assert response.status_code == 200
    stored = AutomationStore().find_by_id(automation["id"])
    assert "mcp__drive__create_file" in {
        t for item in stored["always_allowed"] for t in item.get("tools", [])
    }
    assert _stored() == {}


def test_fix4_a_deleted_automation_saves_nothing_and_says_so(client):
    automation, session_id = _automation_with_run(status="completed")
    register_automation_cleanup(
        lambda context, *, phase: {"ok": True, "owned_session_ids": [session_id]}
    )
    apply_automation_change({"op": "delete", "id": automation["id"]})

    response = client.put(
        "/api/tool-permissions/mcp__drive__create_file",
        json={"level": "always_allow", "connector_id": "mcp:drive", "session_id": session_id},
    )

    assert response.status_code == 409
    assert "no longer exists" in response.json()["detail"]
    assert _stored() == {}


# ── fix 5: one flag read per call; a failed read refuses ─────────────────────────────────────


@pytest.mark.asyncio
async def test_fix5_one_dispatch_reads_the_flag_once_handler_included(surface, monkeypatch):
    reads: list[int] = []
    real = __import__("backend.ai.permissions", fromlist=["enabled"]).enabled

    def counting(name: str) -> bool:
        reads.append(1)
        return real(name)

    monkeypatch.setattr("backend.ai.permissions.enabled", counting)
    perms.set_tool_permission(CORE, "create_payees", "always_allow")

    await registry.dispatch("create_payees", {"name": "Once Co"}, tool_use_id="toolu_once")

    assert len(reads) == 1


@pytest.mark.asyncio
async def test_fix5_a_flip_during_the_call_does_not_change_the_gate_the_handler_sees(
    surface, monkeypatch
):
    from backend.ai.permissions import engine

    seen: list[str] = []

    async def handler(_input):
        with flags(permissions_v2=False):
            seen.append(type(engine()).__name__)
        return json.dumps({"ok": True}), None

    entry = registry._REGISTRY["get_net_worth"]
    monkeypatch.setitem(
        registry._REGISTRY,
        "get_net_worth",
        entry.__class__(**{**entry.__dict__, "handler": handler}),
    )

    await registry.dispatch("get_net_worth", {}, tool_use_id="toolu_flip")

    assert seen == ["NewEngine"]


@pytest.mark.asyncio
async def test_fix5_an_unreadable_flag_refuses_the_call_and_runs_nothing(surface, monkeypatch):
    ran: list[int] = []

    async def handler(_input):
        ran.append(1)
        return json.dumps({"ok": True}), None

    entry = registry._REGISTRY["get_net_worth"]
    monkeypatch.setitem(
        registry._REGISTRY,
        "get_net_worth",
        entry.__class__(**{**entry.__dict__, "handler": handler}),
    )

    def boom(_name: str):
        raise RuntimeError("flag store unreadable")

    monkeypatch.setattr("backend.ai.permissions.enabled", boom)

    text, proposed = await registry.dispatch("get_net_worth", {}, tool_use_id="toolu_unread")

    assert proposed is None and not ran
    assert "permission check" in json.loads(text)["error"]


def test_fix5_only_the_engine_reads_the_flag_below_the_api():
    """The flag is read at ``ai.permissions`` and nowhere else on the permission path: the MCP
    client, the connector service and the routers take the choice from their caller."""
    import ast
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[1] / "backend"
    readers = set()
    for path in backend.rglob("*.py"):
        rel = str(path.relative_to(backend))
        if rel.startswith(("core/feature_flags", "services/feature_flags")):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "enabled"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in ("permissions_v2",)
            ):
                readers.add(rel)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "enabled"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "FLAG"
            ):
                readers.add(rel)
    assert readers == {"ai/permissions.py"}


# ── fix 7: the Antigravity CLI cannot be limited to no tools, so it cannot be the judge ───────


@pytest.mark.asyncio
async def test_fix7_the_antigravity_cli_refuses_to_be_the_judge_without_spawning(monkeypatch):
    from backend.ai.models.antigravity_cli_provider import AntigravityCliProvider
    from backend.core.errors import ConfigurationError

    provider = AntigravityCliProvider(cli_path="agy")

    async def never(*_a, **_k):  # pragma: no cover - must not be reached
        raise AssertionError("the CLI must not be spawned for the judge")

    monkeypatch.setattr(provider, "_run_subprocess", never)

    with pytest.raises(ConfigurationError, match="Auto-approval judge"):
        await provider.complete("judge this", source="auto_approval")

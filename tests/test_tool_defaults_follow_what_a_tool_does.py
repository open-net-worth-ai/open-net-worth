"""One permission model for every tool: the SEED follows what a tool does, the row is the value.

A tool that only reads is seeded Always allow; one that changes something of the owner's, or
sends text out, is seeded Ask — the same for a built-in tool and a connector's own (enabling a
connector is the access grant, so asking again per read adds nothing). Whatever the seed, the
owner can set ANY tool, read or write, to Ask or Block, and the mode only decides what Ask
means (manual: a card, auto: the judge, skip: it runs). External MCP tools are the one
exception: nothing says what they do, so they are seeded Ask (ADRs
``2026-10-11-tool-defaults-follow-what-a-tool-does`` and
``2026-10-11-permissions-are-one-value-per-tool``).

These drive the real gate (``should_gate_dispatch`` / ``effective_level``) and never run a
handler, so no test here touches the network.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import _confirm_gate as gate
from backend.ai.tools import registry
from backend.core.automation_runner import (
    AutomationApprovalContext,
    bind_automation_approval,
    reset_automation_approval,
)
from backend.core.conversation_approval import (
    ConversationApprovalContext,
    bind_conversation_approval,
    reset_conversation_approval,
)
from backend.core.tool_use_context import set_tool_use_id
from backend.services import connectors as connectors_service
from backend.services import tool_permissions as perms

MODES = ("manual", "auto", "skip")

CORE_READ = "get_net_worth"
CONNECTOR_READS = (
    ("duckduckgo_search", "web_search:duckduckgo"),
    ("plaid_get_status", "plaid"),
    ("plaid_fetch_transactions", "plaid"),
    ("coingecko_get_crypto_prices", "coingecko"),
    ("email_search_messages", "email"),
)
CONNECTOR_WRITES = (
    ("telegram_send_message", "telegram"),
    ("webhook_send_message", "webhook"),
    ("email_send_message", "email"),
)


@pytest.fixture()
def surface(db_isolated, monkeypatch):
    """A card-capable surface, a bound tool_use_id and an EMPTY permission table."""
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    set_tool_use_id("toolu_defaults")
    yield
    set_tool_use_id(None)


def _in_chat(mode: str):
    return bind_conversation_approval(ConversationApprovalContext(session_id="s1", mode=mode))


def _in_automation(mode: str):
    return bind_automation_approval(
        AutomationApprovalContext(automation_id="a1", run_id="r1", session_id="s1", mode=mode)
    )


def _asks_in_chat(tool: str, mode: str) -> bool:
    token = _in_chat(mode)
    try:
        return gate.should_gate_dispatch(tool)
    finally:
        reset_conversation_approval(token)


def _asks_in_automation(tool: str, mode: str) -> bool:
    token = _in_automation(mode)
    try:
        return gate.should_gate_dispatch(tool)
    finally:
        reset_automation_approval(token)


def _owner(tool: str) -> str:
    from backend.ai.tools._connector_gate import permission_owner

    return permission_owner(tool)


# ── the default: a read runs, a change or a send asks — core and connector alike ──────────


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", [CORE_READ, *[t for t, _ in CONNECTOR_READS]])
def test_an_undecided_read_runs_in_every_chat_mode(surface, tool, mode):
    assert gate.effective_level(tool) == "always_allow"
    assert _asks_in_chat(tool, mode) is False


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", [CORE_READ, *[t for t, _ in CONNECTOR_READS]])
def test_an_undecided_read_runs_in_every_automation_mode(surface, tool, mode):
    """Even a ``manual`` Automation, which pauses on every approval-tier call, does not pause
    on a read."""
    assert _asks_in_automation(tool, mode) is False


@pytest.mark.parametrize("tool", [t for t, _ in CONNECTOR_WRITES] + ["set_connector_enabled"])
def test_an_undecided_connector_write_or_send_asks_in_manual(surface, tool):
    assert gate.effective_level(tool) == "needs_approval"
    assert _asks_in_chat(tool, "manual") is True


def test_the_seed_is_one_function_for_core_and_connector_tools(surface):
    assert perms.seed_level(CORE_READ) == "always_allow"
    assert perms.seed_level("duckduckgo_search") == "always_allow"
    assert perms.seed_level("add_transactions") == "needs_approval"
    assert perms.seed_level("telegram_send_message") == "needs_approval"
    assert perms.seed_level("plaid_get_status") == "always_allow"
    assert perms.seed_level("email_send_message") == "needs_approval"


def test_the_seed_does_not_depend_on_who_owns_the_tool(surface):
    """A ``connector=`` tag says whose row applies — never what the tool is seeded with."""
    for tool, owner in CONNECTOR_READS:
        assert _owner(tool) == owner
        assert perms.seed_level(tool) == perms.seed_level(CORE_READ) == "always_allow"
    for tool, owner in CONNECTOR_WRITES:
        assert _owner(tool) == owner
        assert perms.seed_level(tool) == perms.seed_level("create_payees") == "needs_approval"


# ── the row is the value, for any tool; the mode only decides what Ask means ──────────────

# manual shows a card; auto hands the call to the judge (no verdict in hand reads as a card);
# skip runs it.
ASK_MEANS = {"manual": True, "auto": True, "skip": False}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", [CORE_READ, *[t for t, _ in CONNECTOR_READS]])
def test_a_read_set_to_ask_follows_the_chat_mode(surface, tool, mode):
    perms.set_tool_permission(_owner(tool), tool, "needs_approval")

    assert gate.effective_level(tool) == "needs_approval"
    assert _asks_in_chat(tool, mode) is ASK_MEANS[mode], f"{tool} in {mode}"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", [CORE_READ, *[t for t, _ in CONNECTOR_READS]])
def test_a_read_set_to_ask_follows_the_automation_mode(surface, tool, mode):
    perms.set_tool_permission(_owner(tool), tool, "needs_approval")

    assert _asks_in_automation(tool, mode) is ASK_MEANS[mode], f"{tool} in {mode}"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [CORE_READ, "duckduckgo_search", "plaid_fetch_transactions"])
async def test_a_stored_blocked_on_a_read_is_refused(surface, tool):
    perms.set_tool_permission(_owner(tool), tool, "blocked")

    text, proposed = await registry.dispatch(tool, {}, tool_use_id="toolu_blocked_read")

    assert proposed is None
    assert json.loads(text)["blocked_by"] == "tool_permission"


@pytest.mark.parametrize("mode", MODES)
def test_a_stored_always_allow_on_a_write_runs_in_chat(surface, mode):
    perms.set_tool_permission(_owner("create_payees"), "create_payees", "always_allow")

    assert gate.effective_level("create_payees") == "always_allow"
    assert _asks_in_chat("create_payees", mode) is False


def test_a_stored_always_allow_on_a_connector_send_runs_in_chat(surface):
    perms.set_tool_permission("telegram", "telegram_send_message", "always_allow")

    assert _asks_in_chat("telegram_send_message", "manual") is False


def test_resetting_a_read_puts_it_back_to_running(surface):
    perms.set_tool_permission("plaid", "plaid_get_status", "needs_approval")
    assert _asks_in_chat("plaid_get_status", "manual") is True

    assert perms.reset_tool_permission("plaid", "plaid_get_status") == "always_allow"

    assert _asks_in_chat("plaid_get_status", "manual") is False


# ── external MCP is unchanged ────────────────────────────────────────────────────────────


def test_an_undecided_external_mcp_tool_still_needs_approval(db_isolated):
    assert perms.get_tool_permission("mcp:srv", "search") == "needs_approval"
    assert perms.get_tool_permission("mcp:srv", "get_balance") == "needs_approval"
    perms.set_tool_permission("mcp:srv", "search", "always_allow")
    assert perms.get_tool_permission("mcp:srv", "search") == "always_allow"


def test_the_ladder_shows_an_external_mcp_tool_asking_by_default(db_isolated):
    from backend.models.connector import ConnectorTool, ConnectorView

    view = ConnectorView(
        id="mcp:srv", kind="mcp", tools=[ConnectorTool(name="search", source="mcp")]
    )
    assert connectors_service.view_default_levels(view) == (
        "needs_approval",
        {"search": "needs_approval"},
    )
    assert connectors_service.default_levels("mcp:srv") == ("needs_approval", {})


# ── the ladder shows what dispatch does ──────────────────────────────────────────────────


def test_the_ladder_shows_exactly_what_dispatch_does(db_isolated):
    seen = 0
    for connector_id in ("plaid", "telegram", "webhook", "email", "web_search:duckduckgo"):
        for t in connectors_service.get_connector(connector_id).tools:
            assert t.level == gate.effective_level(t.name), (connector_id, t.name)
            seen += 1
    assert seen >= 8


def test_the_ladder_reports_per_tool_defaults(db_isolated):
    default_level, per_tool = connectors_service.default_levels("email")
    assert default_level == "always_allow"
    assert per_tool["email_search_messages"] == "always_allow"
    assert per_tool["email_send_message"] == "needs_approval"


def test_a_stored_level_shows_on_the_ladder_beside_the_default(db_isolated):
    perms.set_tool_permission("plaid", "plaid_get_status", "blocked")
    view = connectors_service.get_connector("plaid")
    levels = {t.name: t.level for t in view.tools}
    _, defaults = connectors_service.view_default_levels(view)
    assert levels["plaid_get_status"] == "blocked"
    assert defaults["plaid_get_status"] == "always_allow"


# ── web_search / fetch_page belong to the ACTIVE search connector ────────────────────────
#
# They call the active provider directly, so they have no permission row of their own: the
# search connector's page (the provider's own `<id>_search` / `<id>_fetch_page`) is their one
# control, and a level set on it cannot be bypassed by calling the generic tool.

GENERIC = (
    ("web_search", "duckduckgo_search"),
    ("fetch_page", "duckduckgo_fetch_page"),
)


@pytest.fixture()
def duckduckgo_active(db_isolated):
    from backend.db.search_config_store import SearchConfigStore

    assert SearchConfigStore().default_provider() == "duckduckgo"


@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
def test_the_generic_tool_resolves_to_the_active_providers_own_tool(
    duckduckgo_active, generic, provider_tool
):
    from backend.ai.tools._connector_gate import permission_key
    from backend.ai.tools.web_search_providers import active_provider_tool

    assert active_provider_tool(generic) == provider_tool
    assert permission_key(generic) == ("web_search:duckduckgo", provider_tool)
    assert active_provider_tool("get_net_worth") is None
    assert permission_key("get_net_worth") == ("core", "get_net_worth")


def test_x_search_is_retired_not_aliased(duckduckgo_active):
    from backend.scripts.tool_renames import RENAMES

    assert "x_search" not in registry.registered_tool_names()
    assert RENAMES["x_search"].startswith("retired — web_search")


@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
def test_an_undecided_generic_tool_runs(duckduckgo_active, generic, provider_tool):
    assert gate.effective_level(generic) == "always_allow"


@pytest.mark.asyncio
@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
async def test_blocking_the_provider_tool_blocks_the_generic_tool(
    surface, duckduckgo_active, generic, provider_tool
):
    perms.set_tool_permission(_owner(provider_tool), provider_tool, "blocked")

    assert gate.effective_level(generic) == "blocked"
    text, proposed = await registry.dispatch(generic, {}, tool_use_id="toolu_generic")
    assert proposed is None
    assert json.loads(text)["blocked_by"] == "tool_permission"


@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
def test_asking_on_the_provider_tool_makes_the_generic_tool_ask(
    surface, duckduckgo_active, generic, provider_tool
):
    perms.set_tool_permission(_owner(provider_tool), provider_tool, "needs_approval")

    assert gate.effective_level(generic) == "needs_approval"
    for mode in MODES:
        assert _asks_in_chat(generic, mode) is ASK_MEANS[mode], (generic, mode)


def test_a_stray_core_row_for_the_generic_tool_is_never_read(surface, duckduckgo_active):
    """No second control: whatever sits under ``core:web_search`` cannot disagree with the
    connector, because nothing consults it."""
    perms.set_tool_permission(perms.CORE_CONNECTOR_ID, "web_search", "blocked")

    assert gate.effective_level("web_search") == "always_allow"


def test_a_level_on_a_provider_that_is_not_active_does_not_reach_the_generic_tool(
    surface, duckduckgo_active
):
    perms.set_tool_permission(_owner("tavily_search"), "tavily_search", "blocked")

    assert gate.effective_level("web_search") == "always_allow"


@pytest.mark.parametrize(("generic", "provider_tool"), GENERIC)
def test_setting_a_level_on_the_generic_tool_sets_it_on_the_search_connector(
    db_isolated, duckduckgo_active, generic, provider_tool
):
    """The REST route (and so Settings → Permissions) never writes a ``core`` row for it."""
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        res = client.put(f"/api/tool-permissions/{generic}", json={"level": "blocked"})
        assert res.status_code == 200
        assert perms.stored_tool_level("web_search:duckduckgo", provider_tool) == "blocked"
        assert perms.stored_tool_level(perms.CORE_CONNECTOR_ID, generic) is None
        assert gate.effective_level(generic) == "blocked"

        # Reset puts the PROVIDER's row back to its seed; the generic tool never gets one.
        assert client.delete(f"/api/tool-permissions/{generic}").status_code == 200
        assert perms.stored_tool_level("web_search:duckduckgo", provider_tool) == "always_allow"
        assert perms.stored_tool_level(perms.CORE_CONNECTOR_ID, generic) is None


def test_a_chat_always_allow_tap_on_the_generic_tool_lands_on_the_connector(
    db_isolated, duckduckgo_active
):
    from backend.connectors.chat.grants import record_grant

    assert record_grant("web_search", "always") is True
    assert perms.stored_tool_level("web_search:duckduckgo", "duckduckgo_search") == "always_allow"
    assert perms.stored_tool_level(perms.CORE_CONNECTOR_ID, "web_search") is None


def test_the_provider_tool_names_come_from_the_provider_registry(duckduckgo_active):
    from backend.ai.search.base import PROVIDER_REGISTRY
    from backend.ai.tools.web_search_providers import _PROVIDER_TOOLS

    for spec in PROVIDER_REGISTRY:
        for verb in ("search", "fetch"):
            assert _PROVIDER_TOOLS[(spec.id, verb)] in registry.registered_tool_names()


def test_the_search_connector_page_says_it_also_serves_the_generic_tools(db_isolated):
    tools = {t.name: t for t in connectors_service.get_connector("web_search:duckduckgo").tools}
    assert "general web search" in tools["duckduckgo_search"].description
    assert "page reading" in tools["duckduckgo_fetch_page"].description

"""Every tool has ONE value, and a row is seeded the day the tool appears (ADR
``2026-10-11-permissions-are-one-value-per-tool``).

Pins ``services.tool_permissions.sync_seeds`` / ``seed_discovered_tools``:

  - a fresh sync gives EVERY registry tool a row — a read ``always_allow``, a change or a send
    ``needs_approval`` — and none to ``web_search`` / ``fetch_page`` (the active provider's tool is
    their row) or to a connector withdrawn by policy;
  - it is idempotent, and a row that is still a seed follows a changed seed rule while a row the
    owner set never does;
  - a seed row for a tool that no longer exists is removed, a decision about one is kept, and an
    empty catalog ("cannot say") prunes nothing;
  - the retired YOLO switch is carried forward once, as per-tool Always allow rows, then deleted;
  - an external MCP server's discovered tools are seeded Ask, at discovery and at boot.
"""

from __future__ import annotations

import pytest

from backend.ai.tools import _confirm_gate as gate
from backend.ai.tools import registry
from backend.core import tool_registry_seam
from backend.db.tool_permission_store import ToolPermissionStore
from backend.services import tool_permissions as perms

CORE = perms.CORE_CONNECTOR_ID


def _rows() -> dict[tuple[str, str], dict]:
    return {(r["connector_id"], r["tool"]): r for r in ToolPermissionStore().all()}


@pytest.fixture()
def fresh(db_isolated):
    """An empty permission table on an isolated DB, with the real registry installed."""
    assert ToolPermissionStore().all() == []
    return db_isolated


# ── a fresh sync ────────────────────────────────────────────────────────────────────────


def test_a_fresh_sync_gives_every_registry_tool_a_row_at_its_seed(fresh):
    counts = perms.sync_seeds()

    rows = _rows()
    catalog = tool_registry_seam.tool_catalog()
    assert counts == {"added": len(catalog), "updated": 0, "pruned": 0}
    assert set(rows) == {(owner, tool) for owner, tool, _ in catalog}
    assert len(rows) > 150
    for (owner, tool), row in rows.items():
        assert row["source"] == "seed"
        assert row["level"] == perms.seed_level(tool), (owner, tool)

    assert rows[(CORE, "get_net_worth")]["level"] == "always_allow"  # a read
    assert rows[(CORE, "add_transactions")]["level"] == "needs_approval"  # a change
    assert rows[("email", "email_send_message")]["level"] == "needs_approval"  # a send
    assert rows[("plaid", "plaid_get_status")]["level"] == "always_allow"  # a connector read


def test_a_read_that_only_refreshes_a_screen_is_seeded_always_allow(fresh):
    perms.sync_seeds()

    rows = _rows()
    seeded = [
        (registry.tool_connector(tool) or CORE, tool)
        for tool in registry.refreshing_tool_domains()
        if (registry.tool_connector(tool) or CORE, tool) in rows
    ]
    assert len(seeded) >= 10
    assert all(rows[key]["level"] == "always_allow" for key in seeded), seeded


def test_the_generic_search_tools_get_no_row_of_their_own(fresh):
    perms.sync_seeds()

    tools = {tool for _, tool in _rows()}
    assert not {"web_search", "fetch_page"} & tools
    assert {"duckduckgo_search", "duckduckgo_fetch_page", "tavily_search"} <= tools


def test_a_connector_withdrawn_by_policy_gets_no_rows(fresh):
    perms.sync_seeds()

    assert not [key for key in _rows() if key[0] == "yodlee"]


# ── idempotent; seed rule changes move seed rows only ───────────────────────────────────


def test_a_second_sync_changes_nothing(fresh):
    perms.sync_seeds()
    before = _rows()

    assert perms.sync_seeds() == {"added": 0, "updated": 0, "pruned": 0}
    assert _rows() == before  # not even an updated_at moved


def test_a_changed_seed_rule_moves_seed_rows_and_never_user_rows(fresh, monkeypatch):
    perms.sync_seeds()
    perms.set_tool_permission(
        CORE, "get_accounts", "always_allow"
    )  # the owner's, equal to the seed
    assert perms.get_tool_permission(CORE, "list_accounts") == "always_allow"

    # The rule changes: every read now asks.
    monkeypatch.setattr(gate, "is_gated_tool", lambda name: True)
    counts = perms.sync_seeds()

    rows = _rows()
    assert counts["updated"] > 100
    assert rows[(CORE, "get_net_worth")]["level"] == "needs_approval"  # a seed row followed it
    assert rows[(CORE, "get_net_worth")]["source"] == "seed"
    assert rows[(CORE, "get_accounts")]["level"] == "always_allow"  # a user row did not
    assert rows[(CORE, "get_accounts")]["source"] == "user"


def test_a_value_the_owner_set_survives_every_later_sync(fresh):
    perms.sync_seeds()
    perms.set_tool_permission(CORE, "add_transactions", "blocked")
    perms.set_tool_permission(CORE, "get_net_worth", "needs_approval")

    perms.sync_seeds()
    perms.sync_seeds()

    assert perms.stored_tool_level(CORE, "add_transactions") == "blocked"
    assert perms.stored_tool_level(CORE, "get_net_worth") == "needs_approval"


def test_reset_hands_the_row_back_to_the_seed_rule(fresh, monkeypatch):
    perms.sync_seeds()
    perms.set_tool_permission(CORE, "get_net_worth", "blocked")
    perms.reset_tool_permission(CORE, "get_net_worth")

    monkeypatch.setattr(gate, "is_gated_tool", lambda name: True)
    perms.sync_seeds()

    assert perms.stored_tool_level(CORE, "get_net_worth") == "needs_approval"  # followed the rule


# ── pruning ─────────────────────────────────────────────────────────────────────────────


def test_a_seed_row_for_a_retired_tool_is_removed_and_a_decision_about_one_is_kept(fresh):
    perms.sync_seeds()
    store = ToolPermissionStore()
    store.set_many({(CORE, "retired_seed_tool"): ("always_allow", "seed")})
    store.set_level(CORE, "retired_user_tool", "blocked")

    counts = perms.sync_seeds()

    assert counts["pruned"] == 1
    assert (CORE, "retired_seed_tool") not in _rows()
    assert _rows()[(CORE, "retired_user_tool")]["level"] == "blocked"


def test_an_empty_catalog_means_cannot_say_and_prunes_nothing(fresh, monkeypatch):
    perms.sync_seeds()
    before = _rows()
    monkeypatch.setattr(tool_registry_seam, "_catalog_provider", None)

    assert perms.sync_seeds() == {"added": 0, "updated": 0, "pruned": 0}
    assert _rows() == before


# ── the retired YOLO switch, carried forward once ──────────────────────────────────────


def test_an_armed_yolo_becomes_per_tool_always_allow_and_the_row_is_deleted(fresh):
    store = ToolPermissionStore()
    store.set_level(CORE, "*", "always_allow")  # the retired switch
    store.set_level(CORE, "create_payees", "needs_approval")  # the owner explicitly asked
    store.set_level("email", "email_send_message", "blocked")

    perms.sync_seeds()

    rows = _rows()
    assert (CORE, "*") not in rows
    # Every tool that would have been seeded Ask runs, as the owner's own value now...
    assert rows[(CORE, "add_transactions")]["level"] == "always_allow"
    assert rows[(CORE, "add_transactions")]["source"] == "user"
    assert rows[("telegram", "telegram_send_message")]["level"] == "always_allow"
    # ...reads are untouched seeds...
    assert rows[(CORE, "get_net_worth")]["source"] == "seed"
    # ...and what YOLO never overrode stays: an explicit Ask, an explicit Block.
    assert rows[(CORE, "create_payees")]["level"] == "needs_approval"
    assert rows[("email", "email_send_message")]["level"] == "blocked"
    assert not [
        k for k, r in rows.items() if r["level"] == "needs_approval" and k[1] != "create_payees"
    ]


def test_yolo_never_reached_external_mcp_tools_and_does_not_start_to(fresh):
    ToolPermissionStore().set_level(CORE, "*", "always_allow")
    perms.seed_discovered_tools("mcp:drive", ["delete_file"])

    perms.sync_seeds()

    assert perms.get_tool_permission("mcp:drive", "delete_file") == "needs_approval"


def test_a_disarmed_yolo_row_is_just_deleted(fresh):
    ToolPermissionStore().set_level(CORE, "*", "needs_approval")

    perms.sync_seeds()

    rows = _rows()
    assert (CORE, "*") not in rows
    assert rows[(CORE, "add_transactions")]["level"] == "needs_approval"


def test_the_yolo_carry_runs_once(fresh):
    ToolPermissionStore().set_level(CORE, "*", "always_allow")
    perms.sync_seeds()
    perms.set_tool_permission(CORE, "add_transactions", "needs_approval")  # the owner re-asks

    perms.sync_seeds()

    assert perms.stored_tool_level(CORE, "add_transactions") == "needs_approval"


def test_a_yolo_migrated_install_runs_a_tool_it_used_to_skip_the_card_for(fresh):
    """The point of carrying it: behaviour is preserved at dispatch, not just in the table."""
    ToolPermissionStore().set_level(CORE, "*", "always_allow")
    perms.sync_seeds()

    assert gate.effective_level("add_transactions") == "always_allow"
    assert gate.should_gate("add_transactions") is False


# ── external MCP tools: seeded Ask when discovered ──────────────────────────────────────


def test_discovered_mcp_tools_are_seeded_ask(fresh):
    perms.seed_discovered_tools("mcp:drive", ["list_files", "delete_file", "list_files", ""])

    rows = _rows()
    assert {k for k in rows if k[0] == "mcp:drive"} == {
        ("mcp:drive", "list_files"),
        ("mcp:drive", "delete_file"),
    }
    assert all(r["level"] == "needs_approval" and r["source"] == "seed" for r in rows.values())


def test_rediscovery_keeps_what_the_owner_set_and_drops_withdrawn_seed_rows(fresh):
    perms.seed_discovered_tools("mcp:drive", ["list_files", "delete_file", "old_tool"])
    perms.set_tool_permission("mcp:drive", "list_files", "always_allow")

    perms.seed_discovered_tools("mcp:drive", ["list_files", "delete_file", "new_tool"])

    assert perms.get_connector_permissions("mcp:drive") == {
        "list_files": "always_allow",
        "delete_file": "needs_approval",
        "new_tool": "needs_approval",
    }


def test_a_failed_discovery_reports_no_tools_and_withdraws_nothing(fresh):
    perms.seed_discovered_tools("mcp:drive", ["list_files"])

    perms.seed_discovered_tools("mcp:drive", [])

    assert perms.get_connector_permissions("mcp:drive") == {"list_files": "needs_approval"}


def test_the_manager_seeds_the_tools_a_successful_connect_reports(fresh):
    from backend.db.mcp_client_store import McpClientStore
    from backend.mcp.client_manager import McpClientManager
    from backend.models.mcp_client import McpServerConfig

    cfg = McpServerConfig(
        id="drive", name="Drive", transport="http", url="http://fake/mcp", enabled=True
    )
    McpClientStore().create(cfg)
    manager = McpClientManager()

    manager._record_discovered(cfg, [{"name": "list_files"}, {"name": "delete_file"}], {})

    assert perms.get_connector_permissions("mcp:drive") == {
        "list_files": "needs_approval",
        "delete_file": "needs_approval",
    }


def test_boot_seeds_the_tools_an_existing_server_already_reported(fresh):
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    McpClientStore().create(
        McpServerConfig(
            id="drive",
            name="Drive",
            transport="http",
            url="http://fake/mcp",
            enabled=True,
            discovered_tools=[{"name": "list_files"}],
        )
    )

    perms.sync_seeds()

    assert perms.get_connector_permissions("mcp:drive") == {"list_files": "needs_approval"}


def test_a_deleted_servers_seed_rows_go_with_it_at_the_next_sync(fresh):
    perms.seed_discovered_tools("mcp:gone", ["list_files"])
    perms.sync_seeds()

    assert perms.get_connector_permissions("mcp:gone") == {}


# ── seed by declaration: workspace writes Allow, code and sends Ask ───────────────────────

WORKSPACE_ALLOW = (
    "remember_fact",
    "forget_fact",
    "create_skill",
    "update_skill",
    "delete_skill",
    "save_code_as_skill",
    "create_artifact",
    "mark_notifications_read",
)


def test_a_fresh_sync_seeds_workspace_writes_allow_and_code_and_sends_ask(fresh):
    perms.sync_seeds()

    rows = _rows()
    for tool in WORKSPACE_ALLOW:
        assert rows[(CORE, tool)]["level"] == "always_allow", tool
        assert rows[(CORE, tool)]["source"] == "seed", tool
    for owner, tool in (
        (CORE, "run_code"),
        (CORE, "run_script"),
        ("email", "email_send_message"),
        ("telegram", "telegram_send_message"),
        ("webhook", "webhook_send_message"),
        (CORE, "add_transactions"),  # a book write
        (CORE, "update_model_config"),  # settings
    ):
        assert rows[(owner, tool)]["level"] == "needs_approval", tool


def test_an_existing_install_moves_its_seed_rows_to_the_new_rule_and_keeps_the_owners(fresh):
    """The design: still-seed rows follow the seed rule at boot. An install seeded under the old
    rule has remember_fact / create_skill at Ask and run_code at Allow (a read); the next sync
    corrects both, and a row the owner set is never touched."""
    store = ToolPermissionStore()
    store.set_many(
        {
            (CORE, "remember_fact"): ("needs_approval", "seed"),
            (CORE, "create_skill"): ("needs_approval", "seed"),
            (CORE, "run_code"): ("always_allow", "seed"),
            (CORE, "create_artifact"): ("needs_approval", "seed"),
        }
    )
    store.set_level(CORE, "create_artifact", "needs_approval")  # the owner's own Ask
    store.set_level(CORE, "run_script", "always_allow")  # the owner's own Allow

    counts = perms.sync_seeds()

    rows = _rows()
    assert counts["updated"] >= 3
    assert rows[(CORE, "remember_fact")]["level"] == "always_allow"
    assert rows[(CORE, "create_skill")]["level"] == "always_allow"
    assert rows[(CORE, "run_code")]["level"] == "needs_approval"
    assert rows[(CORE, "create_artifact")]["level"] == "needs_approval"  # user row untouched
    assert rows[(CORE, "create_artifact")]["source"] == "user"
    assert rows[(CORE, "run_script")]["level"] == "always_allow"  # user row untouched


def test_a_workspace_write_runs_without_a_card_until_the_owner_sets_it_to_ask(fresh, monkeypatch):
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    perms.sync_seeds()

    assert gate.effective_level("remember_fact") == "always_allow"
    assert gate.should_gate("remember_fact") is False

    perms.set_tool_permission(CORE, "remember_fact", "needs_approval")
    from backend.core.tool_use_context import set_tool_use_id

    set_tool_use_id("toolu_seed_ws")
    try:
        assert gate.should_gate("remember_fact") is True
    finally:
        set_tool_use_id(None)


# ── a tool with no row yet still reads its seed at dispatch ─────────────────────────────


def test_a_registry_tool_with_no_row_falls_back_to_the_seed_never_to_permission_or_refusal(fresh):
    assert ToolPermissionStore().all() == []

    assert gate.effective_level("get_net_worth") == "always_allow"
    assert gate.effective_level("add_transactions") == "needs_approval"

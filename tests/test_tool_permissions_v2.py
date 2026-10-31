"""The per-tool permission ladder — service + REST surface (Connectors redesign §4,
design handoff §10; absorbed ``tool_grants``' own coverage per the one-permission-store
merge, ADR ``2026-08-14-one-permission-store-for-every-tool``).

The properties this lane exists to guarantee:
  - a pair with no row reads ``needs_approval`` — cautious by default, uniformly, for every
    connector lane (Channels, Data sources, Web AND MCP) with no tiering by tool type and
    no exceptions (what a REGISTRY tool with no row is worth is
    ``tests/test_permissions_v2_read_time_rules.py``);
  - a group pick is a deliberately LOSSY stamp onto every tool named;
  - the group aggregate ("Custom") is DERIVED and NEVER stored — there is no fourth level
    anywhere in the storage or the API;
  - a level survives what must not undo it (a /rewind) and is undone only by Reset, which DELETES
    the row so the tool reads its seed again — nothing is ever written as a default.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core.errors import ValidationError
from backend.services import tool_permissions_v2 as svc

pytestmark = pytest.mark.flags(permissions_v2=True)


@pytest.fixture()
def perms(db_isolated):
    # The service constructs ToolPermissionStore() fresh, with engine=None, on every
    # call — never cached — so db_isolated's engine redirect already reaches it.
    return svc


@pytest.fixture()
def client(perms):
    from backend.main import app

    return TestClient(app)


# ── Service: the default ──────────────────────────────────────────────────────


def test_a_pair_with_no_row_reads_the_cautious_default(perms):
    assert perms.DEFAULT_LEVEL == "needs_approval"
    assert perms.get_tool_permission("plaid", "plaid_sync_now") == "needs_approval"


def test_get_never_raises_on_an_unknown_connector_or_tool(perms):
    """ "I have never heard of this" and "the user has not decided" must be the SAME
    cautious answer — this read runs on every dispatch."""
    assert perms.get_tool_permission("no_such_connector", "no_such_tool") == "needs_approval"
    assert perms.get_tool_permission("", "") == "needs_approval"
    assert perms.get_connector_permissions("no_such_connector") == {}


def test_the_default_is_uniform_across_every_lane_including_mcp_and_money_tools(perms):
    """No tiering by tool type and no exception list: a trade tool on a third-party MCP
    server starts exactly where a read tool on a native channel does."""
    for connector_id, tool in (
        ("telegram", "send_message"),
        ("email", "email_search_messages"),
        ("web_search:tavily", "tavily_search"),
        ("mcp:ibkr", "place_order"),
        ("mcp:ibkr", "get_positions"),
    ):
        assert perms.get_tool_permission(connector_id, tool) == "needs_approval"


# ── Service: writes ───────────────────────────────────────────────────────────


def test_set_then_get_round_trips_every_level(perms):
    for level in ("always_allow", "needs_approval", "blocked"):
        perms.set_tool_permission("plaid", "plaid_sync_now", level)
        assert perms.get_tool_permission("plaid", "plaid_sync_now") == level


def test_a_money_movement_tool_can_be_raised_to_always_allow(perms):
    """The removed hard floor, pinned: nothing is structurally locked out of
    ``always_allow``."""
    perms.set_tool_permission("mcp:ibkr", "place_order", "always_allow")
    assert perms.get_tool_permission("mcp:ibkr", "place_order") == "always_allow"


def test_get_connector_permissions_returns_only_stored_decisions(perms):
    perms.set_tool_permission("plaid", "plaid_sync_now", "always_allow")
    stored = perms.get_connector_permissions("plaid")
    assert stored == {"plaid_sync_now": "always_allow"}
    # The untouched sibling is ABSENT from the map, and the caller applies the default.
    assert "plaid_get_status" not in stored
    assert perms.get_tool_permission("plaid", "plaid_get_status") == perms.DEFAULT_LEVEL


def test_group_permission_is_deliberately_lossy(perms):
    perms.set_tool_permission("mcp:ibkr", "get_positions", "always_allow")
    perms.set_tool_permission("mcp:ibkr", "place_order", "blocked")
    perms.set_group_permission("mcp:ibkr", ["get_positions", "place_order"], "needs_approval")
    assert perms.get_connector_permissions("mcp:ibkr") == {
        "get_positions": "needs_approval",
        "place_order": "needs_approval",
    }


def test_group_permission_is_scoped_to_its_own_connector(perms):
    perms.set_tool_permission("plaid", "plaid_sync_now", "always_allow")
    perms.set_group_permission("mcp:ibkr", ["place_order"], "blocked")
    assert perms.get_tool_permission("plaid", "plaid_sync_now") == "always_allow"


def test_group_permission_of_no_tools_is_a_no_op(perms):
    perms.set_group_permission("telegram", [], "always_allow")
    assert perms.get_connector_permissions("telegram") == {}


def test_unknown_level_is_refused(perms):
    with pytest.raises(ValidationError):
        perms.set_tool_permission("plaid", "plaid_sync_now", "sometimes")
    with pytest.raises(ValidationError):
        perms.set_group_permission("plaid", ["plaid_sync_now"], "sometimes")
    assert perms.get_connector_permissions("plaid") == {}


def test_custom_is_never_a_settable_level(perms):
    """It is a derived GROUP label, not a value — setting it must fail, not persist."""
    with pytest.raises(ValidationError):
        perms.set_tool_permission("mcp:ibkr", "place_order", "custom")
    assert perms.get_connector_permissions("mcp:ibkr") == {}


def test_a_blank_connector_or_tool_is_refused_on_write(perms):
    with pytest.raises(ValidationError):
        perms.set_tool_permission("", "plaid_sync_now", "always_allow")
    with pytest.raises(ValidationError):
        perms.set_tool_permission("plaid", "  ", "always_allow")
    with pytest.raises(ValidationError):
        perms.set_group_permission("plaid", ["plaid_sync_now", ""], "always_allow")
    assert perms.get_connector_permissions("plaid") == {}


def test_reset_puts_a_registry_tool_back_to_its_seed_by_deleting_the_row(perms):
    perms.set_tool_permission("core", "add_transactions", "always_allow")
    perms.set_tool_permission("core", "get_net_worth", "blocked")
    perms.set_tool_permission("core", "email_send_message", "blocked")

    assert perms.reset_tool_permission("core", "add_transactions") == "needs_approval"
    assert perms.reset_tool_permission("core", "get_net_worth") == "always_allow"
    # The sibling decision is untouched.
    assert perms.get_tool_permission("core", "email_send_message") == "blocked"
    # No row survives: the seed is worked out when read, so a later change of the rule reaches it.
    assert perms.stored_tool_level("core", "add_transactions") is None
    assert perms.stored_tool_level("core", "get_net_worth") is None


def test_reset_of_a_tool_that_no_longer_exists_deletes_the_row(perms):
    perms.set_tool_permission("core", "a_tool_that_no_longer_exists", "always_allow")

    assert perms.reset_tool_permission("core", "a_tool_that_no_longer_exists") is None
    assert perms.stored_tool_level("core", "a_tool_that_no_longer_exists") is None


def test_reset_of_an_external_mcp_tool_goes_to_ask(perms):
    perms.set_tool_permission("mcp:ibkr", "place_order", "always_allow")

    assert perms.reset_tool_permission("mcp:ibkr", "place_order") == "needs_approval"


def test_resetting_a_connector_deletes_its_rows_stale_ones_included(perms):
    perms.set_tool_permission("plaid", "plaid_get_status", "blocked")
    perms.set_tool_permission("plaid", "plaid_sync_now", "always_allow")  # not a tool any more

    assert perms.reset_connector_permissions("plaid") == 2
    assert perms.get_connector_permissions("plaid") == {}
    assert perms.get_tool_permission("plaid", "plaid_get_status") == "needs_approval"  # no row
    assert perms.reset_connector_permissions("plaid") == 0
    assert perms.reset_connector_permissions("") == 0


def test_clearing_an_mcp_servers_permissions_removes_every_row(perms):
    perms.set_tool_permission("mcp:ibkr", "place_order", "always_allow")
    assert perms.clear_connector_permissions("mcp:ibkr") == 1
    assert perms.get_connector_permissions("mcp:ibkr") == {}
    assert perms.get_tool_permission("mcp:ibkr", "place_order") == "needs_approval"  # no row
    assert perms.clear_connector_permissions("mcp:ibkr") == 0


# ── Service: the DERIVED group aggregate ──────────────────────────────────────


def test_group_level_is_derived_uniform_or_custom(perms):
    assert perms.derive_group_level(["always_allow", "always_allow"]) == "always_allow"
    assert perms.derive_group_level(["blocked", "blocked", "blocked"]) == "blocked"
    assert perms.derive_group_level(["always_allow", "blocked"]) == "custom"
    assert perms.derive_group_level(["always_allow", "needs_approval"]) == "custom"


def test_an_unset_tool_agrees_with_an_explicit_default_sibling(perms):
    """One tool explicitly ``needs_approval`` beside an untouched one is UNIFORM, not
    Custom — the user sees one state because there is one effective state."""
    assert perms.derive_group_level(["needs_approval", None]) == "needs_approval"
    assert perms.derive_group_level([None, None]) == "needs_approval"
    assert perms.derive_group_level([]) == "needs_approval"
    assert perms.derive_group_level(["always_allow", None]) == "custom"


def test_custom_is_derived_from_the_stored_tools_and_never_itself_stored(perms):
    perms.set_group_permission("mcp:ibkr", ["get_positions", "place_order"], "always_allow")
    tools = ["get_positions", "place_order"]
    stored = perms.get_connector_permissions("mcp:ibkr")
    assert perms.derive_group_level([stored.get(t) for t in tools]) == "always_allow"

    perms.set_tool_permission("mcp:ibkr", "place_order", "blocked")
    stored = perms.get_connector_permissions("mcp:ibkr")
    assert perms.derive_group_level([stored.get(t) for t in tools]) == "custom"
    # ...and nothing anywhere in the store says "custom".
    assert set(stored.values()) == {"always_allow", "blocked"}


# ── REST ──────────────────────────────────────────────────────────────────────


def test_get_permissions_returns_the_default_and_an_empty_map(client):
    """A native connector's tools follow what they do — its reads run — and the response says
    so per tool, so a client can show "default" beside "set by you"."""
    res = client.get("/api/connectors/plaid/permissions")
    assert res.status_code == 200
    body = res.json()
    assert body["connector_id"] == "plaid"
    assert body["default_level"] == "always_allow"
    assert body["permissions"] == {}
    assert set(body["tool_defaults"].values()) <= {"always_allow", "needs_approval"}
    assert body["tool_defaults"]["plaid_get_status"] == "always_allow"
    assert body["tool_defaults"]["plaid_fetch_transactions"] == "always_allow"


def test_an_external_mcp_connector_reports_the_cautious_default(client):
    res = client.get("/api/connectors/mcp:ibkr/permissions")
    assert res.json()["default_level"] == "needs_approval"
    assert res.json()["permissions"] == {}


def test_a_native_connector_reports_its_sends_as_asking_by_default(client):
    body = client.get("/api/connectors/telegram/permissions").json()
    assert body["tool_defaults"]["telegram_send_message"] == "needs_approval"


def test_put_one_tool_returns_the_whole_updated_map(client):
    res = client.put(
        "/api/connectors/plaid/permissions/plaid_sync_now", json={"level": "always_allow"}
    )
    assert res.status_code == 200
    assert res.json()["permissions"] == {"plaid_sync_now": "always_allow"}
    assert client.get("/api/connectors/plaid/permissions").json()["permissions"] == {
        "plaid_sync_now": "always_allow"
    }


def test_put_group_stamps_every_named_tool(client):
    client.put("/api/connectors/mcp:ibkr/permissions/place_order", json={"level": "always_allow"})
    res = client.put(
        "/api/connectors/mcp:ibkr/permissions",
        json={"tools": ["place_order", "get_positions"], "level": "blocked"},
    )
    assert res.status_code == 200
    assert res.json()["permissions"] == {"place_order": "blocked", "get_positions": "blocked"}


def test_delete_resets_the_connector(client):
    client.put("/api/connectors/plaid/permissions/plaid_sync_now", json={"level": "always_allow"})
    res = client.delete("/api/connectors/plaid/permissions")
    assert res.status_code == 200
    assert res.json()["permissions"] == {}


def test_an_unknown_level_is_a_422_at_the_edge(client):
    assert (
        client.put(
            "/api/connectors/plaid/permissions/plaid_sync_now", json={"level": "sometimes"}
        ).status_code
        == 422
    )
    # "custom" is a group DISPLAY state, never a settable level — refused like any other
    # unknown value, so a confused client cannot persist a fourth level.
    assert (
        client.put(
            "/api/connectors/plaid/permissions", json={"tools": ["x"], "level": "custom"}
        ).status_code
        == 422
    )
    # The retired scope vocabulary (once/always/session) is not a level either — a stale
    # client that still speaks it must be refused loud, not silently coerced.
    assert (
        client.put(
            "/api/connectors/plaid/permissions/plaid_sync_now", json={"level": "always"}
        ).status_code
        == 422
    )


def test_an_mcp_connector_id_with_a_colon_round_trips_through_the_route(client):
    res = client.put("/api/connectors/mcp:ibkr/permissions/place_order", json={"level": "blocked"})
    assert res.status_code == 200
    assert res.json()["connector_id"] == "mcp:ibkr"
    assert res.json()["permissions"] == {"place_order": "blocked"}


def test_a_percent_encoded_connector_id_resolves_to_the_same_connector(client):
    """The frontend seam builds its URL with ``encodeURIComponent(entry.id)``
    (``components/tabs/connectors/permissions.ts``), so ``mcp:ibkr`` arrives as
    ``mcp%3Aibkr`` — it must land on the SAME rows as the unencoded form, or a level set
    through the UI would be invisible to the gate reading it back."""
    assert (
        client.put(
            "/api/connectors/mcp%3Aibkr/permissions/place_order", json={"level": "blocked"}
        ).status_code
        == 200
    )
    assert client.get("/api/connectors/mcp:ibkr/permissions").json()["permissions"] == {
        "place_order": "blocked"
    }
    assert svc.get_tool_permission("mcp:ibkr", "place_order") == "blocked"


def test_the_api_never_reports_a_group_level(client):
    """The group aggregate is computed where it is displayed (design handoff §10) — the
    server must not mint a second, coarser record that could disagree with the tools."""
    client.put(
        "/api/connectors/mcp:ibkr/permissions",
        json={"tools": ["a_tool", "b_tool"], "level": "always_allow"},
    )
    body = client.get("/api/connectors/mcp:ibkr/permissions").json()
    assert set(body) == {"connector_id", "default_level", "tool_defaults", "permissions"}
    assert "group" not in body and "groups" not in body


# ── Revoking grants when the endpoint moves ───────────────────────────────────


def test_revoke_allowed_tools_drops_allows_and_leaves_refusals_alone(perms):
    """Dropping a ``blocked`` would move that tool from refused to ``needs_approval`` — the
    permissive direction, which nothing about "the endpoint changed" argues for."""
    perms.set_tool_permission("mcp:srv", "place_order", "always_allow")
    perms.set_tool_permission("mcp:srv", "get_positions", "always_allow")
    perms.set_tool_permission("mcp:srv", "delete_all", "blocked")
    perms.set_tool_permission("mcp:srv", "read_thing", "needs_approval")
    perms.set_tool_permission("mcp:other", "place_order", "always_allow")

    assert perms.revoke_allowed_tools("mcp:srv") == 2
    assert perms.get_connector_permissions("mcp:srv") == {
        "delete_all": "blocked",
        "read_thing": "needs_approval",
    }
    # A revoked tool has no row again and reads Ask (an MCP tool's seed).
    assert perms.get_tool_permission("mcp:srv", "place_order") == "needs_approval"
    # Scoped to its own connector.
    assert perms.get_connector_permissions("mcp:other") == {"place_order": "always_allow"}


def test_revoke_allowed_tools_is_a_no_op_with_nothing_to_revoke(perms):
    assert perms.revoke_allowed_tools("mcp:srv") == 0
    assert perms.revoke_allowed_tools("") == 0


# ── L3: a group stamp is bounded ──────────────────────────────────────────────


def test_a_group_stamp_of_absurd_size_is_refused_at_the_edge(client):
    from backend.api.tool_permissions import MAX_GROUP_TOOLS

    over = client.put(
        "/api/connectors/mcp:srv/permissions",
        json={"tools": [f"tool_{i}" for i in range(MAX_GROUP_TOOLS + 1)], "level": "always_allow"},
    )
    assert over.status_code == 422
    assert svc.get_connector_permissions("mcp:srv") == {}

    long_name = client.put(
        "/api/connectors/mcp:srv/permissions",
        json={"tools": ["x" * 5_000], "level": "always_allow"},
    )
    assert long_name.status_code == 422

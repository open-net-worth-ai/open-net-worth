"""``/api/tool-permissions`` — every tool and its one value (Settings › Permissions, the Automation
tool picker). Approval model RFC 2026-07-20 §15.4/§16; one value per tool, ADR
``2026-10-11-permissions-are-one-value-per-tool``.

Covers the properties this surface exists to guarantee:
  - EVERY registry tool is listed with a real value and its seed (``default_level``), whether or
    not a row exists yet — there is no "undecided" row;
  - ``web_search`` / ``fetch_page`` are not listed (the active provider's own tool is their row);
  - a value is settable and Reset DELETES the row, so the tool reads its seed again (nothing is
    ever written as a default);
  - a stale (renamed-away) tool's row is still listed, still shows ``tool_exists: false``, and
    Reset clears it;
  - an unknown level 422s at the edge;
  - a decision made through the connector-scoped ladder and one made through this app-wide
    route are the SAME row — "one revoke surface" is a fact about the table, not a
    per-surface promise.

The retired ``once`` scope has no equivalent here at all: this endpoint's ``level`` is one
of the three stored levels, never a scope, and there is nothing to persist for a
single-call approval — that half of the old grant contract now lives entirely in
``connectors/chat/grants.py``/the web confirm card's own redemption path (still covered by
``tests/test_chat_confirm_grants.py``, unchanged by this merge).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.services import tool_permissions_v2 as svc

pytestmark = pytest.mark.flags(permissions_v2=True)


@pytest.fixture()
def client(db_isolated):
    from backend.main import app

    return TestClient(app)


def _row(client, tool: str, owner: str | None = None) -> dict:
    rows = client.get("/api/tool-permissions").json()["permissions"]
    return next(
        r for r in rows if r["tool"] == tool and (owner is None or r["connector_id"] == owner)
    )


# ── GET: every tool, with a value and a seed ───────────────────────────────────


def test_get_lists_every_registry_tool_with_its_seed_before_anything_is_stored(client):
    body = client.get("/api/tool-permissions").json()
    assert body["total"] == len(body["permissions"]) > 150

    write = _row(client, "add_transactions")
    assert write["connector_id"] == svc.CORE_CONNECTOR_ID
    assert write["level"] == write["default_level"] == "needs_approval"
    assert write["tool_exists"] is True
    assert write["description"]

    read = _row(client, "get_net_worth")
    assert read["level"] == read["default_level"] == "always_allow"

    # A connector tool is listed under its connector, with the same rule.
    assert _row(client, "plaid_get_status")["connector_id"] == "plaid"
    assert _row(client, "email_send_message")["level"] == "needs_approval"


def test_get_does_not_list_the_generic_search_tools(client):
    """``web_search`` / ``fetch_page`` have no row of their own: they run the active search
    provider's tool, which IS listed (under its connector)."""
    tools = {r["tool"] for r in client.get("/api/tool-permissions").json()["permissions"]}
    assert not {"web_search", "fetch_page"} & tools
    assert {"duckduckgo_search", "duckduckgo_fetch_page"} <= tools


def test_get_lists_built_in_tools_first(client):
    owners = [r["connector_id"] for r in client.get("/api/tool-permissions").json()["permissions"]]
    first_other = next(i for i, owner in enumerate(owners) if owner != svc.CORE_CONNECTOR_ID)
    assert set(owners[:first_other]) == {svc.CORE_CONNECTOR_ID}
    assert svc.CORE_CONNECTOR_ID not in owners[first_other:]


def test_a_stored_decision_shows_its_value_beside_the_seed(client):
    client.put("/api/tool-permissions/add_transactions", json={"level": "always_allow"})
    row = _row(client, "add_transactions")
    assert row["level"] == "always_allow"
    assert row["default_level"] == "needs_approval"
    assert row["updated_at"]


def test_a_withdrawn_connectors_tools_are_not_listed(client):
    """Yodlee is withdrawn by policy: dispatch refuses its tools, so they get no row."""
    tools = {r["tool"] for r in client.get("/api/tool-permissions").json()["permissions"]}
    assert not {t for t in tools if t.startswith("yodlee_")}


def test_discovered_external_mcp_tools_are_listed_and_ask_by_default(client):
    """A tool a server advertised has no row until the owner decides, yet is listed so it can be."""
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    McpClientStore().create(
        McpServerConfig(
            id="drive",
            name="Drive",
            url="http://fake/mcp",
            enabled=True,
            discovered_tools=[{"name": "list_files"}, {"name": "delete_file"}],
        )
    )
    assert svc.stored_tool_level("mcp:drive", "delete_file") is None

    row = _row(client, "delete_file", "mcp:drive")
    assert row["level"] == row["default_level"] == "needs_approval"
    assert row["tool_exists"] is True


# ── PUT: owner resolution, canonicalization, validation ────────────────────────


def test_put_resolves_the_owner_from_the_tools_connector_tag(client):
    """``plaid_fetch_transactions`` carries ``@tool(connector="plaid")`` — the route must resolve
    that owner itself so no client has to know (or guess) which bucket a tool belongs to."""
    body = client.put(
        "/api/tool-permissions/plaid_fetch_transactions", json={"level": "blocked"}
    ).json()
    row = next(r for r in body["permissions"] if r["tool"] == "plaid_fetch_transactions")
    assert row["connector_id"] == "plaid"
    assert row["level"] == "blocked"


def test_put_resolves_email_send_message_to_the_email_connector_not_core(client):
    """Regression: ``email_send_message`` used to be untagged, so a decision made from HERE (the
    app-wide surface) landed under ``core`` while the Email connector's own ladder wrote
    the SAME tool under ``email`` — two rows for one question, and dispatch consulted only
    the ``core`` one, so an app-wide ``always_allow`` never actually fired. Now both
    surfaces resolve to the same owner."""
    body = client.put(
        "/api/tool-permissions/email_send_message", json={"level": "always_allow"}
    ).json()
    row = next(r for r in body["permissions"] if r["tool"] == "email_send_message")
    assert row["connector_id"] == "email"
    assert row["level"] == "always_allow"
    assert svc.stored_tool_level("email", "email_send_message") == "always_allow"


def test_put_refuses_a_retired_name_rather_than_storing_an_unreachable_decision(client):
    """``add_transaction`` is retired and resolves to nothing (ADR
    2026-08-25-tool-names-are-final). Storing a permission under it would record a decision
    the gate can never look up — so it is refused, loudly, and nothing is written."""
    assert (
        client.put(
            "/api/tool-permissions/add_transaction", json={"level": "always_allow"}
        ).status_code
        == 422
    )
    assert svc.stored_tool_level(svc.CORE_CONNECTOR_ID, "add_transaction") is None


def test_put_rejects_an_unknown_tool_with_no_explicit_owner(client):
    assert (
        client.put(
            "/api/tool-permissions/nope_not_real", json={"level": "always_allow"}
        ).status_code
        == 422
    )


def test_put_rejects_an_unknown_level(client):
    assert (
        client.put("/api/tool-permissions/add_transactions", json={"level": "forever"}).status_code
        == 422
    )
    assert svc.stored_tool_level(svc.CORE_CONNECTOR_ID, "add_transactions") is None


def test_put_accepts_an_explicit_owner_for_a_name_the_registry_cannot_resolve(client):
    """An MCP server's tool is not a registry name at all — a caller-supplied
    ``connector_id`` must be taken at face value rather than requiring a registry hit."""
    r = client.put(
        "/api/tool-permissions/delete_file",
        json={"level": "blocked", "connector_id": "mcp:drive"},
    )
    assert r.status_code == 200
    row = next(row for row in r.json()["permissions"] if row["tool"] == "delete_file")
    assert row["connector_id"] == "mcp:drive"


# ── Reset puts the SEED back — a row, not an absence ───────────────────────────


def test_delete_resets_a_tool_to_its_seed(client):
    # email_send_message carries @tool(connector="email") — the route must resolve THAT owner
    # (never CORE_CONNECTOR_ID) with no explicit connector_id supplied.
    client.put("/api/tool-permissions/email_send_message", json={"level": "always_allow"})
    assert svc.stored_tool_level("email", "email_send_message") == "always_allow"

    r = client.delete("/api/tool-permissions/email_send_message")
    assert r.status_code == 200
    assert r.json() == {"reset": "email_send_message", "level": "needs_approval"}

    assert svc.stored_tool_level("email", "email_send_message") is None
    row = _row(client, "email_send_message")
    assert row["level"] == row["default_level"] == "needs_approval"


def test_reset_of_a_tool_that_was_never_set_is_idempotent(client):
    """There is no 404 for "nothing to reset": Reset deletes whatever row there is, and answers
    with the seed the tool reads now."""
    first = client.delete("/api/tool-permissions/get_net_worth")
    second = client.delete("/api/tool-permissions/get_net_worth")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"reset": "get_net_worth", "level": "always_allow"}


def test_delete_an_unknown_tool_with_no_owner_is_422(client):
    assert client.delete("/api/tool-permissions/nope").status_code == 422


def test_a_stale_tool_can_still_be_reset_which_removes_it(client):
    """Leaving the user unable to clear a row is the complaint this tab exists to answer —
    a stale row's owner cannot be re-derived from a registry that no longer knows the
    name, so the caller hands back the ``connector_id`` it was shown."""
    client.put(
        "/api/tool-permissions/a_tool_that_no_longer_exists",
        json={"level": "always_allow", "connector_id": svc.CORE_CONNECTOR_ID},
    )
    r = client.delete(
        f"/api/tool-permissions/a_tool_that_no_longer_exists?connector_id={svc.CORE_CONNECTOR_ID}"
    )
    assert r.status_code == 200
    assert r.json() == {"reset": "a_tool_that_no_longer_exists", "level": None}
    tools = {r["tool"] for r in client.get("/api/tool-permissions").json()["permissions"]}
    assert "a_tool_that_no_longer_exists" not in tools


# ── tool_exists: a rename is a visible revocation, not a silent one ────────────


def test_a_renamed_away_tool_is_shown_as_no_longer_available(client):
    client.put(
        "/api/tool-permissions/a_tool_that_no_longer_exists",
        json={"level": "always_allow", "connector_id": svc.CORE_CONNECTOR_ID},
    )
    row = _row(client, "a_tool_that_no_longer_exists")
    assert row["tool_exists"] is False
    assert row["default_level"] == ""


def test_a_live_tool_reports_tool_exists_true(client):
    client.put("/api/tool-permissions/add_transactions", json={"level": "always_allow"})
    assert _row(client, "add_transactions")["tool_exists"] is True


# ── One surface: the connector ladder and this route are ONE table ────────────


def test_a_decision_made_through_the_connector_ladder_is_listed_and_resettable_here(client):
    client.put(
        "/api/connectors/plaid/permissions/plaid_fetch_transactions", json={"level": "blocked"}
    )

    row = _row(client, "plaid_fetch_transactions")
    assert row["connector_id"] == "plaid" and row["level"] == "blocked"

    assert (
        client.delete(
            "/api/tool-permissions/plaid_fetch_transactions?connector_id=plaid"
        ).status_code
        == 200
    )
    assert svc.stored_tool_level("plaid", "plaid_fetch_transactions") is None
    assert _row(client, "plaid_fetch_transactions")["level"] == "always_allow"


def test_a_decision_made_through_this_route_is_visible_on_the_connector_ladder(client):
    client.put("/api/tool-permissions/plaid_fetch_transactions", json={"level": "blocked"})
    assert (
        client.get("/api/connectors/plaid/permissions").json()["permissions"][
            "plaid_fetch_transactions"
        ]
        == "blocked"
    )


def test_the_connector_reset_route_deletes_every_row_so_each_tool_reads_its_seed(client):
    client.put(
        "/api/connectors/plaid/permissions/plaid_fetch_transactions", json={"level": "blocked"}
    )
    body = client.delete("/api/connectors/plaid/permissions").json()
    assert body["permissions"] == {}
    assert body["tool_defaults"]["plaid_fetch_transactions"] == "always_allow"
    assert svc.stored_tool_level("plaid", "plaid_fetch_transactions") is None

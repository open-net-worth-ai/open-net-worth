"""``/api/tool-permissions`` — the app-wide revocation surface (Settings › Permissions,
approval model RFC 2026-07-20 §15.4/§16). Ports the REST-half coverage of the retired
``/api/grants`` (``ToolGrantStore``/``services.tool_grants``, DELETED at the 2026-08-14
one-permission-store merge, ADR ``2026-08-14-one-permission-store-for-every-tool``) onto
this one endpoint family.

Covers the properties this surface exists to guarantee:
  - a decision is listable and revocable (the tab has something real behind it);
  - revoking returns a tool to its DEFAULT, never to a stored ``needs_approval`` row;
  - yolo arms/disarms through this SAME ordinary route — no side channel;
  - a stale (renamed-away) tool's row is still listed, still shows ``tool_exists: false``,
    and is still clearable;
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

from backend.services import tool_permissions as svc

# The OLD permission gate's behaviour; permissions_v2 defaults on, and the on path is the
# test_tool_permissions_api_v2.py twin. Drop this pin with the flag.
pytestmark = pytest.mark.flags(permissions_v2=False)


@pytest.fixture()
def client(db_isolated):
    from backend.main import app

    return TestClient(app)


# ── GET: the empty shape, and listing a real decision ──────────────────────────


def test_get_empty_shape(client):
    r = client.get("/api/tool-permissions")
    assert r.status_code == 200
    assert r.json() == {"permissions": [], "total": 0, "yolo": False}


def test_get_lists_a_decision_with_its_owner_and_level(client):
    client.put("/api/tool-permissions/add_transactions", json={"level": "always_allow"})
    body = client.get("/api/tool-permissions").json()
    assert body["total"] == 1
    row = body["permissions"][0]
    assert row["tool"] == "add_transactions"
    assert row["connector_id"] == svc.CORE_CONNECTOR_ID
    assert row["level"] == "always_allow"
    assert row["tool_exists"] is True
    assert row["updated_at"]


# ── PUT: owner resolution, canonicalization, validation ────────────────────────


def test_put_resolves_the_owner_from_the_tools_connector_tag(client):
    """``plaid_fetch_transactions`` carries ``@tool(connector="plaid")`` — the route must resolve
    that owner itself so no client has to know (or guess) which bucket a tool belongs to."""
    body = client.put(
        "/api/tool-permissions/plaid_fetch_transactions", json={"level": "always_allow"}
    ).json()
    row = next(r for r in body["permissions"] if r["tool"] == "plaid_fetch_transactions")
    assert row["connector_id"] == "plaid"
    assert row["level"] == "always_allow"


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
    assert client.get("/api/tool-permissions").json()["permissions"] == []


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


# ── Revoke returns a tool to its DEFAULT, not to a stored decision ─────────────


def test_delete_revokes_and_the_tool_returns_to_its_default(client):
    # email_send_message carries @tool(connector="email") — the route must resolve THAT owner
    # (never CORE_CONNECTOR_ID) with no explicit connector_id supplied.
    client.put("/api/tool-permissions/email_send_message", json={"level": "blocked"})
    assert svc.stored_tool_level("email", "email_send_message") == "blocked"

    r = client.delete("/api/tool-permissions/email_send_message")
    assert r.status_code == 200
    assert r.json() == {"cleared": "email_send_message"}

    # UNSET, not a stored "needs_approval" row — the distinction that makes a future
    # default change move this tool along with every other undecided one.
    assert svc.stored_tool_level("email", "email_send_message") is None
    assert client.get("/api/tool-permissions").json()["total"] == 0


def test_delete_unknown_decision_is_404(client):
    """A NAMED owner with nothing stored for it is a 404 (no such decision); an unknown
    NAME with no owner supplied can't even be resolved and is a 422 instead — the same
    edge ``test_put_rejects_an_unknown_tool_with_no_explicit_owner`` covers on write."""
    assert (
        client.delete(
            f"/api/tool-permissions/nope?connector_id={svc.CORE_CONNECTOR_ID}"
        ).status_code
        == 404
    )
    assert client.delete("/api/tool-permissions/nope").status_code == 422


def test_a_stale_tool_can_still_be_revoked(client):
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


# ── tool_exists: a rename is a visible revocation, not a silent one ────────────


def test_a_renamed_away_tool_is_shown_as_no_longer_available(client):
    client.put(
        "/api/tool-permissions/a_tool_that_no_longer_exists",
        json={"level": "always_allow", "connector_id": svc.CORE_CONNECTOR_ID},
    )
    row = client.get("/api/tool-permissions").json()["permissions"][0]
    assert row["tool_exists"] is False


def test_a_live_tool_reports_tool_exists_true(client):
    client.put("/api/tool-permissions/add_transactions", json={"level": "always_allow"})
    row = client.get("/api/tool-permissions").json()["permissions"][0]
    assert row["tool_exists"] is True


# ── Yolo arms/disarms through this SAME ordinary route ─────────────────────────


def test_yolo_arms_through_the_star_tool_name(client):
    r = client.put(f"/api/tool-permissions/{svc.YOLO_TOOL}", json={"level": "always_allow"})
    assert r.status_code == 200
    assert r.json()["yolo"] is True
    assert client.get("/api/tool-permissions").json()["yolo"] is True


def test_yolo_is_listed_and_revocable_like_any_other_decision(client):
    """One revoke surface: arming and disarming the single most powerful permission must
    not be a second code path."""
    client.put(f"/api/tool-permissions/{svc.YOLO_TOOL}", json={"level": "always_allow"})
    listed = client.get("/api/tool-permissions").json()["permissions"]
    assert any(
        r["tool"] == svc.YOLO_TOOL and r["connector_id"] == svc.CORE_CONNECTOR_ID for r in listed
    )

    r = client.delete(f"/api/tool-permissions/{svc.YOLO_TOOL}?connector_id={svc.CORE_CONNECTOR_ID}")
    assert r.status_code == 200
    assert client.get("/api/tool-permissions").json()["yolo"] is False


# ── One revoke surface: the connector ladder and this route are ONE table ─────


def test_a_decision_made_through_the_connector_ladder_is_listed_and_revocable_here(client):
    client.put(
        "/api/connectors/plaid/permissions/plaid_fetch_transactions", json={"level": "blocked"}
    )

    listed = client.get("/api/tool-permissions").json()["permissions"]
    row = next(r for r in listed if r["tool"] == "plaid_fetch_transactions")
    assert row["connector_id"] == "plaid" and row["level"] == "blocked"

    assert (
        client.delete(
            "/api/tool-permissions/plaid_fetch_transactions?connector_id=plaid"
        ).status_code
        == 200
    )
    assert svc.stored_tool_level("plaid", "plaid_fetch_transactions") is None


def test_a_decision_made_through_this_route_is_visible_on_the_connector_ladder(client):
    client.put("/api/tool-permissions/plaid_fetch_transactions", json={"level": "always_allow"})
    assert (
        client.get("/api/connectors/plaid/permissions").json()["permissions"][
            "plaid_fetch_transactions"
        ]
        == "always_allow"
    )

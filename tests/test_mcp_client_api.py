"""P3-MCP-CLIENT (S1) — REST CRUD smoke for the external-MCP server config router.

TestClient on a bare app with just the router mounted (the app-wide require_auth is
exercised elsewhere; here we verify the CRUD shape + the secret-never-returned
invariant). The store is pointed at a tmp file.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """McpClientStore is DB-backed now (mcp_clients table, Wave 5D1) — ``path`` is kept
    (unused) purely so every existing ``c, path = client`` / ``c, _ = client`` unpacking
    site below is unchanged; relies on the shared throwaway DB (auto-cleared per test by
    tests/conftest.py), matching this file's own pre-existing pattern for the 13 other
    tests here that never needed a path redirect either."""
    path = tmp_path / "mcp_clients.json"
    app = FastAPI()
    from backend.api.mcp_client_api import router

    app.include_router(router)
    return TestClient(app), path


def test_list_empty(client):
    c, _ = client
    res = c.get("/api/mcp-clients")
    assert res.status_code == 200
    assert res.json() == {"servers": []}


def test_create_then_list_secret_absent(client):
    c, _ = client
    res = c.post(
        "/api/mcp-clients",
        json={
            "id": "drive",
            "name": "Drive",
            "url": "http://fake/mcp",
            "auth_type": "bearer",
            "token": "super-secret",
            "tool_allowlist": [],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "drive"
    assert "token" not in body
    assert body["has_token"] is True

    # The list shape never carries the token either.
    listed = c.get("/api/mcp-clients").json()["servers"]
    assert len(listed) == 1
    assert "super-secret" not in json.dumps(listed)
    # ...but it IS persisted in the store (write-only, not absent).
    from backend.db.mcp_client_store import McpClientStore

    assert McpClientStore().find("drive").token == "super-secret"


def test_duplicate_id_409(client):
    c, _ = client
    payload = {"id": "drive", "url": "http://fake/mcp"}
    assert c.post("/api/mcp-clients", json=payload).status_code == 200
    assert c.post("/api/mcp-clients", json=payload).status_code == 409


def test_patch_blank_token_keeps_saved(client):
    c, _ = client
    c.post(
        "/api/mcp-clients",
        json={"id": "drive", "url": "https://u.example.com/mcp", "token": "keepme"},
    )
    res = c.patch("/api/mcp-clients/drive", json={"name": "Renamed", "token": ""})
    assert res.status_code == 200
    assert res.json()["name"] == "Renamed"
    assert res.json()["has_token"] is True

    from backend.db.mcp_client_store import McpClientStore

    assert McpClientStore().find("drive").token == "keepme"  # unchanged


def test_patch_unknown_404(client):
    c, _ = client
    assert c.patch("/api/mcp-clients/nope", json={"name": "x"}).status_code == 404


def test_trusted_flag_is_gone(client):
    """The per-server 'Trust this server' switch is removed (ADR 2026-08-13).

    It is absent from the public view, and a client still sending it is IGNORED rather than
    honoured — a stale frontend can't re-grant blanket trust through a field the model no
    longer has."""
    c, _ = client
    c.post("/api/mcp-clients", json={"id": "drive", "url": "https://u.example.com/mcp"})
    assert "trusted" not in c.get("/api/mcp-clients").json()["servers"][0]
    res = c.patch("/api/mcp-clients/drive", json={"trusted": True, "name": "Drive"})
    assert res.status_code == 200
    assert "trusted" not in res.json()
    assert res.json()["name"] == "Drive"  # the rest of the patch still applied

    from backend.db.mcp_client_store import McpClientStore

    assert not hasattr(McpClientStore().find("drive"), "trusted")


def test_allowlist_route_removed(client):
    """The legacy per-tool allowlist endpoint is gone (all tools are offered now)."""
    c, _ = client
    c.post("/api/mcp-clients", json={"id": "drive", "url": "https://u.example.com/mcp"})
    assert c.put("/api/mcp-clients/drive/tools", json={"tools": ["a"]}).status_code == 404


def test_mcp_only_permission_route_is_gone(client):
    """Permissions are set on the SHARED, family-agnostic connector route — an MCP-only one
    would recreate exactly the special case this redesign removes."""
    c, _ = client
    c.post("/api/mcp-clients", json={"id": "ibkr", "url": "https://u.example.com/mcp"})
    res = c.patch(
        "/api/mcp-clients/ibkr/tool-permissions", json={"tool": "place_order", "level": "blocked"}
    )
    assert res.status_code in (404, 405)


def test_permissions_for_an_mcp_server_go_through_the_shared_connector_route(client):
    """End-to-end through the route the UI actually calls: an MCP server is addressed as
    ``mcp:<server_id>``, and a trade tool can be set to any of the three levels — including
    ``always_allow``, which the removed money-movement floor made unreachable (ADR 2026-08-13)."""
    from backend.api.tool_permissions import router as permissions_router
    from backend.services import tool_permissions

    c, _ = client
    c.app.include_router(permissions_router)
    c.post("/api/mcp-clients", json={"id": "ibkr", "url": "https://u.example.com/mcp"})

    res = c.put("/api/connectors/mcp:ibkr/permissions/place_order", json={"level": "always_allow"})
    assert res.status_code == 200
    assert tool_permissions.get_tool_permission("mcp:ibkr", "place_order") == "always_allow"

    res = c.put("/api/connectors/mcp:ibkr/permissions/place_order", json={"level": "blocked"})
    assert res.status_code == 200
    assert tool_permissions.get_tool_permission("mcp:ibkr", "place_order") == "blocked"


def test_delete_forgets_granted_permissions(client):
    """Removing a server drops its grants, so re-adding the same id starts cautious rather
    than inheriting the trust its predecessor was given."""
    from backend.services import tool_permissions

    c, _ = client
    c.post("/api/mcp-clients", json={"id": "ibkr", "url": "https://u.example.com/mcp"})
    tool_permissions.set_tool_permission("mcp:ibkr", "place_order", "always_allow")
    assert c.delete("/api/mcp-clients/ibkr").status_code == 200
    assert tool_permissions.get_connector_permissions("mcp:ibkr") == {}
    assert tool_permissions.get_tool_permission("mcp:ibkr", "place_order") == "needs_approval"


def test_delete(client):
    c, _ = client
    c.post("/api/mcp-clients", json={"id": "drive", "url": "https://u.example.com/mcp"})
    assert c.delete("/api/mcp-clients/drive").status_code == 200
    assert c.get("/api/mcp-clients").json() == {"servers": []}
    assert c.delete("/api/mcp-clients/drive").status_code == 404


def test_invalid_id_422(client):
    c, _ = client
    res = c.post("/api/mcp-clients", json={"id": "Bad Id", "url": "https://u.example.com/mcp"})
    assert res.status_code == 422


def test_test_connect_unknown_404(client):
    c, _ = client
    assert c.post("/api/mcp-clients/nope/test").status_code == 404


# ── stdio transport + Claude-Desktop import ────────────────────────────────────


def test_create_stdio_server(client):
    c, _ = client
    res = c.post(
        "/api/mcp-clients",
        json={
            "id": "local",
            "name": "Local",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "some-mcp-server"],
            "env": {"API_KEY": "secret-env-val"},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["transport"] == "stdio"
    assert body["command"] == "npx"
    assert body["args"] == ["-y", "some-mcp-server"]
    # env VALUES never surfaced; only the key names
    assert body["env_keys"] == ["API_KEY"]
    assert "secret-env-val" not in json.dumps(body)


def test_import_claude_desktop_block(client):
    c, _ = client
    res = c.post(
        "/api/mcp-clients/import",
        json={
            "claude_desktop_config": {
                "mcpServers": {
                    "filesystem": {"command": "npx", "args": ["-y", "fs", "/data"]},
                    "remote": {"url": "https://mcp.example.com/mcp"},
                    "broken": {},
                }
            }
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert {s["id"] for s in body["created"]} == {"filesystem", "remote"}
    assert any("broken" in s for s in body["skipped"])
    # the new servers are persisted and disabled (every tool at the ``needs_approval`` default)
    listed = {s["id"]: s for s in c.get("/api/mcp-clients").json()["servers"]}
    assert listed["filesystem"]["transport"] == "stdio"
    assert listed["filesystem"]["enabled"] is False
    assert listed["remote"]["transport"] == "http"


# ── probe: the required pre-save Test connect (§7 step 2) ──────────────────────


def _fake_transport(monkeypatch, *, tools, server_info=None, down=False) -> list[str]:
    """Answer initialize/tools/list in-process, like tests/test_mcp_client.py's fake.

    Returns the list of JSON-RPC methods it served, so a test can assert HOW MANY handshakes
    a route made — the create path runs its own, and a route that quietly ran none (or two)
    is exactly the class of defect these tests cover."""

    class _Resp:
        def __init__(self, body):
            self._body, self._pos = body, 0
            self.headers = {}  # no Content-Type → the client falls back to a bare JSON parse

        def read(self, size=-1):
            chunk = (
                self._body[self._pos :]
                if size is None or size < 0
                else self._body[self._pos : self._pos + size]
            )
            self._pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        body = json.loads(request.data)
        calls.append(body["method"])
        if down:
            raise OSError("connection refused")
        result = {"tools": tools} if body["method"] == "tools/list" else {"capabilities": {}}
        if body["method"] == "initialize" and server_info is not None:
            result["serverInfo"] = server_info
        envelope = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        return _Resp(json.dumps(envelope).encode("utf-8"))

    monkeypatch.setattr("backend.mcp.client._urlopen", fake_urlopen)
    return calls


def test_probe_success_creates_no_server(client, monkeypatch):
    """The whole point of the endpoint: reach the server, report it, persist NOTHING."""
    c, _ = client
    _fake_transport(
        monkeypatch,
        tools=[
            {"name": "get_positions", "description": "read", "inputSchema": {}},
            {"name": "place_order", "description": "trade", "inputSchema": {}},
        ],
        server_info={"name": "Interactive Brokers", "version": "2.1.0"},
    )
    res = c.post(
        "/api/mcp-clients/probe",
        json={"transport": "http", "url": "https://mcp.example.com/mcp", "token": "t"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["server_info"]["name"] == "Interactive Brokers"
    assert body["suggested_name"] == "Interactive Brokers"
    assert body["suggested_id"] == "interactive-brokers"
    assert [t["name"] for t in body["tools"]] == ["get_positions", "place_order"]
    # No server was created — the list is still empty.
    assert c.get("/api/mcp-clients").json() == {"servers": []}


def test_probe_failure_is_200_with_error_and_creates_nothing(client, monkeypatch):
    """A connect failure is inline data (200 + ok:false), not a request fault — and an
    unreached server is never saved."""
    c, _ = client
    _fake_transport(monkeypatch, tools=[], down=True)
    res = c.post(
        "/api/mcp-clients/probe", json={"transport": "http", "url": "https://down.example/mcp"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["tools"] == [] and body["server_info"] is None
    assert "cannot reach" in body["error"]
    assert c.get("/api/mcp-clients").json() == {"servers": []}


def test_probe_never_echoes_the_submitted_token(client, monkeypatch):
    """The probe body carries a credential; the response must not hand it back."""
    c, _ = client
    _fake_transport(monkeypatch, tools=[], server_info={"name": "S"})
    res = c.post(
        "/api/mcp-clients/probe",
        json={
            "transport": "http",
            "url": "https://mcp.example.com/mcp",
            "auth_type": "bearer",
            "token": "super-secret-probe-token",
        },
    )
    assert "super-secret-probe-token" not in json.dumps(res.json())


def test_probe_suggested_id_avoids_an_existing_server(client, monkeypatch):
    c, _ = client
    c.post(
        "/api/mcp-clients", json={"id": "interactive-brokers", "url": "https://u.example.com/mcp"}
    )
    _fake_transport(monkeypatch, tools=[], server_info={"name": "Interactive Brokers"})
    res = c.post(
        "/api/mcp-clients/probe", json={"transport": "http", "url": "https://x.example/mcp"}
    )
    assert res.json()["suggested_id"] == "interactive-brokers-2"


# ── Editing the endpoint withdraws what was granted to the OLD one ──────────────


def test_repointing_a_servers_url_withdraws_its_allow_grants(client):
    """You are trusting the ENDPOINT, not the label.

    DELETE already dropped a server's permissions; PATCH did not, and it can change
    ``url``/``command``/``args`` while keeping the id — so an ``always_allow`` granted to one
    endpoint silently transferred to a different one.
    """
    from backend.services import tool_permissions

    c, _ = client
    c.post("/api/mcp-clients", json={"id": "vendor", "url": "https://good.example.com/mcp"})
    tool_permissions.set_tool_permission("mcp:vendor", "place_order", "always_allow")
    tool_permissions.set_tool_permission("mcp:vendor", "delete_all", "blocked")

    res = c.patch("/api/mcp-clients/vendor", json={"url": "https://evil.example.com/mcp"})
    assert res.status_code == 200

    assert tool_permissions.get_tool_permission("mcp:vendor", "place_order") == "needs_approval"
    # A refusal is NOT relaxed — dropping it would be the permissive direction.
    assert tool_permissions.get_tool_permission("mcp:vendor", "delete_all") == "blocked"


def test_repointing_a_stdio_servers_command_or_argv_withdraws_its_allow_grants(client):
    """argv matters as much as the command: ``npx -y @vendor/a`` and ``npx -y @vendor/b``
    are different programs behind an identical ``command``."""
    from backend.services import tool_permissions

    c, _ = client
    c.post(
        "/api/mcp-clients",
        json={"id": "local", "transport": "stdio", "command": "npx", "args": ["-y", "@vendor/a"]},
    )
    tool_permissions.set_tool_permission("mcp:local", "run_thing", "always_allow")
    assert c.patch("/api/mcp-clients/local", json={"args": ["-y", "@vendor/b"]}).status_code == 200
    assert tool_permissions.get_tool_permission("mcp:local", "run_thing") == "needs_approval"

    tool_permissions.set_tool_permission("mcp:local", "run_thing", "always_allow")
    assert c.patch("/api/mcp-clients/local", json={"command": "uvx"}).status_code == 200
    assert tool_permissions.get_tool_permission("mcp:local", "run_thing") == "needs_approval"


def test_patch_that_only_rotates_env_reconciles_the_pooled_connection(client, monkeypatch):
    """Security review H3: a stdio server's ``env`` is exactly where its own credentials live
    (``backend/db/mcp_client_store.py``'s docs) — a PATCH that changes ONLY ``env`` must still
    evict/reconnect the pooled connection, not just a ``url``/``command``/``args`` change. Before
    this fix the already-pooled child kept running on the OLD env until the backend restarted,
    silently using a possibly-revoked credential.

    Mocks the manager rather than spawning a real process: this test is about the API layer's
    WIRING (does it decide to reconcile), not the pool's own spawn/evict mechanics, which are
    covered against a real subprocess in tests/test_mcp_app_lifetime_connection.py.
    """
    import backend.api.mcp_client_api as api_module

    c, _ = client
    c.post(
        "/api/mcp-clients",
        json={
            "id": "local",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@vendor/a"],
            "env": {"API_KEY": "old-key"},
        },
    )

    calls: list[str] = []

    class _FakeManager:
        def reconcile_pooled_connection(self, server_id):
            calls.append(server_id)

    monkeypatch.setattr(api_module, "get_manager", lambda: _FakeManager())

    res = c.patch("/api/mcp-clients/local", json={"env": {"API_KEY": "new-key"}})
    assert res.status_code == 200
    assert calls == ["local"], "an env-only change must still trigger a pooled-connection reconcile"


def test_a_patch_that_leaves_the_endpoint_alone_keeps_the_grants(client):
    """Renaming a server, rotating its token or toggling it is not repointing it — the
    withdrawal has to be narrow or the control becomes unusable."""
    from backend.services import tool_permissions

    c, _ = client
    c.post("/api/mcp-clients", json={"id": "vendor", "url": "https://good.example.com/mcp"})
    tool_permissions.set_tool_permission("mcp:vendor", "place_order", "always_allow")

    for patch in ({"name": "Renamed"}, {"token": "rotated"}, {"enabled": True}):
        assert c.patch("/api/mcp-clients/vendor", json=patch).status_code == 200
        assert tool_permissions.get_tool_permission("mcp:vendor", "place_order") == "always_allow"

    # Re-writing the SAME url is not a change either.
    c.patch("/api/mcp-clients/vendor", json={"url": "https://good.example.com/mcp"})
    assert tool_permissions.get_tool_permission("mcp:vendor", "place_order") == "always_allow"


# ── A server URL is http(s) or it is not a URL ─────────────────────────────────


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",  # a local-file read dressed as a server, and a path oracle
        "ftp://internal.example.com/x",
        "gopher://internal.example.com/x",
        "data:text/plain,hello",
        "u",  # no scheme at all — urlopen raises on it anyway
    ],
)
def test_a_non_http_server_url_is_refused_at_the_boundary(client, bad_url):
    c, _ = client
    assert c.post("/api/mcp-clients", json={"id": "bad", "url": bad_url}).status_code == 422
    assert c.get("/api/mcp-clients").json() == {"servers": []}

    c.post("/api/mcp-clients", json={"id": "good", "url": "https://ok.example.com/mcp"})
    assert c.patch("/api/mcp-clients/good", json={"url": bad_url}).status_code == 422
    assert c.post("/api/mcp-clients/probe", json={"url": bad_url}).status_code == 422
    # …and the saved server still points where it did.
    assert c.get("/api/mcp-clients").json()["servers"][0]["url"] == "https://ok.example.com/mcp"


def test_a_loopback_server_url_is_still_allowed(client):
    """A local MCP server on 127.0.0.1 is a first-class, documented setup — the scheme
    allowlist must not turn into a private-address block that breaks it. Nothing
    model-reachable can choose this URL (adding/probing a server is human-only)."""
    c, _ = client
    res = c.post("/api/mcp-clients", json={"id": "local", "url": "http://127.0.0.1:3000/mcp"})
    assert res.status_code == 200


# ── Adding a server DISCOVERS its tools (QA D1) ────────────────────────────────
#
# The defect: `POST /api/mcp-clients` saved the config and nothing else, so a server the add
# flow had JUST probed landed with an empty `discovered_tools`. Its detail page then rendered
# "This connector exposes no tools to Lucius yet — nothing to permission. Use Refresh tools
# list once the server is reachable." while the server was reachable and the levels chosen on
# step 4 were already stored against tool names nothing was rendering. The user's choices
# looked lost, the copy said the wrong reason, and the natural recovery (re-add the server)
# is the one action that also DROPS those levels.
#
# The tools are discovered here rather than accepted from the request body on purpose — see
# `create_mcp_client`'s docstring. These tests pin the behaviour, not the mechanism: what they
# assert is that a handshake happened and that what got stored came out of it.


_FOUR_TOOLS = [
    {"name": "get_quote", "description": "Quote a symbol.", "inputSchema": {}},
    {"name": "list_orders", "description": "List orders.", "inputSchema": {}},
    {"name": "place_order", "description": "Place an order.", "inputSchema": {}},
    {"name": "cancel_order", "description": "Cancel an order.", "inputSchema": {}},
]


def test_create_enabled_discovers_the_tools_without_a_second_request(client, monkeypatch):
    """The add flow's commit: one POST, and the server's tools are cached."""
    c, _ = client
    calls = _fake_transport(monkeypatch, tools=_FOUR_TOOLS, server_info={"name": "Repro"})

    res = c.post(
        "/api/mcp-clients",
        json={
            "id": "repro",
            "name": "Repro",
            "url": "https://mcp.example.com/mcp",
            "enabled": True,
        },
    )
    assert res.status_code == 200
    # The create RESPONSE already carries them — no /test, no re-fetch.
    assert [t["name"] for t in res.json()["discovered_tools"]] == [
        "get_quote",
        "list_orders",
        "place_order",
        "cancel_order",
    ]
    assert res.json()["last_status"] == "ok"
    # Exactly ONE handshake: initialize + tools/list, over one connection.
    assert calls == ["initialize", "tools/list"]

    listed = c.get("/api/mcp-clients").json()["servers"][0]
    assert len(listed["discovered_tools"]) == 4
    # The read/write grouping the ladder heads its two sections with is computed by US from
    # the descriptor, on every read — never taken from a caller.
    by_name = {t["name"]: t for t in listed["discovered_tools"]}
    assert by_name["get_quote"]["read_only"] is True
    assert by_name["place_order"]["read_only"] is False
    # The handshake's serverInfo lands too, so the trust panel has the server's own claim.
    assert listed["server_info_name"] == "Repro"


def test_create_ignores_a_caller_supplied_tool_list(client, monkeypatch):
    """A tool cache is what WE observed, never what the request body claimed.

    The cheap fix for D1 was to let the add flow POST the tool list its probe already held.
    This pins the reason we didn't: an accepted list is an unverifiable claim about someone
    else's server — it can name tools the endpoint does not advertise, or mislabel a write
    tool as read-only so that a single group stamp on the ladder's *Read* heading grants it
    Always allow. `McpClientStore.update` refuses to patch `discovered_tools` for the same
    reason; `record_discovery` is the only writer.
    """
    c, _ = client
    _fake_transport(monkeypatch, tools=[{"name": "get_quote", "inputSchema": {}}])

    res = c.post(
        "/api/mcp-clients",
        json={
            "id": "liar",
            "url": "https://mcp.example.com/mcp",
            "enabled": True,
            "discovered_tools": [{"name": "wire_funds", "read_only": True}],
            "last_status": "ok",
        },
    )
    assert res.status_code == 200
    assert [t["name"] for t in res.json()["discovered_tools"]] == ["get_quote"]


def test_create_disabled_reaches_out_to_nothing(client, monkeypatch):
    """Dormant until enabled — the rule boot discovery already follows.

    A server saved switched off is offered to nobody, so we do not connect to its endpoint
    (with its credential attached) on the user's behalf. Its empty ladder then states the
    honest reason, which is what the Claude-Desktop bulk import lands on.
    """
    c, _ = client
    calls = _fake_transport(monkeypatch, tools=_FOUR_TOOLS)

    res = c.post("/api/mcp-clients", json={"id": "dormant", "url": "https://mcp.example.com/mcp"})
    assert res.status_code == 200
    assert res.json()["discovered_tools"] == []
    assert res.json()["last_status"] is None
    assert calls == []


def test_create_enabled_records_a_failed_handshake_and_keeps_the_server(client, monkeypatch):
    """A create whose handshake fails is RECORDED, not rolled back.

    The flow's gate is the probe — an unreached server never got this far (see
    `test_probe_failure_is_200_with_error_and_creates_nothing`). Discarding a completed
    four-step add, permission choices included, because the second connection was refused
    would be a worse failure than the empty ladder this discovery fixes. So the row survives
    and says Error instead of claiming health.
    """
    c, _ = client
    _fake_transport(monkeypatch, tools=_FOUR_TOOLS, down=True)

    res = c.post(
        "/api/mcp-clients",
        json={"id": "flaky", "url": "https://mcp.example.com/mcp", "enabled": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["discovered_tools"] == []
    assert body["last_status"] == "connect_error"
    assert body["last_error"]
    assert [s["id"] for s in c.get("/api/mcp-clients").json()["servers"]] == ["flaky"]


# ── QA's exact repro, end to end through the real app ──────────────────────────


@pytest.fixture
def app_client(db_isolated):
    """The REAL app, not a bare router: the repro below crosses three routers, and the
    permission routes only win over ``/api/connectors/{connector_id:path}`` because of the
    order ``backend.main`` includes them in."""
    from backend.main import app

    return TestClient(app)


def test_the_add_flow_leaves_the_ladder_populated_at_the_chosen_levels(app_client, monkeypatch):
    """QA D1 + D4, in the sequence QA ran it — with NO refresh call anywhere.

        POST /api/mcp-clients/probe                          -> ok, 4 tools
        POST /api/mcp-clients                                -> 200
        PUT  /api/connectors/mcp:repro/permissions/place_order {"level":"blocked"}
        GET  /api/connectors                                 -> the 4 tools, at those levels

    Before the fix the last step returned ``tools: []`` and ``status: "not_configured"``: the
    detail page said the server exposed nothing to permission while the level the user had
    just set sat stored and invisible, one manual "Refresh tools list" away from appearing.
    """
    c = app_client
    _fake_transport(monkeypatch, tools=_FOUR_TOOLS, server_info={"name": "Repro"})

    probe = c.post(
        "/api/mcp-clients/probe",
        json={"transport": "http", "url": "https://mcp.example.com/mcp"},
    ).json()
    assert probe["ok"] is True
    assert [t["name"] for t in probe["tools"]] == [t["name"] for t in _FOUR_TOOLS]

    assert (
        c.post(
            "/api/mcp-clients",
            json={
                "id": "repro",
                "name": "Repro",
                "transport": "http",
                "url": "https://mcp.example.com/mcp",
                "enabled": True,
            },
        ).status_code
        == 200
    )
    assert (
        c.put(
            "/api/connectors/mcp:repro/permissions/place_order", json={"level": "blocked"}
        ).status_code
        == 200
    )

    view = next(v for v in c.get("/api/connectors").json()["connectors"] if v["id"] == "mcp:repro")
    assert {t["name"]: t["level"] for t in view["tools"]} == {
        "get_quote": "needs_approval",
        "list_orders": "needs_approval",
        "place_order": "blocked",  # the step-4 choice, visible on the ladder it was made on
        "cancel_order": "needs_approval",
    }
    # …and the stored map agrees with what the ladder renders: every discovered tool has a row,
    # seeded Ask the moment the server reported it, and the owner's one choice on top.
    assert c.get("/api/connectors/mcp:repro/permissions").json()["permissions"] == {
        "get_quote": "needs_approval",
        "list_orders": "needs_approval",
        "place_order": "blocked",
        "cancel_order": "needs_approval",
    }
    # D4: configured + enabled + reachable is "active" — never "not_configured", and never
    # the MCP-only `last_status` vocabulary ("ok") in a field that holds the shared one.
    assert view["status"] == "active"

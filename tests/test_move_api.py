"""Move protocol (contract C8): resumable upload, import, secrets, finish, ownership, cleanup.

The importer itself is stubbed (``portability.apply_import`` has its own suite); what is under
test is the transfer protocol and what it does around the import.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core import ratelimit
from backend.core.errors import NotFoundError
from backend.db.auth_store import AuthStore
from backend.services import move, move_secrets, portability

PART = 10
BUNDLE = bytes(range(256)) * 1 + b"tail"  # 260 bytes → 26 parts of 10
HEADERS = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def client(db_isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(move, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(move, "PART_SIZE", PART)
    monkeypatch.setattr(move, "_begin_limiter", ratelimit.SlidingWindowLimiter(10, 3600))
    monkeypatch.setattr(portability, "inspect_bundle", lambda data: {"ok": True, "errors": []})
    imported: list[bytes] = []
    monkeypatch.setattr(
        portability,
        "apply_import",
        # The importer gets the uploaded file itself (streamed from disk, never read into memory).
        lambda source: imported.append(Path(source).read_bytes()) or {"restored_files": []},
    )
    from backend.main import app

    test_client = TestClient(app)
    test_client.imported = imported  # type: ignore[attr-defined]
    return test_client


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _begin(client, data=BUNDLE, headers=HEADERS):
    return client.post(
        "/api/move/begin", json={"size": len(data), "sha256": _sha(data)}, headers=headers
    )


def _put(client, move_id, n, data=BUNDLE, sha=None, headers=HEADERS):
    chunk = data[n * PART : (n + 1) * PART]
    return client.put(
        f"/api/move/{move_id}/parts/{n}",
        content=chunk,
        headers={**headers, "X-Part-Sha256": sha or _sha(chunk)},
    )


def _upload_all(client, move_id, data=BUNDLE):
    for n in range((len(data) + PART - 1) // PART):
        assert _put(client, move_id, n, data).status_code == 204


def _wait(client, move_id, terminal=("done", "failed")):
    for _ in range(200):
        body = client.get(f"/api/move/{move_id}").json()
        if body["status"] in terminal:
            return body
        time.sleep(0.02)
    raise AssertionError(body)


def test_begin_states_part_size_and_the_disk_limit(client):
    body = _begin(client).json()
    assert body["part_size"] == PART and body["max_size"] > 0 and body["move_id"].startswith("mv_")


def test_begin_refuses_a_book_the_disk_cannot_hold(client, monkeypatch):
    monkeypatch.setattr(move, "free_space_limit", lambda path=None: 100)
    response = _begin(client, b"x" * 101)
    assert response.status_code == 413
    assert response.json()["detail"]["max_size"] == 100


def test_begin_validates_its_input(client):
    assert (
        client.post(
            "/api/move/begin", json={"size": 0, "sha256": "0" * 64}, headers=HEADERS
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/move/begin", json={"size": 5, "sha256": "nope"}, headers=HEADERS
        ).status_code
        == 422
    )


def test_upload_resumes_idempotently_out_of_order(client):
    move_id = _begin(client).json()["move_id"]
    for n in (3, 0, 3, 25):
        assert _put(client, move_id, n).status_code == 204
    state = client.get(f"/api/move/{move_id}").json()
    assert (
        state["received"] == [0, 3, 25] and state["status"] == "uploading" and state["parts"] == 26
    )
    for n in range(26):
        assert _put(client, move_id, n).status_code == 204
    assert len(client.get(f"/api/move/{move_id}").json()["received"]) == 26


def test_a_part_is_checked_for_checksum_length_and_range(client):
    move_id = _begin(client).json()["move_id"]
    assert _put(client, move_id, 0, sha="0" * 64).status_code == 422
    assert (
        client.put(
            f"/api/move/{move_id}/parts/0",
            content=b"short",
            headers={**HEADERS, "X-Part-Sha256": _sha(b"short")},
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/move/{move_id}/parts/0",
            content=b"x" * 11,
            headers={**HEADERS, "X-Part-Sha256": _sha(b"x" * 11)},
        ).status_code
        == 413
    )
    assert _put(client, move_id, 99).status_code == 422
    assert client.get(f"/api/move/{move_id}").json()["received"] == []


def test_commit_needs_every_part_then_imports_from_disk(client):
    move_id = _begin(client).json()["move_id"]
    _put(client, move_id, 0)
    assert (
        client.post(
            f"/api/move/{move_id}/commit", json={"replace": True}, headers=HEADERS
        ).status_code
        == 409
    )
    for n in range(1, 26):
        _put(client, move_id, n)
    assert (
        client.post(
            f"/api/move/{move_id}/commit", json={"replace": True}, headers=HEADERS
        ).status_code
        == 200
    )
    final = _wait(client, move_id)
    assert final["status"] == "done" and final["progress"] == 100
    assert client.imported == [BUNDLE]
    assert not (move._dir(move_id) / "bundle.zip").exists()


def test_a_corrupted_whole_file_fails_without_importing(client):
    wrong = BUNDLE[:-1] + b"X"
    move_id = client.post(
        "/api/move/begin", json={"size": len(BUNDLE), "sha256": _sha(wrong)}, headers=HEADERS
    ).json()["move_id"]
    _upload_all(client, move_id)
    client.post(f"/api/move/{move_id}/commit", json={"replace": True}, headers=HEADERS)
    final = _wait(client, move_id)
    assert final["status"] == "failed" and "checksum" in final["error"]
    assert client.imported == []


def test_a_bundle_that_fails_validation_is_not_imported(client, monkeypatch):
    monkeypatch.setattr(
        portability, "inspect_bundle", lambda data: {"ok": False, "errors": ["bad manifest"]}
    )
    move_id = _begin(client).json()["move_id"]
    _upload_all(client, move_id)
    client.post(f"/api/move/{move_id}/commit", json={"replace": True}, headers=HEADERS)
    final = _wait(client, move_id)
    assert final["status"] == "failed" and "bad manifest" in final["error"]
    assert client.imported == []


def test_a_server_with_a_book_asks_before_replacing(client, monkeypatch):
    monkeypatch.setattr(move, "_book_is_empty", lambda: False)
    move_id = _begin(client).json()["move_id"]
    _upload_all(client, move_id)
    refused = client.post(f"/api/move/{move_id}/commit", json={"replace": False}, headers=HEADERS)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "not_empty"
    assert (
        client.post(
            f"/api/move/{move_id}/commit", json={"replace": True}, headers=HEADERS
        ).status_code
        == 200
    )
    assert _wait(client, move_id)["status"] == "done"


def test_the_import_pauses_automations_and_finish_resumes_them(client, monkeypatch):
    monkeypatch.setattr(move_secrets, "pause_automations", lambda: ["auto_1"])
    resumed: list[list[str]] = []
    monkeypatch.setattr(move_secrets, "resume_automations", lambda ids: resumed.append(ids) or ids)
    move_id = _begin(client).json()["move_id"]
    _upload_all(client, move_id)
    client.post(f"/api/move/{move_id}/commit", json={"replace": True}, headers=HEADERS)
    _wait(client, move_id)
    body = client.post(f"/api/move/{move_id}/finish", headers=HEADERS).json()
    assert resumed == [["auto_1"]] and body["automations_resumed"] == ["auto_1"]
    assert client.post(f"/api/move/{move_id}/finish", headers=HEADERS).json() == body
    assert any(item["id"] == "secrets_not_sent" for item in body["checklist"])


def _connector(kind, connector_id=None, *, config=None, secrets=None, enabled=False, **extra):
    """One connector row as the sender's ``collect`` writes it."""
    return {
        "id": connector_id or kind,
        "kind": kind,
        "name": extra.pop("name", connector_id or kind),
        "enabled": enabled,
        "config": config or {},
        "secrets": secrets or {},
        **extra,
    }


def test_nothing_goes_live_until_finish_and_secrets_never_reach_the_log(client, caplog):
    caplog.set_level(logging.DEBUG)
    move_id = _begin(client).json()["move_id"]
    payload = {
        "ai_providers": {
            "anthropic": {"api_key": "sk-ant-SECRET", "enabled": True},
            "ollama": {"base_url": "http://localhost:11434"},
            "claude_cli": {},
        },
        "default_model": "anthropic:claude-sonnet-4-5",
        "connectors": [
            _connector(
                "telegram",
                config={"allowed_chat_ids": ["7"]},
                secrets={"bot_token": "123:TOKEN-SECRET"},
                enabled=True,
            ),
            _connector(
                "mcp",
                "mcp:docs",
                name="Docs",
                config={"transport": "http", "auth_type": "bearer"},
                secrets={"url": "https://mcp.example.com/mcp", "token": "MCP-SECRET"},
                enabled=True,
            ),
            _connector(
                "mcp",
                "mcp:files",
                config={"transport": "stdio"},
                secrets={"command": "npx"},
                enabled=True,
            ),
        ],
    }
    assert (
        client.post(f"/api/move/{move_id}/secrets", json=payload, headers=HEADERS).status_code
        == 409
    )
    _upload_all(client, move_id)
    client.post(f"/api/move/{move_id}/commit", json={"replace": True}, headers=HEADERS)
    _wait(client, move_id)
    response = client.post(f"/api/move/{move_id}/secrets", json=payload, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert {"kind": "ai_provider", "id": "anthropic"} in body["applied"]
    assert {"kind": "mcp_server", "id": "files", "reason": "local_mcp"} in body["skipped"]
    assert {"kind": "ai_provider", "id": "ollama", "reason": "local_model"} in body["skipped"]
    assert {"kind": "ai_provider", "id": "claude_cli", "reason": "cli_subscription"} in body[
        "skipped"
    ]
    assert "SECRET" not in response.text and "deferred" not in body

    from backend.db.connector_store import ConnectorStore
    from backend.db.mcp_client_store import McpClientStore
    from backend.db.model_config_store import ModelConfigStore

    telegram = ConnectorStore().get("telegram")
    assert telegram["enabled"] is False and telegram["config"] == {"allowed_chat_ids": ["7"]}
    assert ConnectorStore().secrets("telegram") == {"bot_token": "123:TOKEN-SECRET"}
    assert ModelConfigStore().read_stored_api_key("anthropic") == "sk-ant-SECRET"
    assert [s.enabled for s in McpClientStore().all()] == [False]

    switched_on: list[str] = []
    import backend.services.connectors as connectors

    monkeypatch_enable = lambda connector_id, enabled: switched_on.append(connector_id)  # noqa: E731
    connectors.set_connector_enabled, original = (
        monkeypatch_enable,
        connectors.set_connector_enabled,
    )
    try:
        finished = client.post(f"/api/move/{move_id}/finish", headers=HEADERS).json()
    finally:
        connectors.set_connector_enabled = original
    assert sorted(switched_on) == ["mcp:docs", "telegram"] and sorted(finished["enabled"]) == [
        "mcp:docs",
        "telegram",
    ]
    assert {item["id"] for item in finished["checklist"]} == {
        "local_model",
        "cli_subscription",
        "local_mcp",
    }
    assert "SECRET" not in caplog.text


def _stamped(state: dict) -> TestClient:
    """A client whose requests arrive already marked by a transport (desktop, gateway) as the real
    middleware would mark them."""
    from backend.main import app

    async def with_state(scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {}).update(state)
        await app(scope, receive, send)

    return TestClient(with_state)


def _export(who: TestClient, headers: dict | None = None, *, nonce: bool = True):
    headers = {**HEADERS, **(headers or {})}
    if nonce:
        issued = who.post("/api/move/export-nonce", headers=headers)
        if issued.status_code != 200:
            return issued
        headers["X-Move-Nonce"] = issued.json()["nonce"]
    return who.post("/api/move/secrets/export", headers=headers)


def test_secrets_export_collects_movable_credentials_and_lists_the_rest(client):
    from backend.db.mcp_client_store import McpClientStore
    from backend.db.model_config_store import ModelConfigStore
    from backend.models.mcp_client import McpServerConfig

    ModelConfigStore().update_provider("openai", {"api_key": "sk-OPENAI", "enabled": True})
    ModelConfigStore().update_provider(
        "ollama", {"base_url": "http://localhost:11434", "key_status": "ok"}
    )
    McpClientStore().create(McpServerConfig(id="local", transport="stdio", command="npx"))
    assert client.post("/api/move/secrets/export").status_code == 403
    response = _export(_stamped({"desktop_authenticated": True}))
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (
        body["ai_providers"]["openai"]["api_key"] == "sk-OPENAI"
        and "ollama" not in body["ai_providers"]
    )
    assert {"kind": "mcp_server", "id": "local", "reason": "local_mcp"} in body["skipped"]
    # The two price sources always exist; the local MCP server is listed as not moving.
    assert {c["id"] for c in body["connectors"]} == {"coingecko", "yahoo_finance"}
    assert "mcp_servers" not in body


def test_a_move_belongs_to_the_credential_that_began_it(client):
    token = AuthStore().api_token()
    move_id = client.post(
        "/api/move/begin",
        json={"size": len(BUNDLE), "sha256": _sha(BUNDLE)},
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
    ).json()["move_id"]
    assert (
        client.get(f"/api/move/{move_id}", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )
    for stranger in (
        _put(client, move_id, 0),
        client.get(f"/api/move/{move_id}"),
        client.post(f"/api/move/{move_id}/commit", json={}, headers=HEADERS),
        client.post(f"/api/move/{move_id}/secrets", json={}, headers=HEADERS),
        client.post(f"/api/move/{move_id}/finish", headers=HEADERS),
        client.delete(f"/api/move/{move_id}", headers=HEADERS),
    ):
        assert stranger.status_code == 404
    assert client.get("/api/move/mv_" + "0" * 32).status_code == 404
    with pytest.raises(NotFoundError):
        move._dir("../../etc")


def test_only_one_move_runs_at_a_time_but_an_owner_can_restart_its_own(client):
    token = AuthStore().api_token()
    mine = {**HEADERS, "Authorization": f"Bearer {token}"}
    first = _begin(client, headers=mine).json()["move_id"]
    assert _begin(client).status_code == 409
    second = _begin(client, headers=mine).json()["move_id"]
    assert second != first and not move._dir(first).exists()


def test_abort_and_expiry_remove_every_temp_file(client, monkeypatch):
    move_id = _begin(client).json()["move_id"]
    _put(client, move_id, 0)
    assert client.delete(f"/api/move/{move_id}", headers=HEADERS).status_code == 204
    assert not move._dir(move_id).exists()
    stale = _begin(client).json()["move_id"]
    now = time.time()
    monkeypatch.setattr(move.time, "time", lambda: now + move.MOVE_TTL_SECONDS + 5)
    move.sweep_expired()
    assert not move._dir(stale).exists()


def test_an_import_interrupted_by_a_restart_reads_as_failed(client):
    move_id = _begin(client).json()["move_id"]
    meta = move._read(move_id)
    meta["status"] = "importing"
    move._write(meta)
    assert client.get(f"/api/move/{move_id}").json()["status"] == "failed"


def test_beginning_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(move, "_begin_limiter", ratelimit.SlidingWindowLimiter(2, 3600))
    token = AuthStore().api_token()
    mine = {**HEADERS, "Authorization": f"Bearer {token}"}
    assert _begin(client, headers=mine).status_code == 200
    assert _begin(client, headers=mine).status_code == 200
    assert _begin(client, headers=mine).status_code == 409


def test_every_move_route_refuses_an_unauthenticated_remote_caller(db_isolated):
    from backend.main import app

    remote = TestClient(app, client=("203.0.113.7", 9999))
    calls = [
        remote.post("/api/move/begin", json={"size": 1, "sha256": "0" * 64}, headers=HEADERS),
        remote.get("/api/move/mv_" + "0" * 32),
        remote.put("/api/move/mv_" + "0" * 32 + "/parts/0", content=b"x", headers=HEADERS),
        remote.post("/api/move/mv_" + "0" * 32 + "/commit", json={}, headers=HEADERS),
        remote.post("/api/move/mv_" + "0" * 32 + "/secrets", json={}, headers=HEADERS),
        remote.post("/api/move/mv_" + "0" * 32 + "/finish", headers=HEADERS),
        remote.post("/api/move/secrets/export", headers=HEADERS),
        remote.delete("/api/move/mv_" + "0" * 32, headers=HEADERS),
    ]
    assert [call.status_code for call in calls] == [401] * len(calls)


def test_pausing_this_copy_stops_the_named_connectors_and_automations_and_resume_restores_them(
    client, monkeypatch
):
    from backend.db.telegram_config_store import TelegramConfigStore
    from backend.services import connectors

    TelegramConfigStore().update({"enabled": True, "bot_token": "1:T", "allowed_chat_ids": ["7"]})
    monkeypatch.setattr(move_secrets, "pause_automations", lambda: ["auto_1"])
    resumed: list[list[str]] = []
    monkeypatch.setattr(move_secrets, "resume_automations", lambda ids: resumed.append(ids) or ids)
    assert client.post("/api/move/pause-copy", json={"connectors": ["telegram"]}).status_code == 403
    paused = client.post(
        "/api/move/pause-copy", json={"connectors": ["telegram", "plaid"]}, headers=HEADERS
    ).json()
    assert paused == {"connectors": ["telegram"], "automations": ["auto_1"]}
    assert connectors.get_connector("telegram").enabled is False
    back = client.post(
        "/api/move/resume-copy", json=paused | {"automations": ["auto_1"]}, headers=HEADERS
    ).json()
    assert (
        back["connectors"] == ["telegram"]
        and back["automations"] == ["auto_1"]
        and resumed == [["auto_1"]]
    )
    assert connectors.get_connector("telegram").enabled is True


def test_credentials_are_never_exported_to_a_bearer_token_loopback_trust_or_a_disabled_login(
    client,
):
    token = AuthStore().api_token()
    bearer = {"Authorization": f"Bearer {token}"}
    assert _export(client).status_code == 403  # unconfigured, loopback
    assert _export(client, bearer).status_code == 403
    assert _export(client, bearer, nonce=False).status_code == 403
    passthrough = _stamped({"gateway_passthrough": True})
    assert _export(passthrough, bearer).status_code == 403
    AuthStore().update_auth_settings(auth_disabled=True)
    assert _export(client).status_code == 403
    assert _export(client, bearer).status_code == 403


def test_a_gateway_session_must_be_fresh(client, monkeypatch):
    from backend.api import move as move_api

    monkeypatch.setattr(move_api.terminal, "gateway_active", lambda: True)
    now = int(time.time())
    assert (
        _export(
            _stamped({"gateway_authenticated": True, "gateway_session_started": now})
        ).status_code
        == 200
    )
    stale = _export(
        _stamped({"gateway_authenticated": True, "gateway_session_started": now - (2 * 3600 + 60)})
    )
    assert stale.status_code == 401 and stale.json()["detail"]["code"] == "fresh_session_required"
    assert _export(_stamped({"gateway_authenticated": True})).status_code == 401
    assert _export(_stamped({"gateway_passthrough": True})).status_code in (401, 403)
    assert _export(_stamped({})).status_code == 403


def test_a_self_hosted_login_cookie_may_export_but_a_bad_one_may_not(client):
    from backend.core.security import hash_password, sign_session

    store = AuthStore()
    store.set_password_hash(hash_password("a-long-enough-password"))
    store.update_auth_settings(trust_loopback=False)
    good = sign_session({}, store.session_key_bytes(), 3600)
    assert _export(client).status_code == 401
    assert _export(client, {"Cookie": "onw_session=forged"}).status_code == 401
    assert _export(client, {"Cookie": f"onw_session={good}"}).status_code == 200


def test_the_export_needs_a_one_time_unexpired_token_of_the_same_caller(client, monkeypatch):
    who = _stamped({"desktop_authenticated": True})
    assert _export(who, nonce=False).status_code == 403
    assert (
        who.post(
            "/api/move/secrets/export", headers={**HEADERS, "X-Move-Nonce": "guess"}
        ).status_code
        == 403
    )
    nonce = who.post("/api/move/export-nonce", headers=HEADERS).json()["nonce"]
    headers = {**HEADERS, "X-Move-Nonce": nonce}
    assert who.post("/api/move/secrets/export", headers=headers).status_code == 200
    assert who.post("/api/move/secrets/export", headers=headers).status_code == 403  # single use
    other = who.post("/api/move/export-nonce", headers=HEADERS).json()["nonce"]
    gateway = _stamped({"gateway_authenticated": True})
    assert (
        gateway.post(
            "/api/move/secrets/export", headers={**HEADERS, "X-Move-Nonce": other}
        ).status_code
        == 403
    )
    late = who.post("/api/move/export-nonce", headers=HEADERS).json()["nonce"]
    real = time.time()
    monkeypatch.setattr(move.time, "time", lambda: real + move._EXPORT_NONCE_SECONDS + 1)
    assert (
        who.post("/api/move/secrets/export", headers={**HEADERS, "X-Move-Nonce": late}).status_code
        == 403
    )


def test_the_export_is_logged_by_name_without_values(client, caplog):
    caplog.set_level(logging.INFO)
    from backend.db.model_config_store import ModelConfigStore

    ModelConfigStore().update_provider("openai", {"api_key": "sk-LOGGED", "enabled": True})
    assert _export(_stamped({"desktop_authenticated": True})).status_code == 200
    assert "move secrets exported" in caplog.text and "sk-LOGGED" not in caplog.text


def _moved_book(client):
    move_id = _begin(client).json()["move_id"]
    _upload_all(client, move_id)
    client.post(f"/api/move/{move_id}/commit", json={"replace": True}, headers=HEADERS)
    _wait(client, move_id)
    return move_id


def test_a_malicious_move_back_payload_cannot_forge_what_this_server_observed_or_trust_a_sender(
    client,
):
    from backend.db.connector_store import ConnectorStore
    from backend.db.mcp_client_store import McpClientStore

    move_id = _moved_book(client)
    payload = {
        "connectors": [
            _connector(
                "telegram",
                config={
                    "allowed_chat_ids": ["7"],
                    "operator_trust": True,
                    "planted": "x",
                },
                secrets={"bot_token": "1:T", "planted_secret": "p", "sessions": {"1": "forged"}},
            ),
            _connector(
                "lunch_flow",
                config={"client_id": "cid"},
                secrets={
                    "client_secret": "cs",
                    "access_token": "FORGED-ACCESS",
                    "refresh_token": "FORGED-REFRESH",
                },
                state={"user_id": "forged-user", "accounts_meta": {"x": {}}},
            ),
            _connector(
                "mcp",
                "mcp:docs",
                name="Docs",
                config={
                    "transport": "http",
                    "auth_type": "bearer",
                    "verification": "verified",
                    "publisher": "Anthropic",
                    "homepage_url": "https://x",
                    "tool_allowlist": ["transfer_money"],
                },
                secrets={
                    "url": "https://mcp.example.com/mcp",
                    "token": "T",
                    "command": "rm",
                    "args": ["-rf"],
                },
                state={
                    "discovered_tools": [{"name": "transfer_money", "read_only": True}],
                    "server_info_name": "Official",
                    "last_status": "ok",
                },
            ),
            _connector(
                "mcp",
                "mcp:bad id!",
                config={"transport": "http"},
                secrets={"url": "https://mcp.example.com"},
            ),
            _connector(
                "mcp",
                "mcp:evil",
                config={"transport": "http"},
                secrets={"url": "file:///etc/passwd"},
            ),
        ],
    }
    body = client.post(f"/api/move/{move_id}/secrets", json=payload, headers=HEADERS).json()
    telegram = ConnectorStore().get("telegram")
    assert telegram["config"] == {"allowed_chat_ids": ["7"]}  # nothing planted, no operator_trust
    assert ConnectorStore().secrets("telegram") == {"bot_token": "1:T"}  # the chat map is hidden
    flow = ConnectorStore().secrets("lunch_flow")
    assert flow == {"client_secret": "cs"}  # a forged grant is never taken from a sender
    assert ConnectorStore().get("lunch_flow")["state"] == {}
    (docs,) = McpClientStore().all()
    assert docs.id == "docs" and docs.token == "T" and not docs.enabled
    assert (
        docs.discovered_tools == [] and docs.tool_allowlist == [] and docs.verification == "unknown"
    )
    assert docs.publisher is None and docs.server_info_name is None and docs.homepage_url is None
    assert docs.last_status is None and docs.command == "" and docs.args == []
    assert {"kind": "mcp_server", "id": "bad id!", "reason": "invalid"} in body["skipped"]
    assert {"kind": "mcp_server", "id": "evil", "reason": "invalid"} in body["skipped"]
    assert {"kind": "connector", "id": "telegram", "reason": "operator_trust"} in body["skipped"]
    finished = client.post(f"/api/move/{move_id}/finish", headers=HEADERS).json()
    assert finished["enabled"] == []
    assert "operator_trust" in {item["id"] for item in finished["checklist"]}


def test_a_connector_the_catalog_does_not_know_is_refused(client):
    move_id = _moved_book(client)
    for bad in (
        _connector("not_a_kind", "not_a_kind"),
        _connector("plaid", "plaid:other"),  # an id the kind cannot own
        _connector("web_search", "web_search:not-a-provider"),
    ):
        response = client.post(
            f"/api/move/{move_id}/secrets", json={"connectors": [bad]}, headers=HEADERS
        )
        assert response.status_code == 422, bad


def test_a_payload_in_the_old_wire_format_is_refused_with_a_clear_message(client):
    """Connectors used to travel as per-store settings documents plus a separate MCP list.
    Both copies of the app must be on the same version; a stale sender is told so."""
    move_id = _moved_book(client)
    for payload in (
        {"connectors": [{"id": "telegram", "config": {"bot_token": "1:T"}}]},
        {"connectors": [], "mcp_servers": []},
    ):
        response = client.post(f"/api/move/{move_id}/secrets", json=payload, headers=HEADERS)
        assert response.status_code == 422
        assert "Update both copies of the app" in response.json()["detail"]


def test_a_moved_connector_is_recreated_off_and_replaces_what_was_there(client):
    from backend.db.connector_store import ConnectorStore
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update(
        {"enabled": True, "client_id": "old", "secret": "old-secret", "access_token": "old-tok"}
    )
    move_id = _moved_book(client)
    payload = {
        "connectors": [
            _connector(
                "plaid",
                config={"client_id": "new", "environment": "production"},
                secrets={"secret": "new-secret", "access_token": "new-tok", "item_id": "item-1"},
                enabled=True,
            ),
            _connector(
                "ynab",
                secrets={"access_token": "ynab-tok"},
                state={"budget_name": "Home", "last_error": "sender's problem"},
            ),
        ]
    }
    assert (
        client.post(f"/api/move/{move_id}/secrets", json=payload, headers=HEADERS).status_code
        == 200
    )
    plaid = ConnectorStore().get("plaid")
    assert plaid["enabled"] is False
    assert plaid["config"] == {"client_id": "new", "environment": "production"}
    assert ConnectorStore().secrets("plaid") == {
        "secret": "new-secret",
        "access_token": "new-tok",
        "item_id": "item-1",
    }
    # Only the state a moved link needs travels; the sender's health does not.
    assert ConnectorStore().get("ynab")["state"] == {"budget_name": "Home"}


def test_a_moved_price_source_keeps_its_row_so_health_and_the_on_switch_survive(client):
    from backend.db.coingecko_quota_store import CoinGeckoQuotaStore
    from backend.db.connector_store import ConnectorStore
    from backend.db.market_data_config_store import MarketDataConfigStore
    from backend.db.market_provider_circuit_store import MarketProviderCircuitStore

    MarketDataConfigStore().update({"coingecko_api_key": "mine"})
    CoinGeckoQuotaStore().reserve(4, 10_000.0)
    MarketProviderCircuitStore("yahoo_history").record_failure(10_000.0)
    before = ConnectorStore().get("coingecko")
    move_id = _moved_book(client)
    payload = {
        "connectors": [
            _connector("coingecko", config={"tier": "pro"}, secrets={"api_key": "theirs"}),
            _connector("yahoo_finance", config={"primary_for_major_crypto": False}),
        ]
    }

    response = client.post(f"/api/move/{move_id}/secrets", json=payload, headers=HEADERS)

    assert response.status_code == 200
    coingecko = ConnectorStore().get("coingecko")
    assert coingecko["enabled"] is True  # a price source is always on
    assert coingecko["config"] == {"tier": "pro"}
    assert ConnectorStore().secrets("coingecko") == {"api_key": "theirs"}
    assert coingecko["state"] == before["state"] and coingecko["state"]["quota"]
    assert coingecko["created_at"] == before["created_at"]  # the row was never recreated
    yahoo = ConnectorStore().get("yahoo_finance")
    assert yahoo["enabled"] is True
    assert yahoo["config"] == {"primary_for_major_crypto": False}
    assert "yahoo_history" in yahoo["state"]["circuit"]
    finished = client.post(f"/api/move/{move_id}/finish", headers=HEADERS).json()
    assert finished["enabled"] == []


def test_a_moved_price_source_without_a_key_leaves_this_instances_key(client):
    from backend.db.connector_store import ConnectorStore
    from backend.db.market_data_config_store import MarketDataConfigStore

    MarketDataConfigStore().update({"coingecko_api_key": "mine"})
    move_id = _moved_book(client)

    client.post(
        f"/api/move/{move_id}/secrets",
        json={"connectors": [_connector("coingecko", config={"tier": "demo"})]},
        headers=HEADERS,
    )

    assert ConnectorStore().secrets("coingecko") == {"api_key": "mine"}


def test_an_exported_connector_carries_its_settings_and_secrets_but_never_a_hidden_one(client):
    from backend.connectors import session_map
    from backend.db.plaid_config_store import PlaidConfigStore
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"enabled": True, "bot_token": "9:TOK", "allowed_chat_ids": ["7"]})
    session_map.get_or_create_session("telegram", "7")
    PlaidConfigStore().update({"client_id": "cid", "secret": "s", "access_token": "tok"})
    PlaidConfigStore().record_status("ok")

    body = _export(_stamped({"desktop_authenticated": True})).json()
    by_id = {c["id"]: c for c in body["connectors"]}
    telegram = by_id["telegram"]
    assert telegram["kind"] == "telegram" and telegram["enabled"] is True
    assert telegram["secrets"] == {"bot_token": "9:TOK"}  # the chat map stays home
    assert telegram["config"]["allowed_chat_ids"] == ["7"]
    plaid = by_id["plaid"]
    assert plaid["secrets"] == {"secret": "s", "access_token": "tok"}
    assert "state" not in plaid  # nothing in plaid's state is worth moving
    assert "last_status" not in str(plaid)


def test_finish_switches_on_only_what_the_payload_had_on(client):
    move_id = _moved_book(client)
    payload = {
        "connectors": [
            _connector(
                "telegram",
                config={"allowed_chat_ids": ["7"]},
                secrets={"bot_token": "1:T"},
                enabled=True,
            ),
            _connector("plaid", secrets={"secret": "s"}),
            _connector(
                "mcp",
                "mcp:off",
                config={"transport": "http"},
                secrets={"url": "https://mcp.example.com/a"},
            ),
        ],
    }
    client.post(f"/api/move/{move_id}/secrets", json=payload, headers=HEADERS)
    assert client.post(f"/api/move/{move_id}/finish", headers=HEADERS).json()["enabled"] == [
        "telegram"
    ]


def test_a_chunked_secrets_body_is_capped_while_streaming(client, monkeypatch):
    from backend.api import move as move_api

    monkeypatch.setattr(move_api, "_MAX_SECRETS_BYTES", 1000)
    move_id = _moved_book(client)
    pieces = (b"x" * 400 for _ in range(10))  # no Content-Length: chunked
    response = client.post(f"/api/move/{move_id}/secrets", content=pieces, headers=HEADERS)
    assert response.status_code == 413


def _moved(client, payload):
    from backend.db.onboarding_state_store import OnboardingStateStore

    move_id = _begin(client).json()["move_id"]
    _upload_all(client, move_id)
    client.post(f"/api/move/{move_id}/commit", json={"replace": True}, headers=HEADERS)
    _wait(client, move_id)
    OnboardingStateStore().mark_restored()  # what the import itself leaves behind
    assert (
        client.post(f"/api/move/{move_id}/secrets", json=payload, headers=HEADERS).status_code
        == 200
    )
    assert client.post(f"/api/move/{move_id}/finish", headers=HEADERS).status_code == 200
    return OnboardingStateStore()


def test_a_move_that_brings_its_model_key_skips_the_pick_a_model_step(client):
    payload = {
        "ai_providers": {"anthropic": {"api_key": "sk-ant-x", "enabled": True}},
        "default_model": "anthropic:claude-sonnet-4-5",
        "connectors": [],
    }
    state = _moved(client, payload)
    assert state.completed_at() and not state.restore_pending()


def test_a_move_without_a_usable_model_key_still_asks_for_one(client):
    payload = {"ai_providers": {"claude_cli": {}}, "connectors": []}
    state = _moved(client, payload)
    assert state.restore_pending() and not state.completed_at()

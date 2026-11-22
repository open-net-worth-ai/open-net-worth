"""The "Talk to Lucius" grant (ADR 2026-11-22-talk-to-lucius-is-a-third-mcp-access-mode): the owner
picks it on the consent page; the app reaches talk_to_lucius and nothing else — no other tool and no
resource. Plus MCP progress streaming for a tools/call that asks for it."""

from __future__ import annotations

import pytest

import backend.core.config as config
from tests.mcp_oauth_helpers import (
    CLAUDE_CALLBACK,
    CLAUDE_CLIENT,
    MCP_URL,
    PUBLIC,
    build_client,
    claude_doc,
    pkce,
    query,
    reset_env,
    rpc,
    seed_auth,
    sign_in,
    start,
)

pytestmark = pytest.mark.flags(mcp_oauth=True)


@pytest.fixture(autouse=True)
def oauth_env(monkeypatch):
    from backend.api import mcp_oauth_server
    from backend.core import ratelimit
    from backend.services.mcp_oauth import clients

    seed_auth()
    reset_env(monkeypatch, ONW_PUBLIC_URL=PUBLIC)
    clients.reset_cache_for_tests()
    monkeypatch.setattr(clients, "_fetch_cimd", lambda client_id: claude_doc())
    ratelimit.login_limiter.clear()
    mcp_oauth_server.token_limiter.clear()
    yield
    config._settings = None


@pytest.fixture
def client():
    return build_client()


def _connect(client, **body) -> dict:
    verifier, challenge = pkce()
    request_id = start(client, challenge)
    assert sign_in(client, request_id).status_code == 200
    res = client.post(
        "/api/oauth/consent", json={"request": request_id, "decision": "allow", **body}
    )
    assert res.status_code == 200, res.text
    tok = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": query(res.json()["redirect"])["code"],
            "code_verifier": verifier,
            "client_id": CLAUDE_CLIENT,
            "redirect_uri": CLAUDE_CALLBACK,
            "resource": MCP_URL,
        },
    )
    assert tok.status_code == 200, tok.text
    return tok.json()


def _names(client, token) -> set[str]:
    return {t["name"] for t in rpc(client, token, "tools/list").json()["result"]["tools"]}


def test_talk_reaches_talk_to_lucius_and_nothing_else(client):
    body = _connect(client, talk_only=True)
    assert body["scope"] == "talk"
    token = body["access_token"]
    assert _names(client, token) == {"talk_to_lucius"}
    assert rpc(client, token, "resources/list").json()["result"]["resources"] == []
    res = rpc(client, token, "resources/read", {"uri": "accounts://list"}).json()
    assert "error" in res


def test_talk_cannot_call_a_book_tool(client, monkeypatch):
    import backend.mcp.adapter as adapter

    ran = []

    async def fake_execute(name, args):
        ran.append(name)
        return '{"ok": true}', None

    monkeypatch.setattr(adapter, "execute_tool", fake_execute)
    token = _connect(client, talk_only=True)["access_token"]
    for name in ("get_net_worth", "run_sql_query", "add_transactions"):
        result = rpc(client, token, "tools/call", {"name": name, "arguments": {}}).json()["result"]
        assert result["isError"] is True, name
    assert ran == []


def test_read_cannot_talk_but_read_and_change_can(client):
    assert "talk_to_lucius" not in _names(client, _connect(client)["access_token"])
    assert "talk_to_lucius" in _names(client, _connect(client, allow_write=True)["access_token"])


def test_talk_cannot_be_refreshed_into_read(client):
    body = _connect(client, talk_only=True)
    res = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": body["refresh_token"],
            "client_id": CLAUDE_CLIENT,
            "scope": "read",
        },
    )
    assert res.status_code == 400


def test_a_tool_call_that_asks_for_progress_is_streamed(client, monkeypatch):
    import json

    import backend.mcp.adapter as adapter
    from backend.core import mcp_progress

    async def fake_execute(name, args):
        await mcp_progress.report("Reading your budget")
        return '{"answer": "It fits."}', None

    monkeypatch.setattr(adapter, "execute_tool", fake_execute)
    token = _connect(client, talk_only=True)["access_token"]
    body = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "talk_to_lucius",
            "arguments": {"question": "hi"},
            "_meta": {"progressToken": "p1"},
        },
    }
    res = client.post(
        "/mcp",
        json=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert res.headers["content-type"].startswith("text/event-stream")
    frames = [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]
    assert frames[0]["method"] == "notifications/progress"
    assert frames[0]["params"] == {
        "progressToken": "p1",
        "progress": 1,
        "message": "Reading your budget",
    }
    assert frames[-1]["id"] == 7 and "It fits." in frames[-1]["result"]["content"][0]["text"]


def test_a_call_without_a_progress_token_is_plain_json(client, monkeypatch):
    import backend.mcp.adapter as adapter

    async def fake_execute(name, args):
        return '{"answer": "It fits."}', None

    monkeypatch.setattr(adapter, "execute_tool", fake_execute)
    token = _connect(client, talk_only=True)["access_token"]
    res = rpc(
        client, token, "tools/call", {"name": "talk_to_lucius", "arguments": {"question": "hi"}}
    )
    assert res.headers["content-type"].startswith("application/json")


# ── the in-chat card (MCP Apps) ──────────────────────────────────────────────────


UI_CAPS = {
    "extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}
}


def _with_ui(client, token) -> str:
    """The app's handshake declares MCP Apps support."""
    rpc(client, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": UI_CAPS})
    return token


def _tools(client, token) -> dict:
    return {t["name"]: t for t in rpc(client, token, "tools/list").json()["result"]["tools"]}


def test_the_card_is_declared_and_its_answer_tool_is_app_only(client):
    tools = _tools(client, _with_ui(client, _connect(client, talk_only=True)["access_token"]))
    ui_uri = "ui://open-net-worth/lucius-card.html"
    assert tools["talk_to_lucius"]["_meta"]["ui"]["resourceUri"] == ui_uri
    assert tools["answer_lucius_approval"]["_meta"]["ui"]["visibility"] == ["app"]


def test_no_card_for_an_app_that_did_not_declare_mcp_apps(client, monkeypatch):
    import json

    import backend.mcp.adapter as adapter

    async def fake_execute(name, args):
        body = {"status": "waiting_for_owner", "_card_meta": {"card_tokens": {"t1": "SECRET"}}}
        return json.dumps(body), None

    monkeypatch.setattr(adapter, "execute_tool", fake_execute)
    token = _connect(client, talk_only=True)["access_token"]
    assert "answer_lucius_approval" not in _tools(client, token)
    call = {"name": "talk_to_lucius", "arguments": {"question": "hi"}}
    result = rpc(client, token, "tools/call", call).json()["result"]
    assert "_meta" not in result and "SECRET" not in json.dumps(result)


def test_a_read_grant_gets_no_card(client):
    token = _with_ui(client, _connect(client)["access_token"])
    assert "answer_lucius_approval" not in _tools(client, token)
    res = rpc(client, token, "resources/read", {"uri": "ui://open-net-worth/lucius-card.html"})
    assert "error" in res.json()


def test_the_card_html_is_served_as_an_mcp_app(client):
    token = _with_ui(client, _connect(client, talk_only=True)["access_token"])
    res = rpc(client, token, "resources/read", {"uri": "ui://open-net-worth/lucius-card.html"})
    content = res.json()["result"]["contents"][0]
    assert content["mimeType"] == "text/html;profile=mcp-app"
    assert "ui/initialize" in content["text"] and "answer_lucius_approval" in content["text"]
    assert content["_meta"]["ui"]["domain"].endswith(".claudemcpcontent.com")


def test_card_tokens_reach_the_card_but_not_the_model(client, monkeypatch):
    import json

    import backend.mcp.adapter as adapter

    async def fake_execute(name, args):
        body = {
            "status": "waiting_for_owner",
            "answer": "Cover 40 from Ready to Assign?",
            "waiting": [{"id": "t1", "what": "Move money"}],
            "_card_meta": {"card_tokens": {"t1": "SECRET-TOKEN"}},
            "conversation_id": "c1",
        }
        return json.dumps(body), None

    monkeypatch.setattr(adapter, "execute_tool", fake_execute)
    token = _with_ui(client, _connect(client, talk_only=True)["access_token"])
    call = {"name": "talk_to_lucius", "arguments": {"question": "dinner?"}}
    result = rpc(client, token, "tools/call", call).json()["result"]
    assert "SECRET-TOKEN" not in result["content"][0]["text"]
    assert result["structuredContent"]["waiting"] == [{"id": "t1", "what": "Move money"}]
    assert result["_meta"] == {"card_tokens": {"t1": "SECRET-TOKEN"}}


def _answer(client, token, **args):
    call = {"name": "answer_lucius_approval", "arguments": args}
    return rpc(client, token, "tools/call", call).json()["result"]


def test_answering_needs_the_cards_token_and_the_apps_own_conversation(client, monkeypatch):
    from backend.ai import app_asks

    token = _with_ui(client, _connect(client, talk_only=True)["access_token"])
    monkeypatch.setattr(app_asks, "open_conversation", lambda surface, cid, q: cid)
    ran = []

    async def fake_answer(session_id, card_id, decision, caller, progress):
        ran.append((session_id, card_id, decision))
        return "Done: moved 40 into Dining."

    monkeypatch.setattr(app_asks, "answer_card", fake_answer)
    monkeypatch.setattr(app_asks, "waiting_approvals", lambda sid: [])
    bad = _answer(
        client, token, conversation_id="c1", card_id="t1", card_token="guess", decision="approve"
    )
    assert bad["isError"] is True and ran == []
    tokens = app_asks.card_tokens("c1", [{"id": "t1", "what": "Move money", "in_chat": True}])
    ok = _answer(
        client,
        token,
        conversation_id="c1",
        card_id="t1",
        card_token=tokens["t1"],
        decision="approve",
    )
    assert ran == [("c1", "t1", "approve")]
    assert ok["structuredContent"]["answer"] == "Done: moved 40 into Dining."
    again = _answer(
        client,
        token,
        conversation_id="c1",
        card_id="t1",
        card_token=tokens["t1"],
        decision="approve",
    )
    assert again["isError"] is True and len(ran) == 1  # a token works once


def test_a_card_that_must_be_approved_in_onw_gets_no_token():
    from backend.ai import app_asks

    tokens = app_asks.card_tokens(
        "c2", [{"id": "t9", "what": "Delete 3 transactions", "in_chat": False}]
    )
    assert tokens == {}


def test_destructive_or_outbound_changes_are_approved_in_onw_only():
    from backend.ai.app_asks import in_chat_ok

    assert in_chat_ok("move_money") is True
    assert in_chat_ok("delete_transactions") is False
    assert in_chat_ok("email_send_message") is False
    assert in_chat_ok("mcp__broker__place_order") is False


def test_a_foreign_conversation_cannot_be_answered(client):
    token = _with_ui(client, _connect(client, talk_only=True)["access_token"])
    res = _answer(
        client, token, conversation_id="not-mine", card_id="t1", card_token="x", decision="approve"
    )
    assert res["isError"] is True

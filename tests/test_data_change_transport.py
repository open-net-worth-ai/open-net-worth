"""Successful REST write responses refresh both desktop and browser screens."""

from __future__ import annotations

import pytest

from backend.api.data_change_transport import DataChangeMiddleware
from backend.core import data_events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,status,expected",
    [
        ("POST", "/api/transactions", 200, True),
        ("PUT", "/api/profile", 200, True),
        ("PATCH", "/api/accounts/a", 200, True),
        ("DELETE", "/api/transactions/a", 204, True),
        ("POST", "/api/transactions", 401, False),
        ("POST", "/api/transactions", 422, False),
        ("POST", "/api/transactions", 500, False),
        ("GET", "/api/accounts", 200, False),
        ("HEAD", "/api/accounts", 200, False),
        ("OPTIONS", "/api/accounts", 200, False),
        ("POST", "/external", 200, False),
        # Outlook simulations compute only; announcing them refetched every screen per drag move
        ("POST", "/api/plan/outlook", 200, False),
        ("POST", "/api/plan/outlook/insights", 200, False),
        ("PUT", "/api/plan/baseline", 200, True),
    ],
)
async def test_only_successful_api_write_notifies_after_final_body(
    monkeypatch, method, path, status, expected
):
    received = []
    monkeypatch.setattr(
        data_events, "publish", lambda domains: received.append(("changed", domains))
    )

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        assert all(event[0] != "changed" for event in received)
        await send({"type": "http.response.body", "body": b"last"})

    async def send(message):
        received.append((message["type"], message.get("body")))

    await DataChangeMiddleware(app)({"type": "http", "method": method, "path": path}, None, send)
    assert (received[-1] == ("changed", ["*"])) is expected
    assert sum(event[0] == "changed" for event in received) == int(expected)


@pytest.mark.asyncio
async def test_interrupted_write_response_does_not_publish(monkeypatch):
    changed = []
    monkeypatch.setattr(data_events, "publish", changed.append)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})
        raise RuntimeError("response interrupted")

    async def send(message):
        pass

    with pytest.raises(RuntimeError, match="interrupted"):
        await DataChangeMiddleware(app)(
            {"type": "http", "method": "POST", "path": "/api/write"}, None, send
        )
    assert changed == []


@pytest.mark.asyncio
async def test_final_send_failure_does_not_publish(monkeypatch):
    changed = []
    monkeypatch.setattr(data_events, "publish", changed.append)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"complete"})

    async def send(message):
        if message["type"] == "http.response.body":
            raise RuntimeError("client disconnected")

    with pytest.raises(RuntimeError, match="disconnected"):
        await DataChangeMiddleware(app)(
            {"type": "http", "method": "POST", "path": "/api/write"}, None, send
        )
    assert changed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
async def test_non_http_scope_passes_through_without_publication(monkeypatch, scope_type):
    changed = []
    monkeypatch.setattr(data_events, "publish", changed.append)
    scopes = []

    async def app(scope, receive, send):
        scopes.append(scope)

    scope = {"type": scope_type}
    await DataChangeMiddleware(app)(scope, None, None)
    assert scopes == [scope]
    assert changed == []

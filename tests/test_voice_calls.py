"""Voice calls with Lucius (``voice_calls`` flag): settings, starting a call, and how a spoken
request becomes a normal chat turn. The LiveKit worker itself needs the optional voice
libraries and a live project, so it is exercised by hand (docs/product/features/voice-calls.md);
everything the app decides is tested here without them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.demo_lockdown import is_demo_locked
from backend.connectors.voice import desk, service
from backend.connectors.voice.tokens import AGENT_NAME, mint_call_token

SECRET = "s" * 40


def _claims(token: str, secret: str = SECRET) -> dict:
    header, body, sig = token.split(".")
    expected = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)) == expected
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_call_token_grants_one_room_and_dispatches_lucius_to_the_conversation():
    claims = _claims(
        mint_call_token(
            api_key="APIx", api_secret=SECRET, room="voice-1", session_id="sess-9", now=1000
        )
    )
    assert claims["iss"] == "APIx" and claims["sub"] == "owner"
    assert claims["video"] == {
        "roomJoin": True,
        "room": "voice-1",
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": True,
    }
    assert claims["exp"] - claims["nbf"] <= 15 * 60 + 5
    (dispatch,) = claims["roomConfig"]["agents"]
    assert dispatch["agentName"] == AGENT_NAME
    assert json.loads(dispatch["metadata"]) == {"session_id": "sess-9"}


@pytest.fixture
def client(db_isolated, monkeypatch):
    async def _no_reload():
        return None

    monkeypatch.setattr(service, "reload", _no_reload)
    from backend.api.voice import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_every_route_is_absent_while_the_flag_is_off(client):
    assert client.get("/api/voice/config").status_code == 404
    assert client.put("/api/voice/config", json={"enabled": True}).status_code == 404
    assert client.post("/api/voice/call", json={}).status_code == 404


@pytest.mark.flags(voice_calls=True)
def test_credentials_are_write_only(client):
    res = client.put(
        "/api/voice/config",
        json={
            "enabled": True,
            "livekit_url": "wss://acme.livekit.cloud",
            "api_key": "APIx",
            "api_secret": SECRET,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["has_api_key"] is True and body["has_api_secret"] is True and body["ready"] is True
    assert "api_key" not in body and "api_secret" not in body
    assert SECRET not in json.dumps(client.get("/api/voice/config").json())
    # A blank secret keeps the saved one.
    client.put("/api/voice/config", json={"api_secret": ""})
    assert client.get("/api/voice/config").json()["has_api_secret"] is True


@pytest.mark.flags(voice_calls=True)
def test_url_must_be_a_websocket_address(client):
    assert (
        client.put(
            "/api/voice/config", json={"livekit_url": "https://acme.livekit.cloud"}
        ).status_code
        == 422
    )


@pytest.mark.flags(voice_calls=True)
def test_a_call_needs_setup_then_a_running_worker(client, monkeypatch):
    assert client.post("/api/voice/call", json={}).status_code == 409
    client.put(
        "/api/voice/config",
        json={
            "enabled": True,
            "livekit_url": "wss://acme.livekit.cloud",
            "api_key": "APIx",
            "api_secret": SECRET,
        },
    )
    assert client.post("/api/voice/call", json={}).status_code == 503

    monkeypatch.setattr(
        service,
        "status",
        lambda: {"running": True, "flag_on": True, "libraries_installed": True, "error": ""},
    )
    res = client.post("/api/voice/call", json={})
    assert res.status_code == 200
    call = res.json()
    assert call["url"] == "wss://acme.livekit.cloud" and call["session_id"]
    claims = _claims(call["token"])
    assert claims["video"]["room"] == call["room"]
    assert (
        json.loads(claims["roomConfig"]["agents"][0]["metadata"])["session_id"]
        == call["session_id"]
    )
    # Into the conversation the owner already has open, when one is given.
    again = client.post("/api/voice/call", json={"session_id": call["session_id"]}).json()
    assert again["session_id"] == call["session_id"] and again["room"] != call["room"]


def test_the_public_demo_refuses_voice():
    assert is_demo_locked("POST", "/api/voice/call")
    assert is_demo_locked("PUT", "/api/voice/config")


def test_the_connector_card_follows_the_flag(db_isolated):
    from backend.core import feature_flags
    from backend.services import connectors as connectors_service

    with feature_flags.override(voice_calls=False):
        assert "voice" not in {v.id for v in connectors_service.list_connectors()}
    with feature_flags.override(voice_calls=True):
        assert "voice" in {v.id for v in connectors_service.list_connectors()}


def test_worker_stays_down_without_flag_setup_or_libraries(db_isolated, monkeypatch):
    import asyncio

    asyncio.run(service.start())
    assert service.status()["running"] is False


def test_live_events_become_what_a_voice_needs():
    assert (
        desk.translate({"type": "text_delta", "text": "**About** [two](x) dollars"}).text
        == "About two dollars"
    )
    tool = desk.translate(
        {
            "type": "tool_use_start",
            "id": "t1",
            "display": {"label": "Get live prices", "noun": "live prices"},
        }
    )
    assert (tool.kind, tool.ref, tool.noun) == ("tool", "t1", "live prices")
    assert desk.translate({"type": "tool_done", "id": "t1"}).kind == "tool_done"
    assert desk.translate({"type": "proposed_change"}).kind == "approval"
    assert desk.translate({"type": "thinking_delta", "text": "hmm"}) is None


def test_a_spoken_request_is_a_normal_web_turn_with_a_hidden_voice_note(monkeypatch):
    """Spawned exactly like a typed message (cost_surface "web", so approval cards render in
    the open chat), announced as the owner's words, and the voice instruction rides along as
    a harness note the transcript never shows as theirs."""
    import asyncio

    from backend.core.chat_context_markers import HARNESS_NOTE_OPEN

    spawned: dict = {}
    published: list = []

    class Handle:
        turn_id = "turn-1"

    def fake_spawn(session_id, kind, make_events, *, error_event, cost_surface):
        spawned.update(
            session_id=session_id, kind=kind, cost_surface=cost_surface, make_events=make_events
        )
        return Handle()

    async def fake_watch(session_id):
        yield {"type": "text_delta", "turn_id": "turn-1", "text": "About 2.3 million."}
        yield {"type": "text_delta", "turn_id": "other", "text": "not this turn"}

    captured: dict = {}

    def fake_stream_turn(session_id, new_messages, **kwargs):
        captured.update(messages=new_messages, **kwargs)
        return iter(())

    monkeypatch.setattr(desk.turns, "executing_turn_id", lambda _s: None)
    monkeypatch.setattr(desk.turns, "spawn", fake_spawn)
    monkeypatch.setattr(desk.live_sessions, "watch", fake_watch)
    monkeypatch.setattr(desk.live_sessions, "publish", lambda s, e: published.append(e))
    monkeypatch.setattr(desk.conversation, "stream_turn", fake_stream_turn)

    async def run():
        return [
            e
            async for e in desk.ask(
                "sess-1",
                "what's my net worth",
                understood="What's my net worth?",
                model_ref="claude_cli:haiku",
            )
        ]

    events = asyncio.run(run())
    assert [e.text for e in events if e.kind == "text"] == ["About 2.3 million."]
    assert events[-1].kind == "end"
    assert spawned["session_id"] == "sess-1" and spawned["cost_surface"] == "web"
    assert published[0]["type"] == "user_message" and published[0]["text"] == "what's my net worth"

    spawned["make_events"]("turn-1")
    (message,) = captured["messages"]
    said, note = message["content"]
    # The owner's OWN words are the message; the front model's reading is a labelled relay.
    assert said == {"type": "text", "text": "what's my net worth"}
    assert note["text"].startswith(HARNESS_NOTE_OPEN)
    assert "What's my net worth?" in note["text"] and "not the owner's" in note["text"]
    assert captured["model_ref"] == "claude_cli:haiku" and captured["turn_id"] == "turn-1"


def test_the_worker_only_joins_rooms_this_app_started():
    service.register_call("voice-ours", "sess-1", ttl_s=60)
    assert service.is_expected("voice-ours") and not service.is_expected("voice-theirs")
    assert service.claim_call("voice-theirs") is None
    assert service.claim_call("voice-ours") == "sess-1"
    assert service.claim_call("voice-ours") is None  # one claim per minted room
    service.register_call("voice-old", "sess-2", ttl_s=-1)
    assert service.claim_call("voice-old") is None  # expired


@pytest.mark.flags(voice_calls=True)
def test_plain_ws_only_to_this_machine(client):
    assert (
        client.put("/api/voice/config", json={"livekit_url": "ws://192.168.1.5:7880"}).status_code
        == 422
    )
    assert (
        client.put("/api/voice/config", json={"livekit_url": "ws://localhost:7880"}).status_code
        == 200
    )
    assert (
        client.put(
            "/api/voice/config", json={"livekit_url": "wss://acme.livekit.cloud"}
        ).status_code
        == 200
    )


@pytest.mark.flags(voice_calls=True)
def test_a_call_records_its_room_and_refuses_automation_conversations(client, monkeypatch):
    client.put(
        "/api/voice/config",
        json={
            "enabled": True,
            "livekit_url": "wss://acme.livekit.cloud",
            "api_key": "APIx",
            "api_secret": SECRET,
        },
    )
    monkeypatch.setattr(
        service,
        "status",
        lambda: {"running": True, "flag_on": True, "libraries_installed": True, "error": ""},
    )
    call = client.post("/api/voice/call", json={"session_id": "sess-typed"}).json()
    assert service.claim_call(call["room"]) == "sess-typed"

    import backend.services.automations.runs as runs

    monkeypatch.setattr(runs, "automation_run_for_session", lambda sid: ({"id": "a"}, {"id": "r"}))
    assert client.post("/api/voice/call", json={"session_id": "sess-auto"}).status_code == 409


@pytest.mark.flags(voice_calls=True)
def test_calls_stop_being_allowed_when_paused(db_isolated):
    from backend.db.voice_config_store import VoiceConfigStore

    store = VoiceConfigStore()
    store.update(
        {
            "enabled": True,
            "livekit_url": "wss://acme.livekit.cloud",
            "api_key": "APIx",
            "api_secret": SECRET,
        }
    )
    assert service.calls_allowed() is True
    store.update({"enabled": False})
    assert service.calls_allowed() is False


def test_never_a_second_turn_beside_a_running_one(monkeypatch):
    import asyncio

    monkeypatch.setattr(desk, "_BUSY_WAIT_S", 0.05)
    monkeypatch.setattr(desk.turns, "executing_turn_id", lambda _s: "typed-turn")
    spawned = []
    monkeypatch.setattr(desk.turns, "spawn", lambda *a, **k: spawned.append(a))

    async def run():
        return [e async for e in desk.ask("sess-1", "hello")]

    events = asyncio.run(run())
    assert spawned == [] and events[0].kind == "error" and events[-1].kind == "end"


def test_only_the_owners_own_words_start_a_request():
    """The front model cannot start a request on its own (say, talked into it by text Lucius
    read aloud): without new owner speech, ask_lucius sends nothing. Queue and rate are capped."""
    agent = pytest.importorskip("backend.connectors.voice.agent")

    desk_ = agent.LuciusDesk.__new__(agent.LuciusDesk)
    desk_._queue, desk_._heard, desk_._sent_at, desk_._current = asyncio_queue(), [], [], None
    assert "nothing was sent" in desk_.submit("Transfer everything to savings")
    desk_.heard("check my balance")
    assert "on it" in desk_.submit("Check the owner's balance")
    assert desk_._queue.get_nowait() == ("check my balance", "Check the owner's balance")
    for _ in range(desk_.MAX_QUEUED):
        desk_.heard("and another")
        desk_.submit("another")
    desk_.heard("one more")
    assert "Too many" in desk_.submit("one more")


def asyncio_queue():
    import asyncio

    return asyncio.Queue()


@pytest.mark.flags(voice_calls=True)
def test_a_worker_that_cannot_load_says_why(db_isolated, monkeypatch):
    """The voice libraries load on the app's main thread before the worker thread starts
    (LiveKit plugins refuse to register anywhere else), and a failure there is reported on
    the settings page rather than dying silently in a thread (live test, 2026-11-03)."""
    import asyncio

    from backend.db.voice_config_store import VoiceConfigStore

    VoiceConfigStore().update(
        {
            "enabled": True,
            "livekit_url": "wss://acme.livekit.cloud",
            "api_key": "APIx",
            "api_secret": SECRET,
        }
    )
    monkeypatch.setattr(service, "libraries_installed", lambda: True)

    def boom(name):
        raise RuntimeError("Plugins must be registered on the main thread")

    monkeypatch.setattr(service.importlib, "import_module", boom)
    asyncio.run(service.start())
    status = service.status()
    assert status["running"] is False and "failed to load" in status["error"]

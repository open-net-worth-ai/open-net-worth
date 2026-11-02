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

    desk_ = bare_desk(agent)
    assert "nothing was sent" in desk_.submit("Transfer everything to savings")
    desk_.heard("check my balance")
    assert "on it" in desk_.submit("Check the owner's balance")
    assert desk_._queue.get_nowait() == ("check my balance", "Check the owner's balance")
    for i in range(desk_.MAX_QUEUED):
        desk_.heard(f"and another {i}")
        desk_.submit(f"another {i}")
    desk_.heard("one more")
    assert "Too many" in desk_.submit("one more")


def asyncio_queue():
    import asyncio

    return asyncio.Queue()


def bare_desk(agent, said: list | None = None):
    """A ``LuciusDesk`` without a LiveKit session: what it would say lands in ``said``."""
    desk_ = agent.LuciusDesk.__new__(agent.LuciusDesk)
    desk_._queue, desk_._heard, desk_._sent_at, desk_._current = asyncio_queue(), [], [], None
    desk_._spoken_since_heard = False
    desk_._recent = {}
    desk_._checked_in = desk_._approving = False
    desk_._hold_until, desk_._hold_timer = 0.0, None
    desk_._answers, desk_._turn_said = set(), ""
    desk_.hung_up = False
    desk_.rearmed = 0
    if said is not None:
        desk_._say = lambda text, **_: said.append(text)
        desk_._line = lambda intent: said.append(intent)
        desk_.hang_up_after_speech = lambda: setattr(desk_, "hung_up", True)

        class _Session:
            def reset_away_timer(self):
                desk_.rearmed += 1

        desk_._session = _Session()
    return desk_


def test_a_silent_line_is_checked_on_once_then_hung_up():
    """Owner, 2026-11-03: a forgotten call shouldn't stay open. Silence → "still there?";
    more silence → goodbye and hang up. Speaking in between starts over."""
    agent = pytest.importorskip("backend.connectors.voice.agent")
    said: list[str] = []
    desk_ = bare_desk(agent, said)
    desk_.away()
    assert said == [agent.CHECK_IN] and not desk_.hung_up
    assert desk_.rearmed == 1  # the next silence must count too (it never did, live call)
    desk_.heard("yes, sorry")
    desk_.away()
    assert said[-1] == agent.CHECK_IN and not desk_.hung_up
    desk_.away()
    assert said[-1] == agent.HANG_UP and desk_.hung_up


def test_no_check_in_while_lucius_works_or_a_change_awaits_approval():
    agent = pytest.importorskip("backend.connectors.voice.agent")
    said: list[str] = []
    desk_ = bare_desk(agent, said)
    desk_._current = "What did I spend?"
    desk_.away()
    desk_._current, desk_._approving = None, True
    desk_.away()
    assert said == [] and not desk_.hung_up


def test_asked_to_wait_it_waits_then_checks_in():
    """Owner: "if I say wait for me it needs to wait, unless it's too long." A hold delays the
    check-in until it's over (capped at ten minutes); the owner speaking ends it."""
    import asyncio

    agent = pytest.importorskip("backend.connectors.voice.agent")

    async def run():
        said: list[str] = []
        desk_ = bare_desk(agent, said)
        assert "600" in desk_.hold(99_999)  # capped
        desk_.hold(15)
        desk_._hold_until = __import__("time").monotonic() + 0.05
        desk_.away()
        assert said == []  # on hold: nothing yet
        await asyncio.sleep(0.15)
        assert said == [agent.CHECK_IN]
        desk_.hold(120)
        desk_.heard("okay I'm back")
        assert desk_._hold_until == 0.0 and desk_._hold_timer is None

    asyncio.run(run())


@pytest.mark.flags(voice_calls=True)
def test_a_worker_that_cannot_load_says_why(db_isolated, monkeypatch):
    """A worker process that dies while loading (LiveKit's plugins once refused to register)
    is reported on the settings page, never left looking like it runs (live test, 2026-11-03)."""
    import asyncio
    import sys

    from backend.connectors.voice import host
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

    monkeypatch.setattr(host, "_WORKER_ARGV", (sys.executable, "-c", "raise SystemExit(3)"))
    asyncio.run(service.start())
    status = service.status()
    assert status["running"] is False and "couldn't start" in status["error"]


def test_the_page_may_reach_only_the_owners_voice_server():
    """Without this the browser refused the LiveKit connection before the call could even ask
    for the microphone (live test, 2026-11-03). Nothing extra while voice is off or unset."""
    from backend.core.spa_csp import spa_csp

    def connect_src(csp: str) -> str:
        return next(d for d in csp.split("; ") if d.startswith("connect-src"))

    assert connect_src(spa_csp()) == "connect-src 'self'"
    assert connect_src(spa_csp(voice_url=None)) == "connect-src 'self'"
    cloud = connect_src(spa_csp(voice_url="wss://acme-x1.livekit.cloud"))
    assert cloud == "connect-src 'self' https://*.livekit.cloud wss://*.livekit.cloud"
    own = connect_src(spa_csp(voice_url="wss://voice.acme.test:7443"))
    assert own == "connect-src 'self' https://voice.acme.test:7443 wss://voice.acme.test:7443"
    assert connect_src(spa_csp(voice_url="https://not-a-voice-url.test")) == "connect-src 'self'"


def test_only_the_owners_current_turn_is_sent():
    """Live call, 2026-11-03: every utterance of the call reached Lucius as one message. Now only
    the owner's words since the voice last spoke are sent (small talk it answered stays out)."""
    agent = pytest.importorskip("backend.connectors.voice.agent")

    desk_ = bare_desk(agent)
    desk_.heard("Hello, how are you?")
    desk_.spoke()  # the front voice answered the small talk
    desk_.heard("So what was my spending yesterday?")
    desk_.submit("What did the owner spend yesterday?")
    said, _ = desk_._queue.get_nowait()
    assert said == "So what was my spending yesterday?"


def test_every_line_spoken_on_the_call_reaches_the_chat_once():
    """Owner, 2026-11-03: whatever is said on the call belongs in the transcript — by one rule,
    not per kind of line. Lucius's answers are already there (his turn wrote them); lines said
    while he works are left out (they would land after his answer; the chat shows his steps)."""
    import asyncio
    import types

    agent = pytest.importorskip("backend.connectors.voice.agent")

    async def run():
        recorded: list[tuple[str, str]] = []

        async def call(method, **args):
            recorded.append((args["said"], args["reply"]))

        desk_ = bare_desk(agent)
        desk_._session_id, desk_._rt = (
            "s1",
            types.SimpleNamespace(app=types.SimpleNamespace(call=call)),
        )
        desk_._session = types.SimpleNamespace(say=lambda text, **_: None)
        desk_.item_added("assistant", "Hi, it's Lucius.")  # the greeting
        desk_.item_added("user", "how are you")
        desk_.item_added("assistant", "Doing well!")  # small talk
        desk_.item_added("assistant", "Are you still there?")  # a line of our own, like any other
        desk_._say("You spent 40 dollars.", answer=True)
        desk_.item_added("assistant", "You spent 40 dollars.")  # his turn already has it
        desk_._current = "what did I spend"
        desk_.item_added("assistant", "Still working on it.")  # said mid-turn: left out
        await asyncio.sleep(0)
        assert recorded == [
            ("", "Hi, it's Lucius."),
            ("how are you", "Doing well!"),
            ("", "Are you still there?"),
        ]

    asyncio.run(run())


@pytest.mark.flags(voice_calls=True)
def test_a_call_can_be_held_in_vietnamese_but_never_with_an_english_only_ear(client):
    """Owner, 2026-11-03: a call in Vietnamese went unheard — the speech-to-text was pinned to
    English. The language is a setting now, refused beside a model that hears English only."""
    refused = client.put("/api/voice/config", json={"language": "vi"})  # default STT: English-only
    assert refused.status_code == 422 and "English only" in refused.json()["detail"]
    ok = client.put("/api/voice/config", json={"language": "vi", "stt_model": "auto"})
    assert ok.status_code == 200 and ok.json()["language"] == "vi"
    assert client.put("/api/voice/config", json={"language": "xx"}).status_code == 422
    # Switching back to an English-only model is refused while the call is in Vietnamese.
    back = client.put("/api/voice/config", json={"stt_model": "assemblyai/universal-streaming"})
    assert back.status_code == 422


def _ready_config():
    from backend.db.voice_config_store import VoiceConfigStore

    VoiceConfigStore().update(
        {
            "enabled": True,
            "livekit_url": "wss://acme.livekit.cloud",
            "api_key": "APIx",
            "api_secret": SECRET,
        }
    )


def test_with_the_flag_off_no_worker_starts_and_no_call_is_taken(db_isolated, monkeypatch):
    """Owner, 2026-11-03: off must mean off — not just no Call button. Set up or not, no
    worker process is started, and a worker already running refuses every call."""
    import asyncio

    from backend.connectors.voice import host

    _ready_config()
    monkeypatch.setattr(service, "libraries_installed", lambda: True)
    spawned: list = []
    monkeypatch.setattr(host, "WorkerProcess", lambda *a, **k: spawned.append(a))
    asyncio.run(service.start())
    assert spawned == [] and service.status()["running"] is False
    assert service.calls_allowed() is False


def test_flipping_the_flag_starts_or_stops_the_worker_at_once(db_isolated, monkeypatch):
    """The worker followed the flag only at the next restart: turned off in Settings, it kept
    taking calls until then. The flag's own switch now applies it."""
    from backend.api.feature_flags import router

    reloads: list[str] = []

    async def reload():
        reloads.append("reload")

    monkeypatch.setattr(service, "reload", reload)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.put("/api/feature-flags/voice_calls", json={"enabled": True}).status_code == 200
    assert client.put("/api/feature-flags/voice_calls", json={"enabled": False}).status_code == 200
    assert reloads == ["reload", "reload"]

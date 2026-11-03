"""Voice calls on the public demo (services/demo_calls, ADR
2026-11-03-voice-calls-are-a-fast-front-voice-over-lucius, amendment "On the public demo").

A call spends the demo's LiveKit inference by the minute, and Lucius's tokens on top. These pin
what that protection rests on: a visitor may only START a call, only into their own
conversation, on their own token budget; every cap refuses with a reason naming it; a call's
own limit reaches the worker, which hangs up at it, and the app closes the room if it doesn't;
a call whose end is never reported counts as having run to its limit. Off a demo none of it
exists.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.connectors.voice import host as voice_host
from backend.connectors.voice import service
from backend.services import demo_calls, demo_quota, demo_visitors

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_ALICE = "a" * 32
_BOB = "b" * 32
_ENV = (
    "DEMO_MODE",
    "DEMO_OPEN_ACCESS",
    "DEMO_TRUSTED_PROXY_HOPS",
    "DEMO_TURNS_PER_WINDOW",
    "DEMO_TOKENS_PER_IP_DAILY",
    "DEMO_TOKENS_TOTAL_DAILY",
    "DEMO_CALL_MAX_S",
    "DEMO_CALLS_PER_VISITOR_DAILY",
    "DEMO_CALL_MINUTES_PER_VISITOR_DAILY",
    "DEMO_CALL_MINUTES_TOTAL_DAILY",
    "DEMO_CALLS_CONCURRENT",
    "AUTH_DISABLED",
    "AUTH_TRUST_LOOPBACK",
    "AUTH_PASSWORD",
    "ONW_GATEWAY_TOKEN",
)


class _Clock:
    now = 10_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    c = _Clock()
    demo_calls.reset(clock=c)
    yield c
    demo_calls.reset()


def _reset_all() -> None:
    config._settings = None
    demo_quota.reset()
    demo_visitors.reset()


@pytest.fixture
def demo(monkeypatch, db_isolated, clock):
    """A public demo with the agreed default caps (only the defaults are asserted below)."""
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_OPEN_ACCESS", "1")
    monkeypatch.setenv("DEMO_TRUSTED_PROXY_HOPS", "1")
    monkeypatch.setenv("DEMO_TOKENS_PER_IP_DAILY", "1000")
    monkeypatch.setenv("DEMO_TOKENS_TOTAL_DAILY", "50000")
    monkeypatch.setenv("AUTH_PASSWORD", "unpublished-random-secret-for-this-test")
    _reset_all()
    demo_calls.reset(clock=clock)
    yield clock
    _reset_all()


@pytest.fixture
def not_a_demo(monkeypatch, db_isolated, clock):
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)
    _reset_all()
    yield clock
    _reset_all()


def _min(minutes: float) -> float:
    return minutes * 60


# ── the caps themselves ────────────────────────────────────────────────────────────


def test_the_caps_are_configured_from_the_environment_with_the_agreed_defaults(monkeypatch):
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)
    cfg = config.Settings()
    assert (
        cfg.demo_call_max_s,
        cfg.demo_calls_per_visitor_daily,
        cfg.demo_call_minutes_per_visitor_daily,
        cfg.demo_call_minutes_total_daily,
        cfg.demo_calls_concurrent,
    ) == (600, 3, 15, 120, 2)
    monkeypatch.setenv("DEMO_CALL_MAX_S", "120")
    monkeypatch.setenv("DEMO_CALLS_CONCURRENT", "5")
    cfg = config.Settings()
    assert cfg.demo_call_max_s == 120 and cfg.demo_calls_concurrent == 5


def test_off_a_demo_there_is_no_cap_and_no_limit(not_a_demo):
    for i in range(20):
        admission = demo_calls.start(f"room-{i}", "1.2.3.4")
        assert admission.allowed and admission.limit_s is None
    assert demo_calls.claim("room-0") is None
    assert demo_calls.remaining("room-0") is None
    assert demo_calls.over("room-0") is False


def test_a_calls_limit_is_the_shortest_of_the_longest_call_and_the_minutes_left(demo):
    clock = demo
    first = demo_calls.start("r1", "1.1.1.1")
    assert first.limit_s == 600  # the longest call
    demo_calls.claim("r1")
    clock.advance(_min(8))
    demo_calls.end("r1")
    # 15 per visitor - 8 used = 7 minutes left for this visitor.
    assert demo_calls.start("r2", "1.1.1.1").limit_s == _min(7)


def test_three_calls_per_visitor_a_day(demo):
    clock = demo
    for i in range(3):
        assert demo_calls.start(f"r{i}", "1.1.1.1").allowed
        demo_calls.claim(f"r{i}")
        clock.advance(30)
        demo_calls.end(f"r{i}")
    refused = demo_calls.start("r3", "1.1.1.1")
    assert not refused.allowed and "3 calls" in refused.reason
    assert refused.retry_after > 0
    assert demo_calls.start("r4", "2.2.2.2").allowed  # another visitor is unaffected
    clock.advance(24 * 3600)
    assert demo_calls.start("r5", "1.1.1.1").allowed  # a rolling day


def test_fifteen_call_minutes_per_visitor_a_day(demo):
    clock = demo
    demo_calls.start("r1", "1.1.1.1")
    demo_calls.claim("r1")
    clock.advance(_min(10))
    demo_calls.end("r1")
    second = demo_calls.start("r2", "1.1.1.1")
    assert second.limit_s == _min(5)
    demo_calls.claim("r2")
    clock.advance(_min(5))
    demo_calls.end("r2")
    refused = demo_calls.start("r3", "1.1.1.1")
    assert not refused.allowed and "15 call minutes" in refused.reason


def test_a_hundred_and_twenty_call_minutes_a_day_across_all_visitors(demo):
    clock = demo
    for i in range(12):  # 12 visitors x 10 minutes = 120
        assert demo_calls.start(f"r{i}", f"10.0.0.{i}").allowed
        demo_calls.claim(f"r{i}")
        clock.advance(_min(10))
        demo_calls.end(f"r{i}")
    refused = demo_calls.start("r-late", "10.0.0.99")
    assert not refused.allowed and "across all visitors" in refused.reason


def test_the_last_minutes_of_the_day_cap_a_calls_own_limit(demo):
    clock = demo
    for i in range(11):
        demo_calls.start(f"r{i}", f"10.0.0.{i}")
        demo_calls.claim(f"r{i}")
        clock.advance(_min(10))
        demo_calls.end(f"r{i}")
    clock.advance(0)
    demo_calls.start("r-a", "10.0.1.1")
    demo_calls.claim("r-a")
    clock.advance(_min(6))
    demo_calls.end("r-a")
    # 120 - 110 - 6 = 4 minutes left in all, though this visitor has 15.
    assert demo_calls.start("r-b", "10.0.1.2").limit_s == _min(4)


def test_two_calls_at_once(demo):
    clock = demo
    assert demo_calls.start("r1", "1.1.1.1").allowed and demo_calls.claim("r1")
    assert demo_calls.start("r2", "2.2.2.2").allowed and demo_calls.claim("r2")
    refused = demo_calls.start("r3", "3.3.3.3")
    assert not refused.allowed and "at once" in refused.reason
    clock.advance(60)
    demo_calls.end("r1")
    assert demo_calls.start("r3", "3.3.3.3").allowed


def test_a_live_call_reserves_its_whole_limit_so_two_cannot_overrun_an_allowance(demo):
    first = demo_calls.start("r1", "1.1.1.1")
    demo_calls.claim("r1")
    second = demo_calls.start("r2", "1.1.1.1")
    assert first.limit_s + second.limit_s == _min(15)


def test_a_call_whose_end_never_arrives_counts_to_its_limit_and_frees_its_slot(demo):
    clock = demo
    demo_calls.start("r1", "1.1.1.1")
    remaining = demo_calls.claim("r1")
    assert remaining == 600
    demo_calls.start("r2", "2.2.2.2")
    demo_calls.claim("r2")
    assert not demo_calls.start("r3", "3.3.3.3").allowed  # two on
    clock.advance(601)  # r1's worker crashed: no end_call, ever
    assert demo_calls.over("r1") and demo_calls.remaining("r1") == 0
    demo_calls.end("r2")
    assert demo_calls.start("r3", "3.3.3.3").allowed
    assert demo_calls.snapshot()["seconds_by_visitor"]["1.1.1.1"] == 600


def test_an_ended_call_counts_what_it_used(demo):
    clock = demo
    demo_calls.start("r1", "1.1.1.1")
    demo_calls.claim("r1")
    clock.advance(_min(2))
    demo_calls.end("r1")
    clock.advance(_min(30))
    demo_calls.end("r1")  # a second report changes nothing
    assert demo_calls.snapshot()["seconds_by_visitor"]["1.1.1.1"] == _min(2)


def test_a_call_never_picked_up_counts_one_call_and_no_minutes(demo):
    clock = demo
    demo_calls.start("r1", "1.1.1.1")
    clock.advance(demo_calls.CLAIM_WINDOW_S + 1)
    assert demo_calls.claim("r1") == 0  # too late: refused
    assert demo_calls.snapshot()["seconds_by_visitor"]["1.1.1.1"] == 0
    for i in range(2):
        demo_calls.start(f"r{i + 2}", "1.1.1.1")
        clock.advance(demo_calls.CLAIM_WINDOW_S + 1)
    assert "3 calls" in demo_calls.start("r9", "1.1.1.1").reason


def test_a_room_is_claimed_once_and_only_if_admitted(demo):
    assert demo_calls.claim("never-admitted") == 0
    demo_calls.start("r1", "1.1.1.1")
    assert demo_calls.claim("r1") == 600
    assert demo_calls.claim("r1") == 0


# ── the route ────────────────────────────────────────────────────────────────────


def _client(cookie: str | None = None, forwarded: str = "203.0.113.7") -> TestClient:
    from backend.main import app

    headers = dict(_XHR)
    if forwarded:
        headers["X-Forwarded-For"] = forwarded
    cookies = {demo_visitors.COOKIE: cookie} if cookie else None
    return TestClient(app, client=("172.18.0.1", 9999), headers=headers, cookies=cookies)


@pytest.fixture
def voice_ready(monkeypatch):
    from backend.db.voice_config_store import VoiceConfigStore

    VoiceConfigStore().update(
        {
            "enabled": True,
            "livekit_url": "wss://acme-demo.livekit.cloud",
            "api_key": "APIdemo",
            "api_secret": "demo-secret-" + "x" * 32,
        }
    )
    monkeypatch.setattr(
        service,
        "status",
        lambda: {"running": True, "flag_on": True, "libraries_installed": True, "error": ""},
    )
    registered = {}
    real_register = service.register_call

    def register(room, session_id, ttl_s):
        registered[room] = ttl_s
        real_register(room, session_id, ttl_s)

    monkeypatch.setattr(service, "register_call", register)
    return registered


@pytest.mark.flags(voice_calls=True)
def test_a_visitor_may_start_a_call_and_nothing_else_under_voice(demo, voice_ready):
    alice = _client(_ALICE)
    res = alice.post("/api/voice/call", json={})
    assert res.status_code == 200, res.text
    call = res.json()
    assert demo_visitors.owner_of(call["session_id"]) == f"c:{_ALICE}"
    # The room is recorded only for as long as a call may ring.
    assert voice_ready[call["room"]] == demo_calls.CLAIM_WINDOW_S
    from backend.api.demo_lockdown import is_demo_locked

    assert all(is_demo_locked(m, "/api/voice/call") for m in ("PUT", "PATCH", "DELETE"))
    assert is_demo_locked("POST", "/api/voice/anything-added-later")
    refused = alice.put("/api/voice/config", json={"api_secret": "stolen"})
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "demo_locked"
    # Reads stay as they were: the settings page shows whether keys are set, never a key.
    view = alice.get("/api/voice/config")
    assert view.status_code == 200 and "demo-secret" not in view.text


@pytest.mark.flags(voice_calls=True)
def test_a_call_goes_only_into_the_visitors_own_conversation(demo, voice_ready, monkeypatch):
    alice, bob = _client(_ALICE, "203.0.113.7"), _client(_BOB, "203.0.113.8")
    mine = alice.post("/api/voice/call", json={}).json()["session_id"]
    assert alice.post("/api/voice/call", json={"session_id": mine}).status_code == 200
    assert bob.post("/api/voice/call", json={"session_id": mine}).status_code == 404

    import backend.services.automations.runs as runs

    monkeypatch.setattr(
        runs,
        "automation_run_for_session",
        lambda sid: ({"id": "a"}, {"id": "r"}) if sid == "run-1" else None,
    )
    assert bob.post("/api/voice/call", json={"session_id": "run-1"}).status_code == 404


@pytest.mark.flags(voice_calls=True)
def test_a_call_is_refused_on_a_spent_token_budget(demo, voice_ready):
    alice = _client(_ALICE)
    demo_quota.note_usage("", 0)
    demo_quota.record_turn("203.0.113.7", "earlier-chat")
    demo_quota.note_usage("earlier-chat", 5_000)  # past the 1000-token visitor budget
    res = alice.post("/api/voice/call", json={})
    assert res.status_code == 429 and "allowance" in res.json()["detail"]
    assert int(res.headers["Retry-After"]) > 0
    assert demo_calls.snapshot()["live"] == 0  # no call slot was taken


@pytest.mark.flags(voice_calls=True)
def test_the_calls_tokens_are_charged_to_the_visitor_who_placed_it(demo, voice_ready):
    """Lucius's turns on a call are spawned by the worker, with no request behind them: the
    call binds its conversation to the visitor, so their tokens land on that visitor."""
    call = _client(_ALICE).post("/api/voice/call", json={}).json()
    demo_quota.note_usage(call["session_id"], 400)  # what ai.cost_tracker reports for a turn
    assert demo_quota.tokens_used("203.0.113.7") == 400
    assert demo_quota.tokens_used(demo_quota.UNATTRIBUTED) == 0


@pytest.mark.flags(voice_calls=True)
def test_each_cap_refuses_at_the_route_with_its_reason(demo, voice_ready):
    clock = demo
    alice = _client(_ALICE)
    for _ in range(3):
        assert alice.post("/api/voice/call", json={}).status_code == 200
        clock.advance(demo_calls.CLAIM_WINDOW_S + 1)  # never picked up: one call, no minutes
    refused = alice.post("/api/voice/call", json={})
    assert refused.status_code == 429 and "3 calls" in refused.json()["detail"]
    assert int(refused.headers["Retry-After"]) > 0


@pytest.mark.flags(voice_calls=True)
def test_off_a_demo_a_call_is_unchanged(not_a_demo, voice_ready):
    from fastapi import FastAPI

    from backend.api.voice import router
    from backend.connectors.voice.tokens import TTL_S

    app = FastAPI()
    app.include_router(router)
    res = TestClient(app).post("/api/voice/call", json={})
    assert res.status_code == 200
    assert voice_ready[res.json()["room"]] == TTL_S
    import base64

    claims = json.loads(base64.urlsafe_b64decode(res.json()["token"].split(".")[1] + "=="))
    assert claims["exp"] - claims["nbf"] == TTL_S + 5 and "canPublishSources" not in claims["video"]
    assert demo_calls.snapshot()["live"] == 0


# ── the app's side of the worker link ─────────────────────────────────────────────


class _Pipe:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data))

    def flush(self) -> None:
        pass


def _host(monkeypatch, closed: list, refused: list | None = None):
    monkeypatch.setattr(service, "calls_allowed", lambda: True)
    monkeypatch.setattr(service, "close_when_over", lambda room, s: closed.append((room, s)))
    monkeypatch.setattr(service, "close_refused", (refused if refused is not None else []).append)
    pipe = _Pipe()
    return voice_host.Host(pipe, asyncio.new_event_loop()), pipe


def _admitted(room: str, session_id: str, ip: str = "203.0.113.7") -> None:
    demo_calls.start(room, ip)
    service.register_call(room, session_id, demo_calls.CLAIM_WINDOW_S)


def test_the_worker_learns_the_calls_limit_and_the_app_closes_the_room_at_it(demo, monkeypatch):
    clock = demo
    closed: list = []
    host, _ = _host(monkeypatch, closed)
    _admitted("voice-a", "sess-a")
    clock.advance(5)  # it rang for a moment
    assert asyncio.run(host._call("claim_call", {"room": "voice-a"})) == "sess-a"
    assert closed == [("voice-a", 595)]
    limit = asyncio.run(host._call("call_limit", {"room": "voice-a", "session_id": "sess-a"}))
    assert limit == 595
    # Only for its own room.
    with pytest.raises(PermissionError):
        asyncio.run(host._call("call_limit", {"room": "voice-zz", "session_id": "sess-a"}))

    clock.advance(_min(3))
    asyncio.run(host._call("end_call", {"session_id": "sess-a", "room": "voice-a"}))
    assert demo_calls.over("voice-a")
    assert demo_calls.snapshot()["seconds_by_visitor"]["203.0.113.7"] == 5 + _min(3)


def test_past_its_limit_the_call_does_nothing_more_for_the_worker(demo, monkeypatch):
    clock = demo
    host, _ = _host(monkeypatch, [])
    _admitted("voice-a", "sess-a")
    asyncio.run(host._call("claim_call", {"room": "voice-a"}))
    clock.advance(600 + demo_calls.GRACE_S - 1)
    assert host._owned({"session_id": "sess-a"}) == "sess-a"  # the goodbye may still be saved
    clock.advance(2)
    with pytest.raises(PermissionError, match="time is up"):
        host._owned({"session_id": "sess-a"})


def test_a_room_admitted_too_long_ago_is_refused_to_the_worker_and_closed(demo, monkeypatch):
    clock = demo
    refused: list = []
    host, _ = _host(monkeypatch, [], refused)
    demo_calls.start("voice-a", "203.0.113.7")
    service.register_call("voice-a", "sess-a", 10_000)  # the registry alone would allow it
    clock.advance(demo_calls.CLAIM_WINDOW_S + 1)
    assert asyncio.run(host._call("claim_call", {"room": "voice-a"})) is None
    assert refused == ["voice-a"]


def test_a_room_the_worker_is_refused_for_is_closed_on_the_demo(demo, monkeypatch):
    """A visitor rejoining a call that ended recreates its room on the demo's project; the
    worker is refused it, and the app closes it so nobody sits in it on the demo's bill."""
    refused: list = []
    host, _ = _host(monkeypatch, [], refused)
    assert asyncio.run(host._call("is_expected", {"room": "voice-ended"})) is False
    assert asyncio.run(host._call("claim_call", {"room": "voice-ended"})) is None
    assert refused == ["voice-ended", "voice-ended"]


def test_off_a_demo_a_refused_room_is_left_alone(not_a_demo, monkeypatch):
    from backend.connectors.voice import rooms

    monkeypatch.setattr(rooms, "delete_room", lambda *a: pytest.fail("never off a demo"))

    async def run():
        service.close_refused("voice-x")
        await asyncio.sleep(0.02)

    asyncio.run(run())


@pytest.mark.flags(voice_calls=True)
def test_a_demo_calls_token_rings_only_briefly_and_carries_only_a_microphone(demo, voice_ready):
    import base64

    call = _client(_ALICE).post("/api/voice/call", json={}).json()
    claims = json.loads(base64.urlsafe_b64decode(call["token"].split(".")[1] + "=="))
    assert claims["exp"] - claims["nbf"] <= demo_calls.CLAIM_WINDOW_S + 5
    assert claims["video"]["canPublishSources"] == ["microphone"]


def test_an_ipv6_visitor_is_one_visitor_per_64(demo):
    for i in range(3):
        assert demo_calls.start(f"r{i}", f"2001:db8:1:2::{i + 1}").allowed
        demo.advance(demo_calls.CLAIM_WINDOW_S + 1)
    refused = demo_calls.start("r9", "2001:db8:1:2:ffff::9")
    assert not refused.allowed and "3 calls" in refused.reason
    assert demo_calls.start("r10", "2001:db8:1:3::1").allowed  # another /64
    assert demo_calls.visitor_key("::ffff:203.0.113.7") == "203.0.113.7"


def test_off_a_demo_the_worker_link_has_no_limit(not_a_demo, monkeypatch):
    closed: list = []
    host, _ = _host(monkeypatch, closed)
    service.register_call("voice-a", "sess-a", 900)
    assert asyncio.run(host._call("claim_call", {"room": "voice-a"})) == "sess-a"
    assert closed == []
    assert (
        asyncio.run(host._call("call_limit", {"room": "voice-a", "session_id": "sess-a"})) is None
    )
    assert host._owned({"session_id": "sess-a"}) == "sess-a"


def test_a_spent_visitor_hears_why_instead_of_a_lucius_turn(demo, monkeypatch):
    host, pipe = _host(monkeypatch, [])
    _admitted("voice-a", "sess-a")
    asyncio.run(host._call("claim_call", {"room": "voice-a"}))
    demo_quota.record_turn("203.0.113.7", "sess-a")
    demo_quota.note_usage("sess-a", 5_000)
    from backend.connectors.voice import desk

    monkeypatch.setattr(desk, "ask", lambda *a, **k: pytest.fail("no turn on a spent budget"))
    asyncio.run(
        host._serve(
            {
                "id": 7,
                "op": "stream",
                "method": "ask",
                "args": {"session_id": "sess-a", "said": "hi"},
            }
        )
    )
    items = [m["item"] for m in pipe.lines if "item" in m]
    assert items[0]["kind"] == "text" and "allowance" in items[0]["text"]
    assert pipe.lines[-1] == {"id": 7, "end": True}


def test_a_calls_turns_run_as_the_visitor_who_placed_it(demo, monkeypatch):
    """``assert_model_available`` (checked where every provider is built) reads the visitor the
    call's turn runs as: the one bound to its conversation."""
    from backend.core.errors import ConfigurationError

    demo_quota.record_turn("203.0.113.7", "sess-a")

    async def in_a_spawned_turn():
        assert voice_host._out_of_budget("sess-a") == ""
        demo_quota.note_usage("sess-a", 5_000)

        async def turn():
            demo_quota.assert_model_available()

        await asyncio.create_task(turn())

    with pytest.raises(ConfigurationError, match="allowance"):
        asyncio.run(in_a_spawned_turn())


def test_the_app_closes_a_room_still_open_past_its_limit(demo, monkeypatch):
    from backend.connectors.voice import rooms

    closed = []
    monkeypatch.setattr(rooms, "delete_room", lambda config, room: closed.append(room))
    monkeypatch.setattr(demo_calls, "GRACE_S", 0)
    demo_calls.start("voice-a", "203.0.113.7")
    demo_calls.start("voice-b", "203.0.113.8")
    demo_calls.claim("voice-a")
    demo_calls.claim("voice-b")
    demo.advance(60)
    demo_calls.end("voice-b")  # this one hung up on time
    demo.advance(600)  # voice-a's worker never reported an end: it is past its limit

    async def run():
        service.close_when_over("voice-a", 0)
        service.close_when_over("voice-b", 0)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    # Both rooms are closed: the worker's word that a call ended is not a limit.
    assert sorted(closed) == ["voice-a", "voice-b"] and demo_calls.ended("voice-a")
    assert demo_calls.snapshot()["seconds_by_visitor"] == {"203.0.113.7": 600, "203.0.113.8": 60}


def test_room_service_calls_are_signed_server_tokens(monkeypatch):
    import base64
    import hashlib
    import hmac

    from backend.connectors.voice import rooms

    sent = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"rooms": [{"name": "voice-1"}]}'

    def fake_urlopen(req, timeout, context):
        sent.update(url=req.full_url, auth=req.get_header("Authorization"), body=req.data)
        return _Resp()

    monkeypatch.setattr(rooms.urllib.request, "urlopen", fake_urlopen)
    cfg = {"livekit_url": "wss://acme-demo.livekit.cloud", "api_key": "APIdemo", "api_secret": "s3"}
    assert rooms.list_rooms(cfg) == [{"name": "voice-1"}]
    assert sent["url"] == "https://acme-demo.livekit.cloud/twirp/livekit.RoomService/ListRooms"
    token = sent["auth"].removeprefix("Bearer ")
    header, body, sig = token.split(".")
    expected = hmac.new(b"s3", f"{header}.{body}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(sig + "==") == expected
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    assert claims["iss"] == "APIdemo" and claims["video"] == {"roomList": True}
    assert claims["exp"] - claims["nbf"] <= 70

    rooms.delete_room(cfg, "voice-1")
    assert sent["url"].endswith("/DeleteRoom") and json.loads(sent["body"]) == {"room": "voice-1"}
    with pytest.raises(ValueError):
        rooms.list_rooms({**cfg, "livekit_url": "file:///etc/passwd"})


# ── the worker hangs up at the limit ─────────────────────────────────────────────


def test_the_voice_warns_then_says_goodbye_and_hangs_up_at_the_limit(monkeypatch):
    agent = pytest.importorskip("backend.connectors.voice.agent")
    from tests.test_voice_calls import bare_desk

    monkeypatch.setattr(agent, "TIME_WARNING_S", 0.2)
    said: list[str] = []
    desk_ = bare_desk(agent, said)
    desk_._limit_timers = []

    async def run():
        desk_.limit_call(0.6)  # the heads-up at 0.4, the goodbye at 0.6
        await asyncio.sleep(0.3)
        assert said == [] and not desk_.hung_up
        await asyncio.sleep(0.2)
        assert said == [agent.TIME_NEARLY_UP] and not desk_.hung_up
        await asyncio.sleep(0.25)

    asyncio.run(run())
    assert said == [agent.TIME_NEARLY_UP, agent.TIME_UP] and desk_.hung_up


def test_a_short_limit_skips_the_heads_up_and_closing_the_call_stops_the_clock(monkeypatch):
    agent = pytest.importorskip("backend.connectors.voice.agent")
    from tests.test_voice_calls import bare_desk

    monkeypatch.setattr(agent, "TIME_WARNING_S", 0.2)
    said: list[str] = []
    desk_ = bare_desk(agent, said)
    desk_._limit_timers = []

    async def run():
        desk_.limit_call(0.3)  # under twice the heads-up: no heads-up
        await asyncio.sleep(0.5)
        assert said == [agent.TIME_UP] and desk_.hung_up

        said.clear()
        desk_.hung_up = False
        desk_.limit_call(0.05)
        desk_._runner = desk_._others = asyncio.ensure_future(asyncio.sleep(10))
        await desk_.aclose()  # the call ended first
        await asyncio.sleep(0.1)

    asyncio.run(run())
    assert said == [] and not desk_.hung_up

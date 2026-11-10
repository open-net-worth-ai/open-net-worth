"""A public demo's per-visitor guard: conversations are private to the visitor who started them,
every deliberate model action is metered against the address the trusted proxy saw, every model
call is checked against the token budgets, and nothing runs unattended (ADR
2026-10-08-a-passwordless-demo-closes-what-the-password-used-to-guard).

Everything here is inert off a demo; the last section proves it.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import backend.core.config as config
from backend.api import demo_visitor
from backend.core.errors import ConfigurationError
from backend.db.session_store import SessionStore
from backend.services import demo_quota, demo_visitors

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
    "AUTH_DISABLED",
    "AUTH_TRUST_LOOPBACK",
    "AUTH_PASSWORD",
    "ONW_GATEWAY_TOKEN",
)


@pytest.fixture
def demo(monkeypatch, db_isolated):
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_OPEN_ACCESS", "1")
    monkeypatch.setenv("DEMO_TRUSTED_PROXY_HOPS", "1")
    monkeypatch.setenv("DEMO_TURNS_PER_WINDOW", "2")
    monkeypatch.setenv("DEMO_TOKENS_PER_IP_DAILY", "1000")
    monkeypatch.setenv("DEMO_TOKENS_TOTAL_DAILY", "5000")
    monkeypatch.setenv("AUTH_PASSWORD", "unpublished-random-secret-for-this-test")
    config._settings = None
    demo_quota.reset()
    demo_visitors.reset()
    yield
    config._settings = None
    demo_quota.reset()
    demo_visitors.reset()


@pytest.fixture
def not_a_demo(monkeypatch, db_isolated):
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)
    config._settings = None
    demo_quota.reset()
    demo_visitors.reset()
    yield
    config._settings = None


def _client(cookie: str | None = None, forwarded: str = "", host: str = "172.18.0.1"):
    from backend.main import app

    headers = dict(_XHR)
    if forwarded:
        headers["X-Forwarded-For"] = forwarded
    cookies = {demo_visitors.COOKIE: cookie} if cookie else None
    return TestClient(app, client=(host, 9999), headers=headers, cookies=cookies)


def _conversation(session_id: str, title: str, owner: str | None) -> None:
    SessionStore().upsert_meta(session_id, {"title": title, "message_count": 1})
    if owner:
        demo_visitors.claim(session_id, f"c:{owner}")


# ── conversations are private to their visitor ────────────────────────────────────


def test_a_visitor_lists_only_their_own_conversations(demo):
    _conversation("s-alice", "Alice's question about her mortgage", _ALICE)
    _conversation("s-bob", "Bob's question about his bonus", _BOB)

    alice = {row["id"] for row in _client(_ALICE).get("/api/sessions").json()}
    bob = {row["id"] for row in _client(_BOB).get("/api/sessions").json()}
    assert "s-alice" in alice and "s-bob" not in alice
    assert "s-bob" in bob and "s-alice" not in bob


def test_a_visitor_cannot_read_touch_or_watch_anothers_conversation(demo):
    _conversation("s-alice", "Alice's", _ALICE)
    bob = _client(_BOB)
    for method, path in (
        ("GET", "/api/sessions/s-alice/messages"),
        ("GET", "/api/sessions/s-alice/tool-results/tu_1"),
        ("GET", "/api/sessions/s-alice/live"),
        ("GET", "/api/sessions/s-alice/permission-mode"),
        ("PUT", "/api/sessions/s-alice/permission-mode"),
        ("PATCH", "/api/sessions/s-alice"),
        ("DELETE", "/api/chat/s-alice"),
        ("POST", "/api/chat/s-alice/stop"),
        ("POST", "/api/chat/s-alice/resolve"),
    ):
        response = bob.request(
            method, path, json={"mode": "skip", "tool_use_id": "x", "decision": "approve"}
        )
        assert response.status_code == 404, f"{method} {path} -> {response.status_code}"
    assert SessionStore().get_meta("s-alice") is not None, "Bob's delete must not have landed"
    assert _client(_ALICE).get("/api/sessions/s-alice/messages").status_code == 200


def test_posting_into_anothers_conversation_is_refused_before_a_turn_starts(demo, monkeypatch):
    import backend.api.chat as chat_api

    _conversation("s-alice", "Alice's", _ALICE)
    started: list[str] = []
    monkeypatch.setattr(
        chat_api.assistant, "get_or_create_session", lambda sid: started.append(sid)
    )
    body = {"session_id": "s-alice", "messages": [{"role": "user", "content": "hi"}]}

    assert _client(_BOB).post("/api/chat", json=body).status_code == 404
    assert started == []


def test_an_orphan_from_before_a_restart_belongs_to_nobody(demo):
    _conversation("s-orphan", "left over from yesterday", None)
    assert "s-orphan" not in {row["id"] for row in _client(_ALICE).get("/api/sessions").json()}
    assert _client(_ALICE).get("/api/sessions/s-orphan/messages").status_code == 404
    assert _client(_ALICE).delete("/api/chat/s-orphan").status_code == 404


def test_a_fresh_conversation_is_claimed_by_the_visitors_first_write(demo):
    alice, bob = _client(_ALICE), _client(_BOB)
    assert alice.get("/api/sessions/s-new/messages").status_code == 200
    assert alice.patch("/api/sessions/s-new", json={"title": "Mine"}).status_code == 200
    assert demo_visitors.owner_of("s-new") == f"c:{_ALICE}"
    assert bob.get("/api/sessions/s-new/messages").status_code == 404


def test_a_visitors_own_conversation_is_never_read_only(demo):
    _conversation("s-mine", "Mine", _ALICE)
    rows = {row["id"]: row for row in _client(_ALICE).get("/api/sessions").json()}
    assert "read_only" not in rows["s-mine"]


def test_an_automation_runs_conversation_is_listed_read_only(demo, monkeypatch):
    _conversation("s-run", "Weekly review", None)
    monkeypatch.setattr(demo_visitors, "_is_automation_run", lambda sid: sid == "s-run")
    SessionStore().upsert_meta("s-run", {"source": "automation"})

    bob = _client(_BOB)
    rows = {row["id"]: row for row in bob.get("/api/sessions").json()}
    assert rows["s-run"]["read_only"] is True
    assert bob.get("/api/sessions/s-run/messages").status_code == 200
    assert bob.patch("/api/sessions/s-run", json={"title": "mine now"}).status_code == 404


def test_the_unread_marker_and_live_list_skip_other_visitors(demo):
    _conversation("s-alice", "Alice's", _ALICE)
    bob = _client(_BOB)
    assert bob.post("/api/sessions/read", json={"ids": ["s-alice"]}).json() == {"last_read_at": {}}
    live = bob.get("/api/sessions/live").json()
    assert "s-alice" not in live["session_ids"] and "s-alice" not in live["titled"]


def test_a_client_that_discards_cookies_is_a_new_visitor_every_time_not_its_neighbour(demo):
    """Refusing the identity cookie costs a visitor their history; it never lends it to anyone,
    including another visitor behind the same address."""
    first = _client(forwarded="203.0.113.7")
    assert first.patch("/api/sessions/s-nocookie", json={"title": "x"}).status_code == 200
    assert demo_visitors.owner_of("s-nocookie").startswith("c:")
    forgetful = _client(forwarded="203.0.113.7")
    assert forgetful.get("/api/sessions/s-nocookie/messages").status_code == 404


def test_the_first_request_is_handed_an_identity_cookie_and_owns_with_it(demo):
    client = TestClient(__import__("backend.main", fromlist=["app"]).app, headers=_XHR)
    first = client.patch("/api/sessions/s-first", json={"title": "x"})
    cookie = first.cookies.get(demo_visitors.COOKIE)
    assert demo_visitors.valid_cookie(cookie)
    assert "httponly" in first.headers["set-cookie"].lower()
    assert demo_visitors.owner_of("s-first") == f"c:{cookie}"

    again = client.get("/api/sessions/s-first/messages")
    assert again.status_code == 200
    assert "set-cookie" not in {k.lower() for k in again.headers}


def test_a_forged_cookie_value_is_replaced_not_trusted(demo):
    response = _client("not-hex").get("/api/portfolio")
    assert demo_visitors.valid_cookie(response.cookies.get(demo_visitors.COOKIE))


# ── every deliberate model action is metered against the trusted address ─────────

_METERED_PATHS = (
    "/api/chat/s1/resolve",
    "/api/chat/suggestions",
    "/api/capture",
    "/api/accounts/describe",
    "/api/matches/judge",
    "/api/instruments/enrich-classification",
    "/api/instruments/enrich-sectors",
    "/api/intel/assets/ast_1/refresh",
)


def test_the_metered_list_is_fully_exercised():
    for path in _METERED_PATHS:
        assert demo_visitor.metered_session("POST", path)[0], path
    for _verb, pattern in demo_visitor._METERED:
        assert any(pattern.match(path) for path in _METERED_PATHS), pattern.pattern


@pytest.mark.parametrize("path", _METERED_PATHS)
def test_a_throttled_visitor_is_refused_before_the_handler_runs(demo, path):
    real = "203.0.113.7"
    for _ in range(2):
        demo_quota.record_turn(real)
    assert not demo_quota.check(real).allowed

    # Their own address, and the same address behind a rotating (client-written) left side.
    for forwarded in (real, f"1.1.1.1, {real}", f"2.2.2.2, 3.3.3.3, {real}"):
        response = _client(_ALICE, forwarded=forwarded).post(path, json={})
        assert response.status_code == 429, (path, forwarded)
        assert int(response.headers["Retry-After"]) > 0


def test_each_metered_action_costs_the_visitor_a_turn(demo):
    ip = "203.0.113.9"
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/capture",
        "headers": [(b"x-forwarded-for", f"8.8.8.8, {ip}".encode())],
        "query_string": b"",
        "client": ("172.18.0.1", 1),
    }
    demo_visitor.guard(Request(scope))
    demo_visitor.guard(Request(scope))
    with pytest.raises(Exception) as refused:
        demo_visitor.guard(Request(scope))
    assert refused.value.status_code == 429


def test_reads_that_may_narrate_are_not_turns(demo):
    assert not demo_visitor.metered_session("GET", "/api/intel/digest")[0]
    assert not demo_visitor.metered_session("GET", "/api/briefing")[0]
    assert not demo_visitor.metered_session("POST", "/api/chat")[0], "chat meters itself inline"


# ── every model call is checked where its provider is built ──────────────────────


def test_a_provider_is_never_built_past_the_instance_ceiling(demo):
    from backend.ai import assistant

    demo_quota.note_usage("any-session", 5_000)
    with pytest.raises(ConfigurationError, match="daily AI budget"):
        assistant._provider_from_resolved(transport="anthropic", base_url="", api_key="k")


def test_a_provider_is_never_built_for_a_visitor_past_their_own_budget(demo):
    from backend.ai import assistant

    demo_quota.bind_visitor("203.0.113.7")
    demo_quota.note_usage("", 1_000)
    with pytest.raises(ConfigurationError, match="allowance for one visitor"):
        assistant._provider_from_resolved(transport="anthropic", base_url="", api_key="k")

    demo_quota.bind_visitor("198.51.100.1")
    assistant._provider_from_resolved(transport="anthropic", base_url="", api_key="k")


def test_a_call_with_no_session_is_charged_to_the_request_that_caused_it(demo):
    demo_quota.bind_visitor("203.0.113.7")
    demo_quota.note_usage("", 400)
    demo_quota.note_usage("session-nobody-bound", 100)
    assert demo_quota.tokens_used("203.0.113.7") == 500
    assert demo_quota.tokens_used_total() == 500


def test_an_unattributed_call_counts_toward_the_ceiling_only(demo):
    demo_quota.note_usage("", 700)
    assert demo_quota.tokens_used_total() == 700
    assert demo_quota.tokens_used("203.0.113.7") == 0


def test_the_bind_dependency_reaches_the_route_that_spends(demo):
    """A context variable set in a worker thread would not: the dependency must be async."""
    import inspect

    assert inspect.iscoroutinefunction(demo_visitor.bind_visitor)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/briefing",
        "headers": [(b"x-forwarded-for", b"1.1.1.1, 203.0.113.7")],
        "query_string": b"",
        "client": ("172.18.0.1", 1),
    }
    asyncio.run(demo_visitor.bind_visitor(Request(scope)))
    assert demo_quota._current_visitor.get() is None, "asyncio.run copies the context"

    async def within_one_task():
        await demo_visitor.bind_visitor(Request(scope))
        return demo_quota._current_visitor.get()

    assert asyncio.run(within_one_task()) == "203.0.113.7"


# ── Lucius's SQL cannot read the other visitors' conversations ──────────────────


@pytest.mark.parametrize("table", ["session_messages", "pending_plans", "tool_call_log"])
def test_a_demos_sql_cannot_read_conversation_tables(demo, table):
    from backend.core.errors import ValidationError
    from backend.db import sql_console, sql_introspect

    with pytest.raises(ValidationError, match="not readable"):
        sql_console.execute_readonly(f"SELECT * FROM {table}")
    assert table not in sql_introspect.readable_tables()
    with pytest.raises(ValidationError):
        sql_introspect.describe_table(table)


def test_a_demos_sql_still_reads_the_book(demo):
    from backend.db import sql_console

    assert sql_console.execute_readonly("SELECT count(*) AS n FROM accounts")["rows"]


def test_off_a_demo_sql_reads_what_it_always_could(not_a_demo):
    from backend.db import sql_console, sql_introspect

    sql_console.execute_readonly("SELECT count(*) FROM session_messages")
    assert "session_messages" in sql_introspect.readable_tables()


# ── nothing runs unattended ──────────────────────────────────────────────────────


def test_scheduled_automations_run_on_a_demo_to_show_what_they_do(demo, monkeypatch):
    # The demo shows Automations working; their spend is bounded by the instance token ceiling
    # (unattributed usage) and the model pin, not by switching them off.
    import backend.scheduler as scheduler
    from backend.services.automations import runs

    ran: list[str] = []

    async def spy(*_a, **_k):
        ran.append("due")
        return {}

    monkeypatch.setattr(runs, "run_due_automations", spy)
    asyncio.run(scheduler._run_automations())
    assert ran == ["due"]


def test_demo_memory_is_read_but_never_written(demo):
    from backend.ai import memory
    from backend.db.memory_settings_store import MemorySettingsStore, memory_writes_enabled

    assert MemorySettingsStore().enabled() is True, "the seeded facts still reach the prompt"
    assert memory_writes_enabled() is False
    with pytest.raises(memory.MemoryDisabledError):
        memory.add_explicit("Bob earns 400k and lives at 1 Example Street", section="other")
    assert asyncio.run(memory.extract_and_merge([{"role": "user", "content": "secret"}])) is None


# ── nothing changes off a demo ───────────────────────────────────────────────────


def test_off_a_demo_every_conversation_is_visible_and_no_cookie_is_issued(not_a_demo):
    _conversation("s-any", "anyone's", None)
    client = _client(host="127.0.0.1")
    assert "s-any" in {row["id"] for row in client.get("/api/sessions").json()}
    assert client.get("/api/sessions/s-any/messages").status_code == 200
    response = client.get("/api/portfolio")
    assert "set-cookie" not in {k.lower() for k in response.headers}


def test_off_a_demo_the_quota_never_throttles_or_refuses_a_provider(not_a_demo):
    from backend.ai import assistant

    ip = "203.0.113.7"
    for _ in range(100):
        demo_quota.record_turn(ip, "s")
    demo_quota.note_usage("s", 10**9)
    assert demo_quota.check(ip).allowed
    demo_quota.assert_model_available()
    assistant._provider_from_resolved(transport="anthropic", base_url="", api_key="k")
    assert demo_visitors.may_access("whatever", "c:x", write=True)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/capture",
        "headers": [],
        "query_string": b"",
        "client": (ip, 1),
    }
    for _ in range(50):
        assert demo_visitor.guard(Request(scope)) is None


def test_off_a_demo_scheduled_automations_and_memory_work_as_before(not_a_demo, monkeypatch):
    import backend.scheduler as scheduler
    from backend.db.memory_settings_store import memory_writes_enabled
    from backend.services.automations import runs

    ran: list[str] = []

    async def spy(*_a, **_k):
        ran.append("due")
        return {}

    monkeypatch.setattr(runs, "run_due_automations", spy)
    asyncio.run(scheduler._run_automations())
    assert ran == ["due"]
    assert memory_writes_enabled() is True

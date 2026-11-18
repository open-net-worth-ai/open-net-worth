"""A sent message keeps the id its client gave it — the server's half of chat sync.

The client names every message it sends (``client_message_id``). The server stores that id
on the transcript entry and renders it back (``clientId``), so a client can tell EXACTLY
whether the server holds a message it sent — never by matching text, which the server
rewrites (mentions, screen context, an injection fence). A resend of a message the server
already holds is acknowledged as a duplicate and never runs twice: that is what makes a
client's outbox safe to retry after a phone drops the response.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.pending_injection as pending_injection
import backend.ai.turns as turns
import backend.api.chat as chat_mod
import backend.services.profile as profile_service
from backend.api.chat import ChatRequest
from backend.db.session_store import SessionStore
from tests._asgi import make_request


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, db_isolated):
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(profile_service, "read_profile", lambda: {"chat_multi_turn_enabled": True})
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)
    yield
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()


def _forget(sid: str) -> None:
    assistant.SESSIONS.pop(sid, None)
    assistant._SESSION_LOCKS.pop(sid, None)


def test_rehydrate_names_each_user_message_by_its_client_id(db_isolated):
    sid = "cid-rehydrate"
    store = SessionStore()
    store.upsert_meta(sid, {"title": "t"})
    store.set_transcript(
        sid,
        [
            {"role": "user", "content": "first", assistant.CLIENT_ID_KEY: "c-1"},
            {"role": "assistant", "content": [{"type": "text", "text": "answer"}]},
            {"role": "user", "content": [{"type": "text", "text": "second"}]},
        ],
    )

    users = [m for m in assistant.rehydrate_messages(sid) or [] if m["role"] == "user"]

    assert users[0]["clientId"] == "c-1"
    assert "clientId" not in users[1], "a message stored without one never gains one"


def test_an_injected_message_is_stored_under_its_client_id(monkeypatch):
    sid = "cid-inject"
    monkeypatch.setattr(assistant, "save_session_durable", lambda *_a, **_k: None)
    history: list[dict] = [{"role": "user", "content": "opening"}]
    pending_injection.enqueue(sid, "c-queued", "and one more thing")

    assistant._drain_injected_messages(sid, history, "feedface")

    assert history[-1][assistant.CLIENT_ID_KEY] == "c-queued"
    assert assistant.client_ids_in(history) == {"c-queued"}


async def test_a_fresh_turn_stores_its_message_under_the_client_id(monkeypatch):
    captured: list[list[dict]] = []

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        captured.append(list(new_messages))
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)
    turns.set_inline_for_tests(True)
    sid = "cid-fresh"
    _forget(sid)
    try:
        req = ChatRequest(
            session_id=sid,
            messages=[{"role": "user", "content": "hello"}],
            client_message_id="c-fresh",
        )
        resp = await chat_mod.chat(req, make_request())
    finally:
        _forget(sid)

    assert json.loads(bytes(resp.body))["accepted"] == "started"
    assert captured[0][-1][assistant.CLIENT_ID_KEY] == "c-fresh"


async def test_a_resend_of_a_stored_message_is_a_duplicate_and_starts_nothing(monkeypatch):
    calls: list[str] = []

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        calls.append(session_id)
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)
    sid = "cid-dup-stored"
    _forget(sid)
    store = SessionStore()
    store.upsert_meta(sid, {"title": "t"})
    store.set_transcript(
        sid, [{"role": "user", "content": "hi", assistant.CLIENT_ID_KEY: "c-sent"}]
    )
    try:
        req = ChatRequest(
            session_id=sid,
            messages=[{"role": "user", "content": "hi"}],
            client_message_id="c-sent",
        )
        resp = await chat_mod.chat(req, make_request())
    finally:
        _forget(sid)

    assert resp.status_code == 202
    assert json.loads(bytes(resp.body))["accepted"] == "duplicate"
    assert calls == []


async def test_a_resend_of_a_still_queued_message_is_not_queued_twice(monkeypatch):
    reached = asyncio.Event()
    proceed = asyncio.Event()

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        reached.set()
        await proceed.wait()
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)
    sid = "cid-dup-queued"
    _forget(sid)
    try:
        await chat_mod.chat(
            ChatRequest(session_id=sid, messages=[{"role": "user", "content": "go"}]),
            make_request(),
        )
        await asyncio.wait_for(reached.wait(), timeout=1)
        follow_up = ChatRequest(
            session_id=sid,
            messages=[{"role": "user", "content": "also this"}],
            client_message_id="c-follow",
        )
        first = await chat_mod.chat(follow_up, make_request())
        again = await chat_mod.chat(follow_up, make_request())

        assert json.loads(bytes(first.body))["accepted"] == "queued"
        assert json.loads(bytes(again.body))["accepted"] == "duplicate"
        assert [m.client_id for m in pending_injection.drain(sid)] == ["c-follow"]
    finally:
        proceed.set()
        assert await turns.adrain_for_tests(sid, timeout=1)
        _forget(sid)


def test_the_transcript_says_whether_a_turn_is_still_writing_it(db_isolated):
    sid = "cid-live-flag"
    store = SessionStore()
    store.upsert_meta(sid, {"title": "t"})
    store.set_transcript(sid, [{"role": "user", "content": "hi"}])
    app = FastAPI()
    app.include_router(chat_mod.router)
    client = TestClient(app)

    assert client.get(f"/api/sessions/{sid}/messages").json()["live"] is False
    live_sessions.begin(sid, "turn-1")
    try:
        assert client.get(f"/api/sessions/{sid}/messages").json()["live"] is True
    finally:
        live_sessions.end(sid, "turn-1")


async def test_a_resend_of_a_message_stranded_in_the_queue_starts_its_turn(monkeypatch):
    """The turn it was queued behind ended without draining it. The finished transcript does
    not name it, so the client shows it failed; Retry must answer it once — not be waved off
    as a duplicate, and not ride in twice (as residue AND as the new message)."""
    captured: list[tuple[list[dict], list]] = []

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        recovered = list(kw.get("recovered_injections") or [])
        captured.append((list(new_messages), recovered))
        kw["on_recovered_adopted"](recovered)
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)
    turns.set_inline_for_tests(True)
    sid = "cid-stranded"
    _forget(sid)
    pending_injection.enqueue(sid, "c-stranded", "what are the sign-up offers?")
    try:
        req = ChatRequest(
            session_id=sid,
            messages=[{"role": "user", "content": "what are the sign-up offers?"}],
            client_message_id="c-stranded",
        )
        resp = await chat_mod.chat(req, make_request())
    finally:
        _forget(sid)

    assert json.loads(bytes(resp.body))["accepted"] == "started"
    new_messages, recovered = captured[0]
    assert recovered == []
    assert new_messages[-1][assistant.CLIENT_ID_KEY] == "c-stranded"
    assert pending_injection.has_pending(sid) is False


async def test_recovered_residue_is_stored_under_its_client_id(monkeypatch):
    class _Answers:
        async def stream_chat(self, *a, **kw):
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "stop_reason": "end_turn"}

    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _Answers())
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_k: "SYS")
    sid = "cid-residue"
    _forget(sid)
    pending_injection.enqueue(sid, "c-residue", "left over from a crashed turn")
    claim = pending_injection.claim(sid)
    try:
        async for _ev in assistant.run(
            sid,
            [{"role": "user", "content": "hello again"}],
            "s",
            recovered_injections=claim.messages,
            on_recovered_adopted=lambda _adopted: pending_injection.ack(claim),
        ):
            pass
    finally:
        _forget(sid)

    users = [m for m in assistant.rehydrate_messages(sid) or [] if m["role"] == "user"]
    residue = [m for m in users if m["text"] == "left over from a crashed turn"]
    assert residue and residue[0]["clientId"] == "c-residue"

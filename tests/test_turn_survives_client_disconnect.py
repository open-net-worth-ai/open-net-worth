"""D1/D2 (concurrent-conversations RFC, acceptance
``docs/qa/acceptance/concurrent-conversations.md``): a turn is server-owned, not
request-owned. ``POST /api/chat`` validates, spawns the turn as a background task
(``backend.ai.turns``), and returns ``202 {session_id, turn_id}`` immediately — the browser
tab that asked for it can close, and the turn must still finish and durably persist its
result. ``GET /api/sessions/{id}/live`` is only ever a WATCHER, never the driver (Phase 3).

D1 — a turn survives the client: dropping/never-attaching a watcher must not affect the
turn at all (``turns._drive`` pumps its OWN internal consumer of the event source;
``live_sessions.watch()`` is a completely separate fan-out reader of the replay/publish
side, decoupled from the turn's own execution — this file proves that decoupling holds by
never fully draining a watcher and showing the turn finishes anyway).

D2 — re-attaching after the fact: a client that opens (or reopens) ``GET /live`` once the
turn has already ended must see the REPLAY of the turn so far (the whole thing, since it is
over), not join a live stream mid-sentence and not see a partial/truncated tail.

Hermetic: no network, no LLM — drives ``assistant.run`` for REAL (not a stand-in for
``assistant.run`` itself, unlike ``tests/test_chat_live_stream.py``) against a scripted fake
LLM provider, so ``_save_session`` actually persists to disk and ``rehydrate_messages``
proves durability from the transcript file, not from anything held in memory. Provider-stub
pattern matches ``tests/test_concurrent_turn_ownership.py``'s ``_TextOnlyProvider``.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.turns as turns
import backend.api.chat as chat_mod
from backend.api.chat import ChatRequest
from tests._asgi import make_request

# sessions / pending_plans are DB-backed now (Wave 5E, JSON->DB) and isolated per test by
# conftest's autouse Tier-2 table clear — no per-store monkeypatch needed any more.


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")
    # Bypasses the discovery/offer gate — irrelevant to what this file pins (matches
    # tests/test_concurrent_turn_ownership.py's _isolate).
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))

    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(False)
    yield
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()
    assistant.SESSIONS.clear()
    assistant._SESSION_LOCKS.clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(chat_mod.router)
    return TestClient(app)


def _parse_events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            pass
    return events


class _OneShotProvider:
    """A real (fake-LLM) provider so assistant.run's own persistence path executes for
    real — text only, one round, matching test_concurrent_turn_ownership.py's
    _TextOnlyProvider but with a distinguishable answer to assert on."""

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        yield {"type": "text_delta", "text": "The answer is 42."}
        yield {
            "type": "usage",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": 0.001,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        }
        yield {"type": "done", "stop_reason": "end_turn"}


# ── D1: the turn survives the client ─────────────────────────────────────────────────────


async def test_turn_runs_to_completion_when_the_client_never_attaches(monkeypatch):
    """The strongest form of "the client dropped the connection": no watcher EVER attaches
    to GET /live at all. The turn must still run to completion and its answer must be
    durably persisted in the transcript — proving the turn is not merely a value inside an
    SSE stream nobody read."""
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _OneShotProvider())

    sid = "test-d1-no-watcher-ever"
    req = ChatRequest(session_id=sid, messages=[{"role": "user", "content": "what is it?"}])
    response = await chat_mod.chat(req, make_request())
    assert response.status_code == 202
    body = json.loads(bytes(response.body))
    assert body["turn_id"].startswith("turn_")

    # No GET /live call happens here at all — that IS the test.

    assert await turns.adrain_for_tests(sid, timeout=2), "the turn never finished"

    rows = {row["id"]: row for row in assistant.list_sessions()}
    assert rows[sid]["last_turn_status"] == "ok"

    # Durable: read back from the STORED transcript (rehydrate_messages loads fresh from
    # the DB-backed sessions row, not from the in-memory SESSIONS dict), proving the result
    # was actually written, not merely computed and discarded.
    messages = assistant.rehydrate_messages(sid)
    assert messages, "expected a persisted transcript"
    assistant_texts = [m["text"] for m in messages if m.get("role") == "assistant"]
    assert any("42" in t for t in assistant_texts), assistant_texts


async def test_turn_runs_to_completion_after_a_watcher_attaches_then_is_abandoned(monkeypatch):
    """A slightly softer form of D1: a watcher DOES attach (as a real client would via GET
    /live), reads one event, and is then abandoned mid-stream without ever reaching
    stream_end or calling aclose() — exactly what a closed TCP connection looks like from
    the server's side once FastAPI garbage-collects the abandoned generator. The turn
    itself (backend.ai.turns' task, driven by its OWN internal _pump()) must be completely
    unaffected, because live_sessions.watch() is a separate fan-out reader, never the
    driver."""
    reached = asyncio.Event()
    proceed = asyncio.Event()

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        yield {"type": "text_delta", "text": "first "}
        reached.set()
        await proceed.wait()
        yield {"type": "text_delta", "text": "second"}
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)

    sid = "test-d1-abandoned-watcher"
    req = ChatRequest(session_id=sid, messages=[{"role": "user", "content": "hi"}])
    response = await chat_mod.chat(req, make_request())
    assert response.status_code == 202

    await asyncio.wait_for(reached.wait(), timeout=1)

    # A watcher attaches and reads exactly ONE event, then is abandoned — never iterated
    # again, never explicitly closed.
    watcher = live_sessions.watch(sid)
    first_seen = await watcher.__anext__()
    assert first_seen.get("type") in ("turn_started", "text_delta")
    del watcher  # simulate the dropped connection — no further reads, no aclose()

    # The turn is still running, decoupled from the abandoned watcher.
    assert turns.executing_turn_id(sid) is not None
    proceed.set()

    assert await turns.adrain_for_tests(sid, timeout=1), (
        "the turn stalled or was cancelled after its only watcher was abandoned — a turn "
        "must never depend on a client staying attached"
    )
    rows = {row["id"]: row for row in assistant.list_sessions()}
    assert rows[sid]["last_turn_status"] == "ok"


# ── D2: re-attaching after the turn ended replays it whole, not mid-sentence ─────────────


async def test_reattaching_after_the_turn_ends_replays_the_whole_turn(monkeypatch):
    """A client that opens GET /live only AFTER the turn has already finished (the
    strongest form of "was never attached during the turn") must see the full replay —
    every event the turn produced, in order — not silently miss the beginning or join a
    live stream partway through (there is nothing live left to join)."""
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _OneShotProvider())

    sid = "test-d2-reattach-after-end"
    req = ChatRequest(session_id=sid, messages=[{"role": "user", "content": "what is it?"}])
    response = await chat_mod.chat(req, make_request())
    assert response.status_code == 202

    assert await turns.adrain_for_tests(sid, timeout=2)
    assert not live_sessions.is_live(sid), "the turn must have actually ended before re-attach"

    c = _client()
    r = c.get(f"/api/sessions/{sid}/live")
    assert r.status_code == 200
    events = _parse_events(r.text)

    assert any(e.get("type") == "turn_started" for e in events), events
    assert any(e.get("type") == "turn_done" for e in events), events
    full_text = "".join(e.get("text", "") for e in events if e.get("type") == "text_delta")
    assert full_text == "The answer is 42.", (
        f"expected the FULL turn text on re-attach, got a partial/mismatched replay: {full_text!r}"
    )

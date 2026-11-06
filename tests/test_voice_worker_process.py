"""The voice worker runs in a process of its own (ADR 2026-11-03-voice-calls-are-a-fast-front-
voice-over-lucius): a crash in LiveKit's native layer once killed the whole app mid-call.

These pin the two halves of that: the app answers the worker only the allow-listed operations,
and only on a conversation it handed out for a call; and a worker that dies — however it dies —
is reported and restarted, never taking the app with it.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading

import pytest

from backend.connectors.voice import desk, host, service
from backend.connectors.voice.app_link import AppError, AppLink
from backend.connectors.voice.events import DeskEvent


@pytest.fixture
def linked(monkeypatch):
    """Both ends of the link in this process: the app's ``Host`` on a loop of its own, and the
    worker's ``AppLink``, joined by two pipes."""
    app_loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=app_loop.run_forever, daemon=True)
    loop_thread.start()
    to_app_r, to_app_w = (os.fdopen(fd, mode) for fd, mode in zip(os.pipe(), ("rb", "wb")))
    to_worker_r, to_worker_w = (os.fdopen(fd, mode) for fd, mode in zip(os.pipe(), ("rb", "wb")))
    app = host.Host(to_worker_w, app_loop)

    def serve():
        import json

        for line in to_app_r:
            app.handle(json.loads(line))

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    gone = threading.Event()
    link = AppLink(to_worker_r, to_app_w, on_stop=lambda: None, on_app_gone=gone.set)
    monkeypatch.setattr(
        service, "claim_call", lambda room: "sess-call" if room in ("r1", "r2") else None
    )
    allowed = {"on": True}
    monkeypatch.setattr(service, "calls_allowed", lambda: allowed["on"])

    async def ask(session_id, said, *, understood="", shown=False, unheard=False):
        yield DeskEvent("text", f"answer to {said}")
        yield DeskEvent("end")

    monkeypatch.setattr(desk, "ask", ask)
    link.allowed = allowed
    yield link
    # Close both pipes so each reader sees the end and returns: no thread outlives the test.
    to_app_w.close()
    to_worker_w.close()
    server.join(5)
    assert gone.wait(5)
    app_loop.call_soon_threadsafe(app_loop.stop)
    loop_thread.join(5)
    app_loop.close()


def test_the_worker_reaches_lucius_only_on_a_conversation_the_app_handed_it(linked):
    async def run():
        assert await linked.call("claim_call", room="nope") is None
        with pytest.raises(AppError):  # not claimed yet: no call owns it
            [e async for e in linked.stream("ask", session_id="sess-call", said="hi")]
        assert await linked.call("claim_call", room="r1") == "sess-call"
        events = [e async for e in linked.stream("ask", session_id="sess-call", said="hi")]
        assert [e.kind for e in events] == ["text", "end"] and events[0].text == "answer to hi"
        with pytest.raises(AppError):  # another conversation stays out of reach
            [e async for e in linked.stream("ask", session_id="sess-other", said="hi")]
        with pytest.raises(AppError):  # and nothing outside the allow-list is served
            await linked.call("delete_account", account="x")

    asyncio.run(run())


def test_the_app_holds_the_limits_itself_not_the_worker(linked, monkeypatch):
    """Security review, 2026-11-03: the worker is the part assumed compromised, so the APP
    caps requests, re-checks that calls are on, picks Lucius's model and forgets a call."""

    async def ask():
        return [e async for e in linked.stream("ask", session_id="sess-call", said="hi")]

    async def run():
        assert await linked.call("claim_call", room="r1") == "sess-call"
        await ask()
        for _ in range(host.MAX_ASKS_PER_MINUTE - 1):
            await ask()
        with pytest.raises(AppError):  # past the per-minute cap
            await ask()
        linked.allowed["on"] = False
        with pytest.raises(AppError):  # voice calls switched off mid-call
            [e async for e in linked.stream("follow_next_turn", session_id="sess-call")]
        linked.allowed["on"] = True
        await linked.call("end_call", session_id="sess-call")
        with pytest.raises(AppError):  # the call is over: its conversation is out of reach
            await linked.call("record_exchange", session_id="sess-call", said="", reply="x")

    asyncio.run(run())


def test_one_call_ending_never_shuts_out_the_next_call_in_the_same_conversation(linked):
    """Owner, 2026-11-04: a second call in a conversation went silent on every request, because
    the first call's late end_call forgot the conversation the second one was using."""

    async def ask():
        return [e async for e in linked.stream("ask", session_id="sess-call", said="hi")]

    async def run():
        assert await linked.call("claim_call", room="r1") == "sess-call"
        assert await linked.call("claim_call", room="r2") == "sess-call"
        await linked.call("end_call", session_id="sess-call", room="r1")
        assert [e.kind for e in await ask()] == ["text", "end"]  # r2 is still on
        await linked.call("end_call", session_id="sess-call", room="r2")
        with pytest.raises(AppError):  # every call over: out of reach again
            await ask()

    asyncio.run(run())


def test_a_malformed_line_from_the_worker_is_ignored_not_fatal():
    """One bad line ended the reader, so the worker blocked and was never restarted."""
    handled: list = []
    worker = host.WorkerProcess(asyncio.new_event_loop(), lambda _c: None)
    fake = type("H", (), {"handle": lambda self, m: handled.append(m)})()
    for msg in ([1, 2], 7, {"id": [1], "op": "call"}, {"op": "ready"}, {"id": 3, "op": "call"}):
        worker._take(fake, msg)
    assert handled == [{"id": 3, "op": "call"}] and worker._ready.is_set()


def _worker(app_loop, code: str, exits: list):
    return host.WorkerProcess(app_loop, exits.append, argv=(sys.executable, "-c", code))


READY = 'import sys; sys.stdout.write(\'{"op":"ready"}\\n\'); sys.stdout.flush()\n'


def test_a_worker_that_crashes_is_reported_and_the_app_lives_on():
    """The 2026-11-03 crash, reproduced: a segfault in native code, in the worker only."""
    exits: list = []
    done = threading.Event()
    worker = host.WorkerProcess(
        asyncio.new_event_loop(),
        lambda code: (exits.append(code), done.set()),
        argv=(sys.executable, "-c", READY + "import ctypes; ctypes.string_at(0)"),
    )
    worker.start({"config": {}}, timeout=20)
    assert done.wait(20), "the crash was never reported"
    assert exits and exits[0] != 0 and not worker.alive()


def test_stopping_a_worker_ends_it_without_calling_it_a_crash():
    exits: list = []
    code = READY + "import sys\nfor line in sys.stdin:\n    if b'stop' in line.encode(): break\n"
    worker = _worker(asyncio.new_event_loop(), code, exits)
    assert worker.start({"config": {}}, timeout=20)
    worker.stop(timeout=10)
    assert not worker.alive() and exits == []


def test_a_worker_that_ignores_stop_is_killed():
    exits: list = []
    worker = _worker(asyncio.new_event_loop(), READY + "import time; time.sleep(60)", exits)
    assert worker.start({"config": {}}, timeout=20)
    worker.stop(timeout=1)
    assert not worker.alive() and exits == []

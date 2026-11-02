"""B3 (concurrent-conversations RFC, acceptance ``docs/qa/acceptance/concurrent-conversations.md``):
three turns on three DIFFERENT sessions run concurrently, with no cap and nothing queuing on
count. The acceptance doc's verification record notes only two were ever driven end to end
("B3 (three conversations at once) — only two were driven; not proven") — this file proves
three, at the API level (``POST /api/chat`` -> ``backend.ai.turns``), the layer B1/B2/B4 were
already proven at via ``make qa-e2e`` (``CC-02``).

Hermetic: no network, no LLM — three independent scripted ``assistant.run`` fakes, released
in an order deliberately different from spawn order, so completion can only be explained by
genuine concurrent scheduling (not a hidden serialization where turn N+1 cannot start until
turn N finishes). Pattern matches ``tests/test_chat_live_stream.py``'s
``test_a_second_client_attaches_mid_turn_and_receives_replay`` (spawn via the real route
function, control interleaving with ``asyncio.Event``s).
"""

from __future__ import annotations

import asyncio
import json

import pytest

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
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(False)
    yield
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()


async def test_three_turns_on_three_sessions_run_concurrently_and_all_complete():
    """Spawns three turns (A, B, C) on three distinct sessions, each of which parks on its
    own ``asyncio.Event`` after emitting one delta. Asserts:

    1. All three reach their parked point and are registered SIMULTANEOUSLY — the turn
       registry holds three live entries at once, proving no cap and no serialization.
    2. Releasing them in reverse spawn order (C, then B, then A) still lets every one
       finish and land its own result — order of completion is independent of spawn
       order, which could only happen if the three are genuinely concurrent tasks rather
       than one queue drained one at a time.
    3. Each conversation's own transcript holds ONLY its own answer — no cross-talk.
    """
    reached = {sid: asyncio.Event() for sid in ("s-tri-a", "s-tri-b", "s-tri-c")}
    release = {sid: asyncio.Event() for sid in ("s-tri-a", "s-tri-b", "s-tri-c")}
    answers = {"s-tri-a": "answer-A", "s-tri-b": "answer-B", "s-tri-c": "answer-C"}
    turn_ids: dict[str, str] = {}

    # assistant.run is resolved once per call inside chat.py's closure (`assistant.run(...)`
    # at call time) — a single dispatching stand-in that routes on session_id lets all
    # three turns run their own script under ONE monkeypatch (turns.py, not assistant.run,
    # is what multiplexes sessions, so this is a faithful test of the concurrent shape).
    async def dispatching_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        sid = session_id
        reached[sid].set()
        await release[sid].wait()
        yield {"type": "text_delta", "text": answers[sid]}
        yield {"type": "turn_done", "usage": {}}

    import backend.ai.assistant as assistant_mod

    orig_run = assistant_mod.run
    assistant_mod.run = dispatching_run
    try:
        for sid in ("s-tri-a", "s-tri-b", "s-tri-c"):
            req = ChatRequest(session_id=sid, messages=[{"role": "user", "content": "hi"}])
            response = await chat_mod.chat(req, make_request())
            assert response.status_code == 202
            turn_ids[sid] = json.loads(bytes(response.body))["turn_id"]

        # All three reached their parked point — proves they were scheduled concurrently,
        # not one after another (a serialized dispatcher would have only the FIRST reached).
        # Each turn parks until released, so a serialized one never gets all three here however
        # long we wait; the slack is for the first provider build's cold SDK import (>1s).
        await asyncio.wait_for(
            asyncio.gather(*(reached[sid].wait() for sid in reached)), timeout=10
        )

        # The turn registry holds all three live entries SIMULTANEOUSLY — no cap, nothing
        # queuing on count.
        assert set(turns._by_session.keys()) == {"s-tri-a", "s-tri-b", "s-tri-c"}
        assert len(turns._turns) == 3
        for sid, tid in turn_ids.items():
            assert turns.executing_turn_id(sid) == tid
        assert set(live_sessions.live_session_ids()) == {"s-tri-a", "s-tri-b", "s-tri-c"}

        # Release in the REVERSE of spawn order — completion order tracks release order,
        # not spawn order, which is only possible if the three tasks are truly independent.
        for sid in ("s-tri-c", "s-tri-b", "s-tri-a"):
            release[sid].set()
            assert await turns.adrain_for_tests(sid, timeout=1), f"{sid} never completed"
    finally:
        assistant_mod.run = orig_run

    # All three finished with no cap/queuing residue.
    assert turns._turns == {}
    assert turns._by_session == {}
    assert live_sessions.live_session_ids() == []

    # No cross-talk: each session's own transcript/replay holds only its own answer.
    for sid, answer in answers.items():
        replay = list(live_sessions._replay.get(sid) or ())
        texts = [e.get("text") for e in replay if e.get("type") == "text_delta"]
        assert texts == [answer], f"{sid} replay was {texts!r}, expected only [{answer!r}]"
        for other_sid, other_answer in answers.items():
            if other_sid == sid:
                continue
            assert other_answer not in texts, f"{other_answer} leaked into {sid}'s replay"

    rows = {row["id"]: row for row in assistant.list_sessions()}
    for sid in answers:
        assert rows[sid]["last_turn_status"] == "ok"

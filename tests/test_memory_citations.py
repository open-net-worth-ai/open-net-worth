"""Usage citations: the model ends a reply with ``<memory-used>m7a88f8 m3c01d2</memory-used>``
naming the remembered facts that shaped it.

The tag is machine syntax and must never reach a reader — not in the streamed deltas, not in the
saved transcript — and a handle that names no fact, or more than one, must never mark a fact.
Only a CITED fact is marked used; being injected into the prompt counts for nothing.
Synthetic facts only.
"""

from __future__ import annotations

import json
import random
import sys

import pytest

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.models.claude_cli_provider as ccp
import backend.ai.pending_injection as pending_injection
import backend.ai.tools  # noqa: F401 — fills the registry the turn reads
import backend.ai.turns as turns
from backend.ai.memory_citations import (
    CitationStripper,
    note_cited_facts,
    split_citations,
)
from backend.ai.memory_render import fact_handle
from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.memory_store import MemoryStore

ANSWER = "Your lease renews in March, so a May move would mean paying a break fee."
CITED = f"{ANSWER}\n\n<memory-used>m7a88f8 m3c01d2</memory-used>"


def _fact(fid: str, content: str, **extra) -> dict:
    return {
        "id": fid,
        "content": content,
        "source": "explicit",
        "state": "live",
        "section": "money",
        "topic": "Housing",
        "expires_at": None,
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "last_used_at": None,
        **extra,
    }


@pytest.fixture
def facts(db_isolated):
    store = MemoryStore()
    store.bulk_insert(
        [
            _fact("mem_7a88f842d02f", "Lease renews in March"),
            _fact("mem_3c01d2aa90b1", "Landlord is Sam"),
            _fact("mem_9ff0aa11bb22", "Keeps a spare key with a neighbour"),
        ]
    )
    return store


def _used(store: MemoryStore) -> dict[str, str | None]:
    return {f["id"]: f.get("last_used_at") for f in store.all_facts()}


# ── the stripper, over every way a stream can be cut ─────────────────────────────────


def _stream(raw: str, chunk: int) -> tuple[str, list[str]]:
    s = CitationStripper()
    out = "".join(s.feed(raw[i : i + chunk]) for i in range(0, len(raw), chunk))
    return out + s.flush(), s.handles


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 8, 13, 1000])
def test_the_tag_is_hidden_however_the_stream_is_cut(chunk):
    shown, handles = _stream(CITED, chunk)
    assert "memory-used" not in shown and "<" not in shown
    assert shown.rstrip() == ANSWER
    assert handles == ["m7a88f8", "m3c01d2"]


def test_a_tag_split_across_deltas_is_stripped():
    s = CitationStripper()
    parts = [
        s.feed("Done.\n\n<memory-"),
        s.feed("used>m3a"),
        s.feed("bcde</memory-"),
        s.feed("used>"),
    ]
    assert "".join(parts) + s.flush() == "Done.\n\n"
    assert s.handles == ["m3abcde"]


@pytest.mark.parametrize(
    "reply",
    [
        "Yes, 3 < 5 and 7 > 2.",
        "Use <b>bold</b> sparingly.",
        "The ratio is a<b, which is odd.",
        "Ends on an angle bracket <",
        "Ends on a near miss <memory-",
        "Ends on <memory-used",
        "A tag <memory-usedx>y</memory-usedx> that is not the tag.",
    ],
)
@pytest.mark.parametrize("chunk", [1, 4, 1000])
def test_text_that_is_not_the_tag_is_shown_untouched(reply, chunk):
    shown, handles = _stream(reply, chunk)
    assert shown == reply
    assert handles == []


def test_a_tag_the_reply_never_closes_is_dropped_not_shown():
    shown, handles = _stream("Answer.\n<memory-used>m7a88f8 m3", 4)
    assert shown == "Answer.\n"
    assert handles == []


def test_text_after_the_tag_still_streams():
    shown, handles = _stream("A. <memory-used>m7a88f8</memory-used> B.", 3)
    assert shown == "A.  B." and handles == ["m7a88f8"]


def test_the_tag_is_found_where_it_is_never_split_mid_character_class():
    rng = random.Random(3)
    for _ in range(50):
        shown, handles = _stream(CITED, rng.randint(1, 9))
        assert shown.rstrip() == ANSWER and handles == ["m7a88f8", "m3c01d2"]


def test_split_citations_strips_the_saved_text_and_returns_the_handles():
    assert split_citations(CITED) == (ANSWER, ["m7a88f8", "m3c01d2"])
    assert split_citations("No tag here.") == ("No tag here.", [])
    assert split_citations("Cut off <memory-used>m7a8") == ("Cut off", [])
    assert split_citations("<memory-used>m7a88f8</memory-used>") == ("", ["m7a88f8"])


# ── resolving a handle: prefix against LIVE facts, one match or nothing ───────────────


def test_cited_facts_get_last_used_and_tag_is_hidden_from_the_store_side(facts):
    assert fact_handle("mem_7a88f842d02f") == "m7a88f8"

    note_cited_facts(["m7a88f8", "m3c01d2"])

    used = _used(facts)
    assert used["mem_7a88f842d02f"] and used["mem_3c01d2aa90b1"]
    assert used["mem_9ff0aa11bb22"] is None


def test_a_handle_that_matches_nothing_or_two_facts_marks_nothing(facts):
    facts.insert(_fact("mem_7a88f8ffffff", "Twin of the first handle"))

    note_cited_facts(["m7a88f8", "mdeadbe", "m", "m7", "x7a88f8", ""])

    assert set(_used(facts).values()) == {None}


def test_a_short_prefix_that_is_unambiguous_resolves(facts):
    note_cited_facts(["m9ff"])
    assert _used(facts)["mem_9ff0aa11bb22"]


def test_a_retired_or_unsorted_fact_is_never_marked(facts):
    facts.insert(_fact("mem_aa00bb00cc00", "Old landlord", state="retired"))
    facts.insert({**_fact("mem_bb11cc11dd11", "Legacy"), "state": "unsorted", "section": None})

    note_cited_facts(["maa00bb", "mbb11cc"])

    assert set(_used(facts).values()) == {None}


def test_citing_does_nothing_on_a_demo(facts, monkeypatch):
    monkeypatch.setattr("backend.ai.memory_citations.memory_writes_enabled", lambda: False)
    note_cited_facts(["m7a88f8"])
    assert set(_used(facts).values()) == {None}


def test_citing_when_memory_is_off_is_harmless(facts):
    MemorySettingsStore().set_enabled(False)
    note_cited_facts(["m7a88f8"])
    assert set(_used(facts).values()) == {None}


# ── end to end through assistant.run ─────────────────────────────────────────────────


def _delta(text: str) -> dict:
    return {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    }


def _cli_round(raw: str, *, chunk: int = 6) -> list[dict]:
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "duration_ms": 1,
        "num_turns": 1,
        "total_cost_usd": 0.0,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    return [
        {"type": "system", "subtype": "init", "apiKeySource": "none", "tools": []},
        *(_delta(raw[i : i + chunk]) for i in range(0, len(raw), chunk)),
        result,
    ]


@pytest.fixture
def _turn(monkeypatch, facts):
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(ccp.claude_live_session, "sessions_enabled", lambda: False)
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)
    yield
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()


async def _run(sid: str, provider) -> tuple[list[dict], list[dict]]:
    monkeypatched = assistant
    try:
        events = [
            ev
            async for ev in monkeypatched.run(
                sid, [{"role": "user", "content": "should I move in May?"}], "s"
            )
        ]
    finally:
        saved = assistant.get_or_create_session(sid)[1]
        assistant.SESSIONS.pop(sid, None)
        assistant._SESSION_LOCKS.pop(sid, None)
    return events, saved


def _shown(events: list[dict]) -> str:
    return "".join(ev["text"] for ev in events if ev["type"] == "text_delta")


async def test_cli_transport_hides_the_tag_saves_clean_text_and_marks_the_cited(
    monkeypatch, _turn, facts
):
    provider = ccp.ClaudeCliProvider(cli_path=sys.executable)

    async def _events(*_a, **_k):
        for ev in _cli_round(CITED):
            yield ev

    monkeypatch.setattr(provider, "_events", _events)
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)

    events, saved = await _run("test-citation-cli", provider)

    assert _shown(events).strip() == ANSWER
    assert "memory-used" not in json.dumps(events) and "memory-used" not in json.dumps(saved)
    assert saved[-1]["content"] == [{"type": "text", "text": ANSWER}]
    used = _used(facts)
    assert used["mem_7a88f842d02f"] and used["mem_3c01d2aa90b1"]
    assert used["mem_9ff0aa11bb22"] is None


class _ApiProvider:
    """The Anthropic-shaped seam: a provider whose ``stream_chat`` yields text deltas and ``done``."""

    def __init__(self, deltas: list[str]):
        self.deltas = deltas

    async def stream_chat(self, *_a, **_k):
        for d in self.deltas:
            yield {"type": "text_delta", "text": d}
        yield {"type": "done", "stop_reason": "end_turn"}


@pytest.mark.parametrize("chunk", [1, 4, 1000])
async def test_api_transport_hides_the_tag_across_deltas(monkeypatch, _turn, facts, chunk):
    deltas = [CITED[i : i + chunk] for i in range(0, len(CITED), chunk)]
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _ApiProvider(deltas))

    events, saved = await _run(f"test-citation-api-{chunk}", None)

    assert _shown(events).strip() == ANSWER
    assert "memory-used" not in json.dumps(events) and "memory-used" not in json.dumps(saved)
    assert saved[-1]["content"] == [{"type": "text", "text": ANSWER}]
    assert _used(facts)["mem_7a88f842d02f"]


async def test_a_reply_with_a_literal_angle_bracket_is_shown_whole(monkeypatch, _turn, facts):
    reply = "If rent < 30% of income and the lease is <12 months, you are fine."
    deltas = [reply[i : i + 3] for i in range(0, len(reply), 3)]
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _ApiProvider(deltas))

    events, saved = await _run("test-citation-literal", None)

    assert _shown(events) == reply
    assert saved[-1]["content"] == [{"type": "text", "text": reply}]
    assert set(_used(facts).values()) == {None}


async def test_uncited_injected_facts_are_not_marked_used(monkeypatch, _turn, facts):
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _ApiProvider([ANSWER]))

    events, _ = await _run("test-citation-none", None)

    assert _shown(events) == ANSWER
    assert set(_used(facts).values()) == {None}

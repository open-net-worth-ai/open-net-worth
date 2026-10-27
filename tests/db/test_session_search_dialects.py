"""Past-conversation search is one statement over two dialects: it must find the same things on
SQLite and on Postgres (the CI ``db-postgres`` job runs this file with ONW_TEST_DATABASE_URL set).

Covers what differs between the engines: the JSON block extraction, Unicode case folding and NFC
normalisation, the model-context exclusions, and the per-conversation de-duplication.
"""

from __future__ import annotations

import pytest

from backend.db.engine import get_engine
from backend.db.session_search import search_message_text
from backend.db.session_store import SessionStore


def _chat(sid: str, *messages: dict) -> None:
    store = SessionStore()
    store.upsert_meta(
        sid, {"id": sid, "title": sid.title(), "updated_at": "2026-10-01T00:00:00+00:00"}
    )
    store.append_messages(sid, list(messages))


def _find(needle: str, **kw) -> list[dict]:
    return search_message_text(get_engine(), needle, limit=kw.pop("limit", 10), **kw)


@pytest.fixture
def book(db_isolated):
    _chat("chat-a", {"role": "user", "content": "Chuyến đi đà lạt tháng sau"})
    _chat(
        "chat-b",
        {"role": "assistant", "content": [{"type": "text", "text": "Plan for DA LAT noted"}]},
        {"role": "user", "content": [{"type": "text", "text": "Plan again"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Plan three"}]},
    )
    _chat(
        "chat-c",
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t", "name": "x", "input": {"q": "lemur"}}],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": "lemur rows"},
                {"type": "text", "text": "<turn_context>lemur</turn_context>"},
                {"type": "text", "text": "[Attached file: a.txt | ref_id: x]\n\nlemur"},
                {"type": "text", "text": "the lemur question"},
            ],
        },
    )
    _chat("scheduled-0123abcd", {"role": "assistant", "content": "lemur digest"})
    _chat(
        "chat-d",
        {
            "role": "user",
            "content": "[SKILL INVOCATION: s (s)]\nlemur steps",
            "_onw_skill": {"skill_id": "s"},
        },
    )


def test_unicode_case_and_normalisation_match_on_both_dialects(book):
    assert [h["session_id"] for h in _find("Đà Lạt")] == ["chat-a"]
    assert [h["session_id"] for h in _find("đà lạt")] == ["chat-a"]
    assert {h["session_id"] for h in _find("da lat")} == {"chat-b"}


def test_only_text_that_was_said_is_a_candidate(book):
    hits = _find("lemur", hidden_ids=("scheduled",), hidden_prefixes=("scheduled-",))

    assert [h["session_id"] for h in hits] == ["chat-c"]
    assert "the lemur question" in hits[0]["excerpt"]


def test_one_hit_per_conversation_newest_first_and_the_current_one_excluded(book):
    hits = _find("plan")
    assert [h["session_id"] for h in hits] == ["chat-b"]
    assert "Plan three" in hits[0]["excerpt"]

    assert _find("plan", exclude_session_id="chat-b") == []
    assert len(_find("lemur", limit=1)) == 1


def test_a_capture_sessions_user_turn_is_skipped_but_its_reply_is_found(db_isolated):
    _chat(
        "capture-1a2b3c4d",
        {"role": "user", "content": "template mentions the pelican clause"},
        {"role": "assistant", "content": [{"type": "text", "text": "Booked the flat white"}]},
    )
    SessionStore().upsert_meta("capture-1a2b3c4d", {"origin": "capture"})

    assert _find("pelican") == []
    assert [h["session_id"] for h in _find("flat white")] == ["capture-1a2b3c4d"]

"""A capture's conversation is kept, titled, read, and tagged as background.

It used to be deleted the moment the turn ended — and a memory-extraction task finishing
afterwards re-created it as an empty "New conversation" in the history list, one per
Shortcut / Apple Pay capture. Now the conversation stays (the record of what came in and
what was booked) and the list hides it by its ``origin``.
"""

from __future__ import annotations

import pytest

from backend.ai import capture as capture_mod
from backend.db.session_store import SessionStore


@pytest.fixture
def fake_turn(monkeypatch):
    seen: dict = {}

    async def _fake_run(*, session_id, new_messages, system, model, surface):
        seen["session_id"] = session_id
        from backend.ai.assistant import upsert_session_meta

        # What a real turn leaves behind: a finished-turn stamp the list reads as "unread".
        upsert_session_meta(session_id, message_count=2, last_turn_status="ok")
        yield {"type": "text_delta", "text": "Booked $12.40 at Blue Bottle."}

    monkeypatch.setattr("backend.ai.assistant.run", _fake_run)
    return seen


@pytest.mark.asyncio
async def test_capture_keeps_a_titled_read_background_conversation(db_isolated, fake_turn):
    await capture_mod.run_capture("Citi: $12.40 at Blue Bottle", source="shortcut")

    meta = SessionStore().get_meta(fake_turn["session_id"])
    assert meta is not None, "the capture conversation is kept"
    assert meta["origin"] == "capture"
    assert meta["title"] == "Capture · Citi: $12.40 at Blue Bottle"
    assert meta["last_read_at"] >= meta["last_turn_at"], "never shows as a new reply"


@pytest.mark.asyncio
async def test_capture_conversation_is_listed_with_its_origin(db_isolated, fake_turn):
    from backend.ai.assistant import list_sessions

    await capture_mod.run_capture("coffee 4.50")
    rows = {r["id"]: r for r in list_sessions()}
    assert rows[fake_turn["session_id"]]["origin"] == "capture"


def test_capture_title_is_one_clipped_line():
    title = capture_mod.capture_title("Chase:\n  you spent " + "x" * 200)
    assert "\n" not in title
    assert title.startswith("Capture · Chase: you spent ")
    assert len(title) <= len("Capture · ") + 60
    assert capture_mod.capture_title("   ") == "Capture"

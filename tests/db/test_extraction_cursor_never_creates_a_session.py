"""Saving the memory-extraction cursor never brings a deleted conversation back.

Extraction runs as a background task that can outlive its session: a capture turn deletes
its throwaway ``capture-…`` session as soon as the turn ends, then extraction finished and
upserted the cursor — re-inserting an empty "New conversation" into the history list once
per Shortcut / Apple Pay capture.
"""

from __future__ import annotations

import backend.ai.memory as memory
from backend.db.session_store import SessionStore


def test_cursor_on_a_missing_session_writes_nothing(db_isolated):
    s = SessionStore()
    assert s.set_extraction_cursor("capture-deadbeef", {"last_extracted_len": 8}) is False
    assert s.get_meta("capture-deadbeef") is None
    assert all(m.get("id") != "capture-deadbeef" for m in s.all_meta())


def test_memory_save_cursor_after_delete_does_not_resurrect(db_isolated):
    s = SessionStore()
    s.upsert_meta("capture-0001", {"title": "t"})
    s.delete("capture-0001")
    memory._save_cursor("capture-0001", 8)
    assert s.get_meta("capture-0001") is None


def test_cursor_on_a_live_session_is_saved(db_isolated):
    s = SessionStore()
    s.upsert_meta("sess-live", {"title": "t"})
    assert s.set_extraction_cursor("sess-live", {"last_extracted_len": 8}) is True
    assert s.get_extraction_cursor("sess-live") == {"last_extracted_len": 8}
    assert s.get_meta("sess-live")["title"] == "t"

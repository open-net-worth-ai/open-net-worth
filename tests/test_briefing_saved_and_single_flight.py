"""The briefing card shows the last note at once, and only one rewrite runs at a time.

Every open showed "generating" for several seconds even when the saved note still held: the
freshness check fingerprints the whole book first. `/api/briefing/saved` returns the note
straight from its file. And two loads at once (two cards, a reload mid-write) each started a
model call for the same note (seen at 22:13, 2026-11-19); now the second waits for the first.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import backend.api.briefing as brf
from backend.main import app


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "briefing_cache.json"
    monkeypatch.setattr(brf, "CACHE_FILE", path)
    monkeypatch.setattr(brf, "_inflight", None)
    return path


def test_the_saved_note_comes_back_without_any_freshness_check(cache, monkeypatch):
    cache.write_text(
        json.dumps(
            {
                "note": "ACME is up.",
                "generated_at": "2020-01-01T08:00:00",
                "book_fingerprint": "old",
                "activity_fingerprint": "old",
            }
        )
    )

    def _boom():
        raise AssertionError("the saved note must not fingerprint the book")

    monkeypatch.setattr(brf, "_current_fingerprint", _boom)
    body = TestClient(app).get("/api/briefing/saved").json()
    assert body["note"] == "ACME is up."
    assert body["is_cached"] is True
    assert "book_fingerprint" not in body and "activity_fingerprint" not in body


@pytest.mark.parametrize("content", [None, "not json", json.dumps({"generated_at": "x"})])
def test_no_saved_note_says_so(cache, content):
    if content is not None:
        cache.write_text(content)
    assert TestClient(app).get("/api/briefing/saved").json() == {"note": None}


def test_two_loads_at_once_share_one_rewrite(cache, monkeypatch):
    monkeypatch.setattr(brf, "_current_fingerprint", lambda: "fp")
    monkeypatch.setattr(brf, "_current_activity", lambda: "act")
    calls = 0

    async def _generate(total_nw):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return {"note": f"note {calls}", "actions": [], "chat_suggestion": ""}

    monkeypatch.setattr(brf, "_generate_briefing", _generate)

    async def _both():
        return await asyncio.gather(
            brf.get_briefing(refresh=False, current_nw=1.0),
            brf.get_briefing(refresh=False, current_nw=1.0),
        )

    first, second = asyncio.run(_both())
    assert calls == 1
    assert first["note"] == second["note"] == "note 1"

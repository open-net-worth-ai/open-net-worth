"""The briefing card shows the last note at once, from ONE endpoint, and only one check or
rewrite runs at a time.

Every open showed "generating" for several seconds even when the saved note still held: the
freshness check fingerprints the whole book (~350 queries). `GET /api/briefing?wait=false`
answers from the saved file and runs that check behind the answer, at most once a minute
(the database changes every minute from price polls, so no cheap change counter can stand in
for it). Two loads at once (two cards, a reload mid-write) each started a model call for the
same note (seen at 22:13, 2026-11-19); now the second waits for the first.
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
    monkeypatch.setattr(brf, "_last_check", None)
    return path


def _write_saved(cache, note="ACME is up."):
    cache.write_text(
        json.dumps(
            {
                "note": note,
                "generated_at": "2020-01-01T08:00:00",
                "book_fingerprint": "old",
                "activity_fingerprint": "old",
            }
        )
    )


def test_a_quick_open_answers_with_the_saved_note_and_checks_behind_it(cache, monkeypatch):
    _write_saved(cache)
    started = []

    def _fake_start(current_nw, refresh):
        started.append(refresh)

    monkeypatch.setattr(brf, "_start", _fake_start)
    body = TestClient(app).get("/api/briefing?wait=false").json()
    assert body["note"] == "ACME is up."
    assert body["is_cached"] is True and body["checking"] is True
    assert "book_fingerprint" not in body and "activity_fingerprint" not in body
    assert started == [False]


def test_a_quick_open_right_after_a_check_starts_nothing(cache, monkeypatch):
    _write_saved(cache)
    monkeypatch.setattr(brf, "_last_check", brf.time.monotonic())

    def _boom(*a, **kw):
        raise AssertionError("checked under a minute ago: no new check")

    monkeypatch.setattr(brf, "_start", _boom)
    body = TestClient(app).get("/api/briefing?wait=false").json()
    assert body["note"] == "ACME is up." and body["checking"] is False


def test_the_follow_up_joins_the_check_the_quick_open_started(cache, monkeypatch):
    """The card's second call (wait=true) waits on the SAME check — the book is fingerprinted
    once per open, and a book that changed gets exactly one rewrite."""
    _write_saved(cache, note="old note")
    fingerprints = 0
    rewrites = 0

    def _fp():
        nonlocal fingerprints
        fingerprints += 1
        return "new"

    async def _generate(total_nw):
        nonlocal rewrites
        rewrites += 1
        await asyncio.sleep(0.05)
        return {"note": "new note", "actions": [], "chat_suggestion": ""}

    monkeypatch.setattr(brf, "_current_fingerprint", _fp)
    monkeypatch.setattr(brf, "_current_activity", lambda: "act")
    monkeypatch.setattr(brf, "_generate_briefing", _generate)

    async def _open():
        quick = await brf.get_briefing(refresh=False, current_nw=1.0, wait=False)
        full = await brf.get_briefing(refresh=False, current_nw=1.0, wait=True)
        return quick, full

    quick, full = asyncio.run(_open())
    assert quick["note"] == "old note" and quick["checking"] is True
    assert full["note"] == "new note" and full["is_cached"] is False
    assert fingerprints == 1 and rewrites == 1


@pytest.mark.parametrize("content", [None, "not json", json.dumps({"generated_at": "x"})])
def test_no_saved_note_waits_for_a_real_one(cache, monkeypatch, content):
    if content is not None:
        cache.write_text(content)
    monkeypatch.setattr(brf, "_current_fingerprint", lambda: "fp")
    monkeypatch.setattr(brf, "_current_activity", lambda: "act")

    async def _generate(total_nw):
        return {"note": "fresh", "actions": [], "chat_suggestion": ""}

    monkeypatch.setattr(brf, "_generate_briefing", _generate)
    body = asyncio.run(brf.get_briefing(refresh=False, current_nw=1.0, wait=False))
    assert body["note"] == "fresh" and body["is_cached"] is False
    assert "checking" not in body


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
            brf.get_briefing(refresh=False, current_nw=1.0, wait=True),
            brf.get_briefing(refresh=False, current_nw=1.0, wait=True),
        )

    first, second = asyncio.run(_both())
    assert calls == 1
    assert first["note"] == second["note"] == "note 1"

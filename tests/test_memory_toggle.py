"""Tests for Feature A: global memory toggle and incremental extraction cursor.

Coverage:
1. MemorySettingsStore — CRUD, default-True when absent
2. extract_and_merge — no-op when toggle OFF (assert LLM not called)
3. (the idle sweep and tidy pass are no-ops when OFF — tests/test_memory_idle.py, test_memory_tidy.py)
4. remove_fact — no-op when toggle OFF
5. _fmt_memory — returns "" when toggle OFF even with facts present
6. memory_enabled_for — effective-AND truth table (global × per-agent)
7. remember_fact / forget_fact tool handlers — ok=False + message when global OFF, success when ON
8. REST GET/PUT /api/memory/settings — shape + round-trip
9. Incremental cursor: advances on success, only-new-turns content
10. Incremental backfill cap: first-run capped to EXTRACT_BACKFILL_CAP
11. Cursor reset on truncated history
12. _incremental_slice — returns [] when no new messages; extract_and_merge skips LLM call
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient

import backend.ai.memory as memory_mod
import backend.db.memory_settings_store as settings_store_mod
import backend.db.memory_store as memory_store_mod

# ── Shared helpers ────────────────────────────────────────────────────────────


def _fact(content: str = "a fact", tier: str = "durable", fid: str | None = None) -> dict:
    return {
        "id": fid or str(uuid.uuid4()),
        "content": content,
        "state": "live",
        "tier": tier,
        "source": "implicit",
        "expires_at": None,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


def _history(n_turns: int = 3) -> list[dict]:
    msgs = []
    for i in range(n_turns):
        msgs.append({"role": "user", "content": f"User message {i}"})
        msgs.append({"role": "assistant", "content": f"Assistant reply {i}"})
    return msgs


@pytest.fixture
def isolated(tmp_path, db_isolated):
    return tmp_path


# ── 1. MemorySettingsStore CRUD ───────────────────────────────────────────────


def test_settings_default_true_when_absent(isolated):
    store = settings_store_mod.MemorySettingsStore()
    assert store.enabled() is True


def test_settings_set_false(isolated):
    store = settings_store_mod.MemorySettingsStore()
    store.set_enabled(False)
    assert store.enabled() is False


def test_settings_set_true_roundtrip(isolated):
    store = settings_store_mod.MemorySettingsStore()
    store.set_enabled(False)
    store.set_enabled(True)
    assert store.enabled() is True


def test_settings_persists_across_instances(isolated):
    settings_store_mod.MemorySettingsStore().set_enabled(False)
    assert settings_store_mod.MemorySettingsStore().enabled() is False


def test_settings_set_enabled_returns_dict(isolated):
    result = settings_store_mod.MemorySettingsStore().set_enabled(False)
    assert result == {"enabled": False}


# ── 2. extract_and_merge gated by toggle ──────────────────────────────────────


def test_extract_noop_when_toggle_off(isolated, monkeypatch):
    settings_store_mod.MemorySettingsStore().set_enabled(False)
    called = {"n": 0}

    class _FakeProv:
        async def complete(self, *a, **kw):
            called["n"] += 1
            return '{"new": [], "updates": [], "remove": []}'

    monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProv())
    asyncio.run(memory_mod.extract_and_merge(_history()))
    assert called["n"] == 0, "provider.complete must NOT be called when toggle is OFF"


def test_extract_runs_when_toggle_on(isolated, monkeypatch):
    settings_store_mod.MemorySettingsStore().set_enabled(True)
    called = {"n": 0}

    class _FakeProv:
        async def complete(self, *a, **kw):
            called["n"] += 1
            return '{"new": [], "updates": [], "remove": []}'

    monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProv())
    asyncio.run(memory_mod.extract_and_merge(_history()))
    assert called["n"] == 1


# ── 4. remove_fact gated by toggle ───────────────────────────────────────────


def test_remove_fact_raises_when_toggle_off(isolated, monkeypatch):
    settings_store_mod.MemorySettingsStore().set_enabled(False)

    store = memory_store_mod.MemoryStore()
    store.replace_all([_fact("risk tolerance is moderate", fid="target")])

    called = {"n": 0}

    class _FakeProv:
        async def complete(self, *a, **kw):
            called["n"] += 1
            return '{"id": "target"}'

    monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProv())
    with pytest.raises(memory_mod.MemoryDisabledError):
        asyncio.run(memory_mod.remove_fact("risk tolerance"))
    assert called["n"] == 0, "LLM must NOT be called when memory is OFF"


# ── 5. _fmt_memory returns "" when toggle OFF ─────────────────────────────────


def test_fmt_memory_empty_when_toggle_off(isolated):
    settings_store_mod.MemorySettingsStore().set_enabled(False)

    store = memory_store_mod.MemoryStore()
    store.replace_all([_fact("Important user preference")])

    from backend.ai.prompts import _fmt_memory

    result = _fmt_memory()
    assert result == "", "must return empty string when memory toggle is OFF"


def test_fmt_memory_non_empty_when_toggle_on(isolated):
    settings_store_mod.MemorySettingsStore().set_enabled(True)

    store = memory_store_mod.MemoryStore()
    store.replace_all([_fact("Important user preference")])

    from backend.ai.prompts import _fmt_memory

    result = _fmt_memory()
    assert "Important user preference" in result


# ── 6. memory_enabled_for truth table ────────────────────────────────────────
# Tests call settings_store_mod.memory_enabled_for directly — that's where
# the canonical AND-gate lives (db layer, importable by both services and ai).


def test_memory_enabled_for_both_on(isolated):
    settings_store_mod.MemorySettingsStore().set_enabled(True)
    assert settings_store_mod.memory_enabled_for({"memory_enabled": True}) is True


def test_memory_enabled_for_global_off(isolated):
    settings_store_mod.MemorySettingsStore().set_enabled(False)
    assert settings_store_mod.memory_enabled_for({"memory_enabled": True}) is False


def test_memory_enabled_for_agent_off(isolated):
    settings_store_mod.MemorySettingsStore().set_enabled(True)
    assert settings_store_mod.memory_enabled_for({"memory_enabled": False}) is False


def test_memory_enabled_for_both_off(isolated):
    settings_store_mod.MemorySettingsStore().set_enabled(False)
    assert settings_store_mod.memory_enabled_for({"memory_enabled": False}) is False


def test_memory_enabled_for_no_agent(isolated):
    settings_store_mod.MemorySettingsStore().set_enabled(True)
    assert settings_store_mod.memory_enabled_for(None) is True


def test_memory_enabled_for_no_agent_global_off(isolated):
    settings_store_mod.MemorySettingsStore().set_enabled(False)
    assert settings_store_mod.memory_enabled_for(None) is False


def test_memory_enabled_for_agent_default_true(isolated):
    """Agent dict with no memory_enabled key → defaults to True."""
    settings_store_mod.MemorySettingsStore().set_enabled(True)
    assert settings_store_mod.memory_enabled_for({}) is True


# ── 7. REST API shape ──────────────────────────────────────────────────────────


@pytest.fixture
def api_env(monkeypatch, db_isolated):
    """Isolate REST API tests to a tmp data dir."""
    import backend.core.config as cfg

    monkeypatch.setenv("AUTH_DISABLED", "true")
    cfg._settings = None
    yield
    cfg._settings = None


def _client():
    from backend.main import app

    return TestClient(app, raise_server_exceptions=True)


def test_get_memory_settings_default(api_env):
    resp = _client().get("/api/memory/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert "enabled" in body
    assert body["enabled"] is True


def test_put_memory_settings_false(api_env):
    resp = _client().put("/api/memory/settings", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_put_memory_settings_true(api_env):
    c = _client()
    c.put("/api/memory/settings", json={"enabled": False})
    resp = c.put("/api/memory/settings", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def test_get_reflects_put(api_env):
    c = _client()
    c.put("/api/memory/settings", json={"enabled": False})
    resp = c.get("/api/memory/settings")
    assert resp.json()["enabled"] is False


def test_put_missing_field_422(api_env):
    resp = _client().put("/api/memory/settings", json={})
    assert resp.status_code == 422


# ── 8. Incremental cursor advances on success ─────────────────────────────────


def test_cursor_advances_after_extraction(isolated, monkeypatch, tmp_path):
    settings_store_mod.MemorySettingsStore().set_enabled(True)

    class _FakeProv:
        async def complete(self, *a, **kw):
            return '{"new": [], "updates": [], "remove": []}'

    monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProv())
    monkeypatch.setattr(memory_mod, "SESSIONS_DIR", tmp_path, raising=False)
    # sessions is DB-backed now (Wave 5F, JSON->DB) — the extraction cursor is a column
    # on the session row (SessionStore.get_extraction_cursor/set_extraction_cursor), no
    # more per-session _cursor_file to isolate.

    from backend.db.session_store import SessionStore

    # The cursor rides an EXISTING session row; it never creates one.
    SessionStore().upsert_meta("test-sess", {"title": "t"})
    history = _history(5)  # 10 messages
    asyncio.run(memory_mod.extract_and_merge(history, session_id="test-sess"))

    doc = SessionStore().get_extraction_cursor("test-sess")
    assert doc is not None, "cursor must be written after extraction"
    assert doc["last_extracted_len"] == 10


def test_cursor_only_sends_new_turns(isolated, monkeypatch, tmp_path):
    """Second extraction only sends new messages + the overlap window.

    Specifically: messages before (last_extracted_len - EXTRACT_OVERLAP) must NOT
    appear in the second extraction prompt.
    """
    settings_store_mod.MemorySettingsStore().set_enabled(True)

    prompts_seen: list[str] = []

    class _FakeProv:
        async def complete(self, prompt, **kw):
            prompts_seen.append(prompt)
            return '{"new": [], "updates": [], "remove": []}'

    monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProv())
    # sessions is DB-backed now (Wave 5F, JSON->DB) and isolated per test by conftest's
    # autouse Tier-2 table clear — no per-session _cursor_file to isolate any more.

    sid = "incremental-test"
    from backend.db.session_store import SessionStore

    SessionStore().upsert_meta(sid, {"title": "t"})
    # Build a first history long enough that early messages fall outside the overlap window.
    # EXTRACT_OVERLAP=4, so we need >4 messages before the new ones.
    old_messages = []
    for i in range(10):
        old_messages.append({"role": "user", "content": f"VERY_OLD user {i}"})
        old_messages.append({"role": "assistant", "content": f"VERY_OLD reply {i}"})

    asyncio.run(memory_mod.extract_and_merge(old_messages, session_id=sid))
    prompts_seen.clear()

    # Second extraction: add new messages on top
    history = old_messages + [
        {"role": "user", "content": "BRAND_NEW user message"},
        {"role": "assistant", "content": "BRAND_NEW reply"},
    ]
    asyncio.run(memory_mod.extract_and_merge(history, session_id=sid))

    assert prompts_seen, "provider must be called on second extraction"
    prompt_text = prompts_seen[-1]
    assert "BRAND_NEW user message" in prompt_text, "new turn must appear in extraction prompt"
    # VERY_OLD messages 0–5 are before (20 - EXTRACT_OVERLAP=4) = index 16, so they must not
    # appear (they are well outside the overlap window).
    assert "VERY_OLD user 0" not in prompt_text, (
        "early already-extracted messages must not appear in incremental prompt"
    )


def test_cursor_backfill_capped_on_first_run(isolated, monkeypatch, tmp_path):
    """First run on a long session: only the last EXTRACT_BACKFILL_CAP messages are sent."""
    settings_store_mod.MemorySettingsStore().set_enabled(True)

    prompts_seen: list[str] = []

    class _FakeProv:
        async def complete(self, prompt, **kw):
            prompts_seen.append(prompt)
            return '{"new": [], "updates": [], "remove": []}'

    monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProv())
    # sessions is DB-backed now (Wave 5F, JSON->DB) and isolated per test by conftest's
    # autouse Tier-2 table clear — no per-session _cursor_file to isolate any more.

    # Build a history longer than EXTRACT_BACKFILL_CAP
    n = memory_mod.EXTRACT_BACKFILL_CAP + 50
    history = []
    for i in range(n):
        history.append({"role": "user", "content": f"Ancient user msg {i}"})
        history.append({"role": "assistant", "content": f"Ancient reply {i}"})

    asyncio.run(memory_mod.extract_and_merge(history, session_id="backfill-test"))

    assert prompts_seen, "provider must be called"
    prompt_text = prompts_seen[-1]
    # The very first ancient message must NOT be in the prompt (it's before the cap window)
    assert "Ancient user msg 0" not in prompt_text, (
        "messages before backfill cap must not appear in first-run extraction"
    )


def test_cursor_reset_on_truncated_history(isolated, monkeypatch, tmp_path):
    """If history length < last_extracted_len, cursor is treated as stale (reset path)."""
    settings_store_mod.MemorySettingsStore().set_enabled(True)

    prompts_seen: list[str] = []

    class _FakeProv:
        async def complete(self, prompt, **kw):
            prompts_seen.append(prompt)
            return '{"new": [], "updates": [], "remove": []}'

    monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProv())

    from backend.db.session_store import SessionStore

    sid = "truncate-test"
    # sessions is DB-backed now (Wave 5F, JSON->DB) — seed the extraction cursor directly
    # through the store's column, no more per-session _cursor_file to isolate.
    SessionStore().upsert_meta(sid, {"title": "t"})
    SessionStore().set_extraction_cursor(sid, {"last_extracted_len": 1000})

    # Short history (much less than cursor says)
    history = _history(3)  # 6 messages
    asyncio.run(memory_mod.extract_and_merge(history, session_id=sid))

    # Should have run without error and sent the capped history
    assert prompts_seen, "extraction must still run after cursor reset"
    # Cursor should now be updated to current history length
    doc = SessionStore().get_extraction_cursor(sid)
    assert doc["last_extracted_len"] == len(history)


# ── 7. remember_fact / forget_fact tool handlers gated by toggle ──────────────


def _call_tool(tool_name: str, **inp):
    """Run an AI tool handler via execute_tool; return (parsed_result, proposed_change)."""
    from backend.ai.tools import execute_tool

    text, proposed = asyncio.run(execute_tool(tool_name, inp))
    return json.loads(text), proposed


def test_remember_fact_blocked_when_global_off(isolated):
    """remember_fact tool handler must return ok=False with a message when memory is OFF."""
    settings_store_mod.MemorySettingsStore().set_enabled(False)
    result, proposed = _call_tool(
        "remember_fact", content="User wants to retire at 45", section="plans", topic="Retirement"
    )
    assert result["ok"] is False
    assert "message" in result
    assert proposed is None


def test_remember_fact_succeeds_when_global_on(isolated):
    """remember_fact tool handler must return ok=True and save the fact when memory is ON."""
    settings_store_mod.MemorySettingsStore().set_enabled(True)
    result, proposed = _call_tool(
        "remember_fact", content="User wants to retire at 45", section="plans", topic="Retirement"
    )
    assert result["ok"] is True
    assert "saved" in result
    assert "retire at 45" in result["saved"]
    # Verify fact was actually written to the store.
    store = memory_store_mod.MemoryStore()
    facts = store.all_facts()
    assert any("retire at 45" in f.get("content", "") for f in facts)


def test_forget_fact_blocked_when_global_off(isolated):
    """forget_fact tool handler must return ok=False with a message when memory is OFF."""
    settings_store_mod.MemorySettingsStore().set_enabled(False)
    # Pre-populate a fact that would otherwise match.
    memory_store_mod.MemoryStore().insert(
        {
            "id": "f1",
            "content": "risk tolerance is moderate",
            "tier": "durable",
            "source": "explicit",
            "expires_at": None,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
    )
    result, proposed = _call_tool("forget_fact", description="risk tolerance")
    assert result["ok"] is False
    assert "message" in result
    # Fact must still be present — write path was inert.
    facts = memory_store_mod.MemoryStore().all_facts()
    assert any(f["id"] == "f1" for f in facts)


# ── 12. _incremental_slice returns [] when no new messages ────────────────────


def test_incremental_slice_empty_when_no_new_messages():
    """_incremental_slice must return [] when history length equals the cursor."""
    history = _history(5)  # 10 messages
    result = memory_mod._incremental_slice(history, last_extracted_len=10)
    assert result == [], "_incremental_slice must be empty when cursor == len(history)"


def test_incremental_slice_non_empty_when_new_messages():
    """_incremental_slice returns messages when cursor < len(history)."""
    history = _history(5)  # 10 messages
    result = memory_mod._incremental_slice(history, last_extracted_len=8)
    assert len(result) > 0


def test_extract_noop_when_no_new_messages(isolated, monkeypatch, tmp_path):
    """extract_and_merge must not call the LLM when cursor == len(history)."""
    settings_store_mod.MemorySettingsStore().set_enabled(True)

    called = {"n": 0}

    class _FakeProv:
        async def complete(self, *a, **kw):
            called["n"] += 1
            return '{"new": [], "updates": [], "remove": []}'

    monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProv())

    from backend.db.session_store import SessionStore

    history = _history(5)  # 10 messages
    # Simulate cursor already at len(history) — nothing new to extract. sessions is
    # DB-backed now (Wave 5F, JSON->DB) — seed the extraction cursor directly.
    sid = "no-new-test"
    SessionStore().upsert_meta(sid, {"title": "t"})
    SessionStore().set_extraction_cursor(sid, {"last_extracted_len": len(history)})

    asyncio.run(memory_mod.extract_and_merge(history, session_id=sid))
    assert called["n"] == 0, "LLM must NOT be called when there are no new messages"

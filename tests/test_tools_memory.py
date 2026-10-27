"""Unit tests for backend/ai/tools/memory.py.

Covers the four tool handlers — remember_fact, forget_fact, agent_remember_fact,
agent_forget_fact — with an emphasis on:

  • Global memory toggle gating (OFF → refuse without writing, ON → succeed)
  • Per-agent memory toggle: effective AND of global × agent setting
  • Happy paths with real store writes verified
  • No-active-agent guard for agent-scoped tools
  • Memory disabled for agent guard
  • forget_fact / agent_forget_fact partial-match logic

All tests are isolated to tmp_path via monkeypatching. No LLM or network calls.
forget_fact requires an LLM call (remove_fact is async + calls provider.complete);
we monkeypatch _provider in backend.ai.memory so no real LLM is hit.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta

import pytest

import backend.ai.memory as memory_mod
import backend.db.memory_settings_store as settings_store_mod
import backend.db.memory_store as memory_store_mod
from backend.core.user_time import user_today

# ── helpers ────────────────────────────────────────────────────────────────────


def _call_tool(tool_name: str, **inp):
    """Execute an AI tool by name and return (parsed_result, proposed_change)."""
    from backend.ai.tools import execute_tool

    text, proposed = asyncio.run(execute_tool(tool_name, inp))
    return json.loads(text), proposed


def _make_fact(content: str, fid: str | None = None) -> dict:
    now = "2026-01-01T00:00:00"
    return {
        "id": fid or str(uuid.uuid4()),
        "content": content,
        "tier": "durable",
        "source": "explicit",
        "expires_at": None,
        "created_at": now,
        "updated_at": now,
    }


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def isolated(tmp_path, db_isolated):
    """Redirect shared-memory stores to a fresh tmp directory."""
    return tmp_path


class _FakeProvider:
    def __init__(self, target_id: str = "none"):
        self.target_id = target_id
        self.call_count = 0

    async def complete(self, *a, **kw):
        self.call_count += 1
        return json.dumps({"id": self.target_id})


# ══════════════════════════════════════════════════════════════════════════════
# remember_fact
# ══════════════════════════════════════════════════════════════════════════════


class TestRememberFact:
    def test_blocked_when_global_memory_off(self, isolated):
        """remember_fact must return ok=False with a message when memory is OFF."""
        settings_store_mod.MemorySettingsStore().set_enabled(False)
        result, proposed = _call_tool("remember_fact", content="Has a dog", section="you")
        assert result["ok"] is False
        assert "message" in result
        assert proposed is None

    def test_blocked_does_not_write(self, isolated):
        """When memory is OFF, no fact must be written to the store."""
        settings_store_mod.MemorySettingsStore().set_enabled(False)
        _call_tool("remember_fact", content="Has a dog", section="you")
        assert memory_store_mod.MemoryStore().all_facts() == []

    def test_succeeds_when_global_memory_on(self, isolated):
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        result, proposed = _call_tool(
            "remember_fact", content="Prefers short answers", section="you", topic="Preferences"
        )
        assert result["ok"] is True
        assert "short answers" in result["saved"]
        assert proposed is None

    def test_remember_fact_takes_a_section_and_topic(self, isolated):
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        _call_tool(
            "remember_fact", content="Rents a flat in Tampines", section="money", topic=" housing "
        )
        _call_tool(
            "remember_fact", content="Rent is due on the 1st", section="money", topic="Housing"
        )
        first, second = sorted(
            memory_store_mod.MemoryStore().all_facts(), key=lambda f: f["content"], reverse=True
        )
        assert (first["section"], first["topic"], first["state"], first["source"]) == (
            "money",
            "housing",
            "live",
            "explicit",
        )
        assert second["topic"] == first["topic"]
        assert "tier" not in first and first["expires_at"] is None

    def test_an_open_item_expires_and_carries_no_topic(self, isolated):
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        _call_tool(
            "remember_fact", content="Waiting on the tax letter", section="open_items", topic="Tax"
        )
        (fact,) = memory_store_mod.MemoryStore().all_facts()
        assert fact["topic"] is None
        assert fact["expires_at"] == (user_today() + timedelta(days=14)).isoformat()

    def test_expires_at_is_stored(self, isolated):
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        due = (user_today() + timedelta(days=20)).isoformat()
        _call_tool(
            "remember_fact",
            content="Expects to sell ONWAI after Q3 vest",
            section="plans",
            topic="Equity",
            expires_at=due,
        )
        assert memory_store_mod.MemoryStore().all_facts()[0]["expires_at"] == due

    def test_a_new_sensitive_fact_needs_the_setting_and_is_stored_without_a_flag(self, isolated):
        store = settings_store_mod.MemorySettingsStore()
        store.set_enabled(True)
        result, _ = _call_tool(
            "remember_fact", content="Has asthma", section="you", topic="About you", sensitive=True
        )
        assert result["ok"] is False and "Sensitive" in result["message"]
        assert memory_store_mod.MemoryStore().all_facts() == []

        store.set_sensitive_topics(True)
        result, _ = _call_tool(
            "remember_fact", content="Has asthma", section="you", topic="About you", sensitive=True
        )
        assert result["ok"] is True
        (fact,) = memory_store_mod.MemoryStore().all_facts()
        assert fact["content"] == "Has asthma" and "sensitive" not in fact

    @pytest.mark.parametrize(
        "content, needle",
        [("x" * 281, "280"), ("Card 4111 1111 1111 1111", "last 4")],
    )
    def test_a_refusal_comes_back_as_a_message_and_stores_nothing(self, isolated, content, needle):
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        result, _ = _call_tool("remember_fact", content=content, section="other")
        assert result["ok"] is False and needle in result["message"]
        assert memory_store_mod.MemoryStore().all_facts() == []

    def test_an_unknown_section_is_refused_with_the_choices(self, isolated):
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        result, _ = _call_tool("remember_fact", content="Likes jazz", section="hobbies")
        assert result["ok"] is False and "Pick a section" in result["message"]

    def test_multiple_facts_accumulate(self, isolated):
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        _call_tool("remember_fact", content="Fact one", section="other")
        _call_tool("remember_fact", content="Fact two", section="other")
        assert len(memory_store_mod.MemoryStore().all_facts()) == 2

    def test_the_description_carries_the_routing_guide_and_no_profile_example(self):
        from backend.ai.memory_structure import routing_guide_for_tool
        from backend.ai.tools.registry import build_tool_schemas

        schema = next(s for s in build_tool_schemas() if s["name"] == "remember_fact")
        assert routing_guide_for_tool() in schema["description"]
        assert "retire at 45" not in json.dumps(schema)
        assert "sensitive" in schema["input_schema"]["properties"]
        assert "health" in schema["input_schema"]["properties"]["sensitive"]["description"]
        assert schema["input_schema"]["required"] == ["content", "section"]


def test_update_profile_notes_description_says_edit_not_rewrite():
    from backend.ai.memory_structure import routing_guide_for_tool
    from backend.ai.tools.registry import build_tool_schemas

    schema = next(s for s in build_tool_schemas() if s["name"] == "update_profile")
    notes = schema["input_schema"]["properties"]["changes"]["properties"]["notes"]["description"]
    assert "Instructions for Lucius" in notes
    assert "edit or add a line, never rewrite their text wholesale" in notes
    assert "anything else worth keeping goes to remember_fact" in notes
    assert routing_guide_for_tool() not in notes


def test_schema_golden_matches():
    """The three memory tools' live schemas are pinned in the hand-edited golden file."""
    from backend.ai.tools.registry import build_tool_schemas

    golden = json.loads(
        (
            __import__("pathlib").Path(__file__).parent / "golden" / "tool_input_schemas.json"
        ).read_text()
    )
    live = {s["name"]: s["input_schema"] for s in build_tool_schemas()}
    for name in ("remember_fact", "recall_memory", "forget_fact", "update_profile"):
        assert live[name] == golden[name], name


# ══════════════════════════════════════════════════════════════════════════════
# forget_fact
# ══════════════════════════════════════════════════════════════════════════════


class TestForgetFact:
    def test_blocked_when_global_memory_off(self, isolated):
        """forget_fact must return ok=False with a message when memory is OFF."""
        settings_store_mod.MemorySettingsStore().set_enabled(False)
        # Pre-populate a fact that would otherwise be a match.
        memory_store_mod.MemoryStore().insert(_make_fact("risk tolerance is moderate", "f1"))
        result, proposed = _call_tool("forget_fact", description="risk tolerance")
        assert result["ok"] is False
        assert "message" in result
        assert proposed is None

    def test_blocked_does_not_remove_fact(self, isolated):
        """When memory is OFF, the pre-existing fact must not be removed."""
        settings_store_mod.MemorySettingsStore().set_enabled(False)
        memory_store_mod.MemoryStore().insert(_make_fact("risk tolerance is moderate", "f1"))
        _call_tool("forget_fact", description="risk tolerance")
        assert any(f["id"] == "f1" for f in memory_store_mod.MemoryStore().all_facts())

    def test_succeeds_and_removes_fact(self, isolated, monkeypatch):
        """forget_fact returns ok=True when memory is ON and the LLM identifies a match."""
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        memory_store_mod.MemoryStore().insert(_make_fact("risk tolerance is moderate", "target-1"))

        fake = _FakeProvider(target_id="target-1")
        monkeypatch.setattr(memory_mod, "_provider", lambda: fake)

        result, proposed = _call_tool("forget_fact", description="risk tolerance")
        assert result["ok"] is True
        assert proposed is None
        # Fact must actually be gone.
        remaining = memory_store_mod.MemoryStore().all_facts()
        assert not any(f["id"] == "target-1" for f in remaining)

    def test_returns_false_when_no_match(self, isolated, monkeypatch):
        """forget_fact returns ok=False when the LLM returns 'none'."""
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        memory_store_mod.MemoryStore().insert(_make_fact("something unrelated", "f2"))

        monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProvider(target_id="none"))

        result, proposed = _call_tool("forget_fact", description="totally unknown fact")
        assert result["ok"] is False

    def test_no_facts_returns_false(self, isolated, monkeypatch):
        """forget_fact returns ok=False when the store is empty."""
        settings_store_mod.MemorySettingsStore().set_enabled(True)
        monkeypatch.setattr(memory_mod, "_provider", lambda: _FakeProvider())

        result, _ = _call_tool("forget_fact", description="anything")
        assert result["ok"] is False


# ══════════════════════════════════════════════════════════════════════════════
# agent_remember_fact
# ══════════════════════════════════════════════════════════════════════════════

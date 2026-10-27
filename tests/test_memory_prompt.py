"""What the system prompt says about memory: one routing guide, Instructions for Lucius labelled
as the user's, the citation rule in the cacheable half, and no "preferences go in notes" lines.
"""

from __future__ import annotations

import pytest

from backend.ai.memory_structure import ROUTING_GUIDE
from backend.ai.prompts import CACHE_SPLIT_MARKER, build_system_prompt
from backend.services import profile as profile_service


@pytest.fixture
def prompt(db_isolated):
    profile_service.write_profile(
        {**profile_service.read_profile(), "notes": "Always answer in plain English."}
    )
    return build_system_prompt()


def test_system_prompt_renders_routing_guide_once(prompt):
    assert prompt.count(ROUTING_GUIDE) == 1
    stable, volatile = prompt.split(CACHE_SPLIT_MARKER)
    assert ROUTING_GUIDE in stable and ROUTING_GUIDE not in volatile


def test_system_prompt_no_longer_sends_preferences_to_notes(prompt):
    assert "goes in notes" not in prompt
    assert "that goes in PROFILE notes" not in prompt
    assert "goes in the profile's `notes` field" not in prompt
    assert "PERSONAL NOTES" not in prompt


def test_the_notes_are_labelled_as_the_users_instructions(prompt):
    _, volatile = prompt.split(CACHE_SPLIT_MARKER)
    assert "INSTRUCTIONS FROM THE USER:\nAlways answer in plain English." in volatile


def test_the_citation_rule_is_in_the_stable_half(prompt):
    stable, volatile = prompt.split(CACHE_SPLIT_MARKER)
    assert "<memory-used>m7a88f8 m3c01d2</memory-used>" in stable
    assert "memory-used" not in volatile
    assert "never mention" in stable.lower() or "never show" in stable.lower()


def _store_live(fid: str, content: str, **extra) -> None:
    from backend.db import MemoryStore

    MemoryStore().insert(
        {
            "id": fid,
            "content": content,
            "state": "live",
            "section": "you",
            "topic": "About you",
            "source": "implicit",
            "expires_at": None,
            "created_at": "2026-03-04T10:00:00",
            "updated_at": "2026-03-04T10:00:00",
            **extra,
        }
    )


def test_the_briefing_block_carries_no_citation_handles(db_isolated):
    from backend.ai.prompts import _fmt_memory, memory_block
    from backend.db.memory_settings_store import MemorySettingsStore

    MemorySettingsStore().set_enabled(True)
    _store_live("mem_7a88f842d02f", "Prefers short answers")

    assert "[m7a88f8]" in _fmt_memory()
    block = memory_block()
    assert "Prefers short answers" in block and "[m7a88f8]" not in block


def test_a_saved_sensitive_fact_is_in_the_prompt_whatever_the_setting(db_isolated):
    from backend.db.memory_settings_store import MemorySettingsStore

    MemorySettingsStore().set_enabled(True)
    _store_live("mem_000000000001", "Prefers short answers")
    _store_live("mem_000000000002", "Has a chronic condition")

    MemorySettingsStore().set_sensitive_topics(False)
    assert "chronic" in build_system_prompt()
    MemorySettingsStore().set_sensitive_topics(True)
    assert "chronic" in build_system_prompt()

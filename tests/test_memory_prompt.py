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

"""The You topic is "Profile" (ADR 2026-10-25 amendment 7, ruling R30), kept distinct from a
Settings profile field in every prompt. A write naming the old topic "About you" lands as
"Profile". Synthetic facts only."""

from __future__ import annotations

import pytest

from backend.ai import memory_structure as ms
from backend.ai.memory_write import apply_memory_change
from backend.db.memory_store import MemoryStore

TODAY = "2026-10-27"


@pytest.fixture(autouse=True)
def _isolated(db_isolated):
    return db_isolated


def _guide_rows() -> list[str]:
    return ms.ROUTING_GUIDE.splitlines()[1:]


def test_the_you_topics_are_profile_and_preferences():
    assert ms.SUGGESTED_TOPICS["you"] == ("Profile", "Preferences")


def test_the_guide_keeps_a_settings_field_and_the_memory_topic_apart():
    rows = _guide_rows()
    assert "a Settings profile field (update_profile) — not memory" in rows[0]
    assert "Memory · You · Profile" in rows[7]
    assert "About you" not in ms.ROUTING_GUIDE
    short = ms.routing_guide_for_tool()
    assert "Settings profile field" in short and "You · Profile" in short


@pytest.mark.parametrize("old", ["About you", "about YOU", "  About   you "])
def test_a_write_naming_about_you_lands_as_profile(old):
    out = apply_memory_change(
        {"new": [{"content": "Speaks Portuguese at home", "section": "you", "topic": old}]},
        source="implicit",
        strict=False,
        today=TODAY,
    )
    assert out["created"][0]["topic"] == "Profile"
    fact = out["created"][0]
    apply_memory_change(
        {"updates": [{"id": fact["id"], "topic": "About you"}]},
        source="explicit",
        strict=False,
        today=TODAY,
    )
    assert MemoryStore().find_by_id(fact["id"])["topic"] == "Profile"


def test_about_you_outside_the_you_section_is_left_alone():
    out = apply_memory_change(
        {"new": [{"content": "A note filed oddly", "section": "other", "topic": "About you"}]},
        source="implicit",
        strict=False,
        today=TODAY,
    )
    assert out["created"][0]["topic"] == "About you"

"""The public demo's seeded memory is filed the way a real fact is: every fact in a section
under a topic, none of them something the demo profile already holds."""

from __future__ import annotations

from backend.ai import memory_structure as ms
from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.memory_store import MemoryStore
from backend.scripts.demo import assistant


def test_every_seeded_fact_has_a_section_and_topic(db_isolated):
    MemorySettingsStore().set_enabled(True)

    assert assistant.seed_memory() == len(assistant.MEMORY)

    facts = MemoryStore().all_facts()
    assert len(facts) == len(assistant.MEMORY)
    for fact in facts:
        assert fact["state"] == "live" and fact["section"] in ms.SECTIONS
        assert fact["section"] == "open_items" or fact["topic"], fact["content"]
    assert {f["section"] for f in facts} != {"other"}


def test_no_seeded_fact_restates_a_profile_field():
    assert not any("retire" in content.lower() for content, _, _ in assistant.MEMORY)

"""A forgotten fact stays forgotten (ADR 2026-10-25 amendment 7, ruling R29).

User-initiated forgets (the UI forget, forget-all, delete-topic, ``forget_fact``) still hard-delete
and leave a SHA-256 fingerprint of the normalised text — the hash only. The write choke refuses an
implicit write whose text matches one (``forgotten``); an explicit save of the same text is
allowed and clears it. A model ``remove`` never deletes: extraction retires ``stale``, and any
implicit remove of a live fact is a retire. Synthetic facts only."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.ai import memory
from backend.ai import memory_structure as ms
from backend.ai.memory_write import apply_memory_change
from backend.db.memory_forgotten_store import MemoryForgottenStore
from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.memory_store import MemoryStore

TODAY = "2026-10-27"
DOG = "Has a dog named Biscuit"


@pytest.fixture(autouse=True)
def _isolated(db_isolated):
    MemorySettingsStore().set_enabled(True)
    return db_isolated


def _write(ops: dict, *, source: str, strict: bool = False) -> dict:
    return apply_memory_change(ops, source=source, strict=strict, today=TODAY)


def _add(content: str, section: str = "you", topic: str | None = "Profile", source="explicit"):
    out = _write({"new": [{"content": content, "section": section, "topic": topic}]}, source=source)
    return out["created"][0]


def _client():
    from backend.main import app

    return TestClient(app)


# ── the fingerprint ──────────────────────────────────────────────────────────


def test_the_fingerprint_ignores_case_punctuation_and_spacing():
    base = ms.fingerprint(DOG)
    assert len(base) == 64 and base == ms.fingerprint(DOG)
    for variant in (
        "has a dog named biscuit",
        "Has a dog, named Biscuit!",
        "  HAS a  dog named\tBISCUIT.",
    ):
        assert ms.fingerprint(variant) == base, variant
    assert ms.fingerprint("Has a dog named Beans") != base
    assert ms.fingerprint("Has a dog called Biscuit") != base, "a paraphrase is not caught"


# ── a user forget records the hash, never the text ───────────────────────────


def test_forgetting_one_fact_records_only_its_hash(db_isolated):
    fact = _add(DOG)

    out = _write({"remove": [fact["id"]]}, source="explicit")

    assert out["removed"] == 1 and MemoryStore().all_facts() == []
    assert MemoryForgottenStore().known({ms.fingerprint(DOG)}) == {ms.fingerprint(DOG)}
    assert "biscuit" not in _whole_database().casefold(), "the text must not be stored anywhere"


def _whole_database() -> str:
    from backend.db.engine import get_engine
    from backend.db.tables import metadata

    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT hash FROM memory_forgotten")).fetchall()
        assert [r.hash for r in rows] == [ms.fingerprint(DOG)]
        return "\n".join(
            str(tuple(row))
            for table in metadata.sorted_tables
            for row in conn.execute(table.select())
        )


def test_the_forget_route_forget_all_and_delete_topic_record_fingerprints():
    one = _add("Plays badminton on Saturdays")
    _add("Banks with a credit union", section="money", topic="Banking")
    _add("Keeps receipts in a shoebox", section="money", topic="Banking")
    _add("Speaks Portuguese at home")
    client = _client()

    assert client.delete(f"/api/memory/facts/{one['id']}").status_code == 200
    assert (
        client.delete("/api/memory/topics", params={"section": "money", "topic": "Banking"}).json()[
            "deleted"
        ]
        == 2
    )
    assert client.delete("/api/memory/facts").json()["deleted"] == 1

    texts = (
        "Plays badminton on Saturdays",
        "Banks with a credit union",
        "Keeps receipts in a shoebox",
        "Speaks Portuguese at home",
    )
    wanted = {ms.fingerprint(t) for t in texts}
    assert MemoryForgottenStore().known(wanted) == wanted


def test_forget_fact_tool_records_the_fingerprint(monkeypatch):
    fact = _add(DOG)

    class _Picker:
        async def complete(self, prompt, **kwargs):
            return json.dumps({"id": fact["id"]})

    monkeypatch.setattr(memory, "_provider", lambda: _Picker())

    assert asyncio.run(memory.remove_fact("the dog")) is True
    assert MemoryForgottenStore().known({ms.fingerprint(DOG)})


# ── the choke enforces it ────────────────────────────────────────────────────


@pytest.mark.parametrize("again", [DOG, "has a dog named biscuit", "Has a dog — named Biscuit!"])
def test_an_implicit_re_add_is_refused_as_forgotten(again):
    fact = _add(DOG)
    _write({"remove": [fact["id"]]}, source="explicit")

    out = _write(
        {"new": [{"content": again, "section": "you", "topic": "Profile"}]}, source="implicit"
    )

    assert out["added"] == 0
    assert [r["reason"] for r in out["rejected"]] == ["forgotten"]
    assert MemoryStore().all_facts() == []


def test_an_implicit_update_cannot_rewrite_a_fact_into_a_forgotten_one():
    gone = _add(DOG)
    _write({"remove": [gone["id"]]}, source="explicit")
    other = _add("Has a dog", source="implicit")

    out = _write({"updates": [{"id": other["id"], "content": DOG}]}, source="implicit")

    assert out["updated"] == 0 and out["rejected"][0]["reason"] == "forgotten"
    assert MemoryStore().find_by_id(other["id"])["content"] == "Has a dog"


def test_an_explicit_re_add_is_allowed_and_clears_the_fingerprint():
    fact = _add(DOG)
    _write({"remove": [fact["id"]]}, source="explicit")

    again = memory.add_explicit("has a dog named Biscuit.", section="you", topic="Profile")

    assert again["content"] == "has a dog named Biscuit."
    assert MemoryForgottenStore().known({ms.fingerprint(DOG)}) == set()
    _write({"remove": [again["id"]]}, source="implicit")  # a model remove: retired, not deleted
    out = _write(
        {"new": [{"content": DOG, "section": "you", "topic": "Profile"}]}, source="implicit"
    )
    assert out["added"] == 1, "the user said it again, so it may be learned again"


def test_a_purge_or_a_retire_records_no_fingerprint():
    fact = _add("Plans a trip to Hokkaido", section="plans", topic="Hokkaido", source="implicit")
    _write({"retire": [{"id": fact["id"], "reason": "stale"}]}, source="implicit")
    _write({"remove": [fact["id"]]}, source="implicit")

    assert MemoryStore().all_facts() == []
    assert MemoryForgottenStore().known({ms.fingerprint("Plans a trip to Hokkaido")}) == set()


# ── a model never deletes ────────────────────────────────────────────────────


def test_an_implicit_remove_of_a_live_fact_is_a_stale_retire():
    fact = _add("Rents a flat in Tampines", section="money", topic="Housing", source="implicit")

    out = _write({"remove": [fact["id"]]}, source="implicit")

    kept = MemoryStore().find_by_id(fact["id"])
    assert out["removed"] == 0 and out["retired"] == 1
    assert (kept["state"], kept["retired_reason"]) == ("retired", "stale")


def test_extraction_remove_retires_the_fact_as_stale(monkeypatch):
    fact = _add("Rents a flat in Tampines", section="money", topic="Housing", source="implicit")

    class _Extractor:
        async def complete(self, prompt, **kwargs):
            return json.dumps({"new": [], "updates": [], "remove": [fact["id"]], "mentioned": []})

    monkeypatch.setattr(memory, "_provider", lambda: _Extractor())
    history = [
        {"role": "user", "content": "We moved out of the Tampines flat last month."},
        {"role": "assistant", "content": "Noted."},
    ]

    asyncio.run(memory.extract_and_merge(history))

    kept = MemoryStore().find_by_id(fact["id"])
    assert (kept["state"], kept["retired_reason"]) == ("retired", "stale")
    assert kept["retired_at"]
    assert MemoryForgottenStore().known({ms.fingerprint(fact["content"])}) == set()


def test_extraction_that_both_updates_and_removes_one_id_keeps_it(monkeypatch):
    fact = _add("Pays rent on the 1st", section="money", topic="Housing", source="implicit")

    class _Extractor:
        async def complete(self, prompt, **kwargs):
            return json.dumps(
                {
                    "new": [],
                    "updates": [{"id": fact["id"], "content": "Pays rent on the 3rd"}],
                    "remove": [fact["id"]],
                    "mentioned": [],
                }
            )

    monkeypatch.setattr(memory, "_provider", lambda: _Extractor())
    history = [
        {"role": "user", "content": "The landlord moved my rent day to the 3rd."},
        {"role": "assistant", "content": "Noted."},
    ]

    asyncio.run(memory.extract_and_merge(history))

    kept = MemoryStore().find_by_id(fact["id"])
    assert (kept["state"], kept["content"]) == ("live", "Pays rent on the 3rd")


def test_a_reset_wipes_the_fingerprints_with_the_memory():
    from backend.services.portability import reset_bridged_stores

    fact = _add(DOG)
    _write({"remove": [fact["id"]]}, source="explicit")
    assert MemoryForgottenStore().all_hashes()

    reset_bridged_stores()

    assert MemoryForgottenStore().all_hashes() == set()

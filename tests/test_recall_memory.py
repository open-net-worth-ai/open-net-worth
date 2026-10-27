"""Behavioral tests for the ``recall_memory`` tool (backend/ai/tools/memory.py) and its
scorer ``backend.ai.memory.recall_facts``.

The prompt's memory block carries at most 30 facts; recall searches all of them. Synthetic
facts only, isolated to a tmp DB via ``db_isolated``. No LLM, no network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import backend.db.memory_settings_store as settings_store_mod
import backend.db.memory_store as memory_store_mod

PAST = "2000-01-01"
FUTURE = "2999-12-31"


def _call(**inp):
    from backend.ai.tools import execute_tool

    text, proposed = asyncio.run(execute_tool("recall_memory", inp))
    return json.loads(text), proposed


def _seed(content, *, fid, created_at, source="implicit", expires_at=None, **extra):
    memory_store_mod.MemoryStore().insert(
        {
            "id": fid,
            "content": content,
            "source": source,
            "expires_at": expires_at,
            "created_at": created_at,
            "updated_at": created_at,
            **extra,
        }
    )


@pytest.fixture()
def memory_on(db_isolated):
    settings_store_mod.MemorySettingsStore().set_enabled(True)


def _contents(result):
    return [m["content"] for m in result["matches"]]


def test_more_relevant_fact_outranks_a_newer_one(memory_on):
    _seed("Rents an apartment with a lease ending in June", fid="m1", created_at="2026-01-01")
    _seed("Wants a bigger apartment someday", fid="m2", created_at="2026-09-01")

    result, proposed = _call(query="apartment lease")

    assert proposed is None
    assert result["ok"] is True
    assert _contents(result) == [
        "Rents an apartment with a lease ending in June",
        "Wants a bigger apartment someday",
    ]


def test_equal_relevance_is_broken_by_recency(memory_on):
    _seed("Apartment is in Tiong Bahru", fid="m1", created_at="2026-01-01")
    _seed("Apartment rent rose to 3,200", fid="m2", created_at="2026-08-01")
    _seed("Apartment has two bedrooms", fid="m3", created_at="2026-04-01")

    result, _ = _call(query="apartment")

    assert _contents(result) == [
        "Apartment rent rose to 3,200",
        "Apartment has two bedrooms",
        "Apartment is in Tiong Bahru",
    ]


def test_returns_every_field_the_model_needs(memory_on):
    _seed(
        "Plans to sell the car before moving",
        fid="m1",
        created_at="2026-05-01",
        source="explicit",
        expires_at=FUTURE,
        state="live",
        section="plans",
        topic="Move",
        last_used_at="2026-06-01",
    )

    match = _call(query="car")[0]["matches"][0]

    assert match == {
        "content": "Plans to sell the car before moving",
        "section": "plans",
        "topic": "Move",
        "source": "explicit",
        "created_at": "2026-05-01",
        "expires_at": FUTURE,
        "last_used_at": "2026-06-01",
    }


def test_an_unsorted_fact_is_found_and_says_it_is_unsorted(memory_on):
    _seed("Keeps a spare key with a neighbour", fid="m1", created_at="2026-05-01")

    match = _call(query="spare key")[0]["matches"][0]

    assert (match["section"], match["topic"]) == (None, None)


def test_recall_by_topic(memory_on):
    _seed(
        "Rents a flat in Tampines",
        fid="m1",
        created_at="2026-05-01",
        state="live",
        section="money",
        topic="Housing",
    )
    _seed(
        "Rent is due on the 1st",
        fid="m2",
        created_at="2026-05-02",
        state="live",
        section="money",
        topic="Housing",
    )
    _seed(
        "Wants to buy a flat by 2030",
        fid="m3",
        created_at="2026-05-03",
        state="live",
        section="plans",
        topic="Home purchase",
    )
    _seed(
        "Rents a storage unit",
        fid="m4",
        created_at="2026-05-04",
        state="live",
        section="money",
        topic="Storage",
    )
    _seed(
        "Old landlord was Lee",
        fid="m5",
        created_at="2026-05-05",
        state="retired",
        section="money",
        topic="Housing",
    )

    result = _call(topic=" housing")[0]

    assert _contents(result) == ["Rent is due on the 1st", "Rents a flat in Tampines"]
    assert result["total_matches"] == 2
    narrowed = _call(topic="Housing", query="due")[0]
    assert _contents(narrowed) == ["Rent is due on the 1st"]


def test_recall_by_a_topic_nobody_has_says_so(memory_on):
    _seed(
        "Rents a flat",
        fid="m1",
        created_at="2026-05-01",
        state="live",
        section="money",
        topic="Housing",
    )

    result = _call(topic="Cars")[0]

    assert result["matches"] == [] and "Nothing remembered" in result["note"]


def test_conversational_query_matches_on_its_subject_word(memory_on):
    _seed("Plan: retire at 50 in Lisbon", fid="m1", created_at="2026-01-01")
    _seed("Prefers index funds", fid="m2", created_at="2026-02-01")

    result, _ = _call(query="what do you know about my plans")

    assert _contents(result) == ["Plan: retire at 50 in Lisbon"]


def test_expired_facts_are_excluded(memory_on):
    _seed(
        "Saving for a trip to Japan",
        fid="live",
        created_at="2026-01-01",
        tier="time_bound",
        expires_at=FUTURE,
    )
    _seed(
        "Saving for a trip to Peru",
        fid="gone",
        created_at="2026-02-01",
        tier="time_bound",
        expires_at=PAST,
    )
    _seed("Trip budget is fixed", fid="gone_durable", created_at="2026-03-01", expires_at=PAST)

    result, _ = _call(query="trip")

    assert _contents(result) == ["Saving for a trip to Japan"]
    assert result["total_facts"] == 1


def test_limit_caps_the_result_and_says_more_matched(memory_on):
    for i in range(30):
        _seed(f"Bike service note {i}", fid=f"m{i}", created_at=f"2026-01-{i + 1:02d}")

    result, _ = _call(query="bike", limit=5)

    assert result["showing"] == 5
    assert result["total_matches"] == 30
    assert "note" in result
    assert _contents(result)[0] == "Bike service note 29"


def test_limit_is_clamped_to_the_tool_bound(memory_on):
    for i in range(30):
        _seed(f"Bike service note {i}", fid=f"m{i}", created_at=f"2026-01-{i + 1:02d}")

    result, _ = _call(query="bike", limit=500)

    assert result["showing"] == 25


def test_long_fact_content_is_truncated(memory_on):
    _seed("Garden " + "x" * 2000, fid="m1", created_at="2026-01-01")

    content = _call(query="garden")[0]["matches"][0]["content"]

    assert len(content) == 500
    assert content.endswith("…")


def test_no_match_returns_empty_with_a_note(memory_on):
    _seed("Prefers index funds", fid="m1", created_at="2026-01-01")

    result, _ = _call(query="sailboat")

    assert result["ok"] is True
    assert result["matches"] == []
    assert "note" in result


def test_memory_off_returns_no_facts(memory_on):
    _seed("Rents an apartment", fid="m1", created_at="2026-01-01")
    settings_store_mod.MemorySettingsStore().set_enabled(False)

    result, proposed = _call(query="apartment")

    assert proposed is None
    assert result["ok"] is False
    assert result["memory_enabled"] is False
    assert "matches" not in result
    assert "apartment" not in json.dumps(result).lower()


@pytest.mark.parametrize("query", ["", "   ", "what do you know about me"])
def test_query_without_a_searchable_word_is_refused(memory_on, query):
    _seed("Rents an apartment", fid="m1", created_at="2026-01-01")

    result, _ = _call(query=query)

    assert result["ok"] is False
    assert "error" in result


def test_recall_writes_nothing(memory_on):
    _seed("Rents an apartment", fid="m1", created_at="2026-01-01")
    before = memory_store_mod.MemoryStore().all_facts()

    _call(query="apartment")

    assert memory_store_mod.MemoryStore().all_facts() == before


def test_classified_as_a_reviewed_read():
    import backend.ai.tools  # noqa: F401 — registers every tool
    from backend.ai.tools._write_classification import READ_ONLY_TOOLS
    from backend.ai.tools.registry import mutated_domains

    assert mutated_domains("recall_memory") == ()
    assert "recall_memory" in READ_ONLY_TOOLS


def test_a_saved_sensitive_fact_is_recalled_whatever_the_setting(memory_on):
    _seed(
        "Trip insurance covers the knee surgery",
        fid="s",
        created_at="2026-01-01",
        state="live",
        section="you",
        topic="About you",
    )
    _seed(
        "Trip to Japan in spring",
        fid="p",
        created_at="2026-01-02",
        state="live",
        section="plans",
        topic="Japan",
    )

    settings_store_mod.MemorySettingsStore().set_sensitive_topics(False)
    off, _ = _call(query="trip")
    settings_store_mod.MemorySettingsStore().set_sensitive_topics(True)
    on, _ = _call(query="trip")

    both = ["Trip insurance covers the knee surgery", "Trip to Japan in spring"]
    assert sorted(_contents(off)) == both
    assert sorted(_contents(on)) == both


def test_recall_and_the_prompt_agree_on_a_durable_legacy_fact_with_a_stray_date(memory_on):
    _seed(
        "Trip savings live at the credit union",
        fid="d",
        created_at="2026-01-01",
        state="unsorted",
        tier="durable",
        expires_at=PAST,
    )

    result, _ = _call(query="trip")

    assert _contents(result) == ["Trip savings live at the credit union"]

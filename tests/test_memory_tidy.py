"""The daily tidy pass: unused facts and stale plans fade without a model; duplicates and
near-duplicate topics merge through the write choke, and a destructive reply is refused.

All model calls are mocked.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.ai import memory, memory_tidy
from backend.core.user_time import user_today
from backend.db import MemoryStore
from backend.db.memory_consolidate_state_store import MemoryConsolidateStateStore
from backend.db.memory_settings_store import MemorySettingsStore


@pytest.fixture
def isolated(db_isolated):
    MemorySettingsStore().set_enabled(True)


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _fact(fid: str, content: str, section: str = "other", topic: str | None = "Misc", **extra):
    return {
        "id": fid,
        "content": content,
        "section": section,
        "topic": topic,
        "state": "live",
        "source": "implicit",
        "expires_at": None,
        "created_at": _ago(1),
        "updated_at": _ago(1),
        **extra,
    }


class _Model:
    def __init__(self, reply: dict | None = None, fail: bool = False):
        self.reply = reply if reply is not None else {"updates": [], "remove": []}
        self.fail = fail
        self.prompts: list[str] = []

    async def complete(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("model down")
        return json.dumps(self.reply)


def _use(monkeypatch, model: _Model) -> _Model:
    monkeypatch.setattr(memory, "_provider", lambda: model)
    return model


def _by_id() -> dict:
    return {f["id"]: f for f in MemoryStore().all_facts()}


def test_tidy_retires_unused_after_90_days_but_not_injected_only(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _fact("m_idle", "Likes window seats", created_at=_ago(120), updated_at=_ago(1)),
            _fact(
                "m_cited",
                "Banks at the credit union",
                created_at=_ago(200),
                last_used_at=_ago(10),
            ),
            _fact("m_sorted", "Rents in Tampines", created_at=_ago(200), sorted_at=_ago(20)),
            _fact("m_new", "Has a dog named Beans", created_at=_ago(30)),
            _fact("m_unsorted", "Old legacy fact", state="unsorted", created_at=_ago(400)),
        ]
    )
    _use(monkeypatch, _Model())

    result = asyncio.run(memory_tidy.tidy())

    facts = _by_id()
    assert facts["m_idle"]["state"] == "retired"
    assert facts["m_idle"]["retired_reason"] == "unused"
    assert [facts[i]["state"] for i in ("m_cited", "m_sorted", "m_new")] == ["live"] * 3
    assert facts["m_unsorted"]["state"] == "unsorted"
    assert result["ran"] is True and result["retired"] == 1


def test_tidy_expires_plans_unmentioned_for_90_days(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _fact(
                "p_old",
                "Plans to move to Lisbon",
                section="plans",
                topic="Move abroad",
                created_at=_ago(200),
                mentioned_at=_ago(100),
            ),
            _fact(
                "p_again",
                "Plans to buy a flat",
                section="plans",
                topic="Home",
                created_at=_ago(200),
                mentioned_at=_ago(5),
            ),
        ]
    )
    _use(monkeypatch, _Model())

    asyncio.run(memory_tidy.tidy())

    facts = _by_id()
    assert (facts["p_old"]["state"], facts["p_old"]["retired_reason"]) == ("retired", "stale")
    assert facts["p_again"]["state"] == "live"


def test_tidy_merges_duplicates_and_topic_names_through_the_choke(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _fact("a", "Rents a flat in Tampines", section="money", topic="Housing"),
            _fact("b", "Lives in a rented flat in Tampines", section="money", topic="Housing"),
            _fact("c", "Pays rent on the 1st", section="money", topic="Home"),
            _fact("d", "Has two cats", section="you", topic="About you"),
            _fact("e", "Prefers email over calls", section="you", topic="About you"),
            _fact("f", "Is a vegetarian", section="you", topic="About you"),
        ]
    )
    model = _use(
        monkeypatch,
        _Model(
            {
                "updates": [{"id": "c", "topic": "Housing"}],
                "remove": ["b"],
            }
        ),
    )

    result = asyncio.run(memory_tidy.tidy())

    facts = _by_id()
    assert "b" not in facts
    assert facts["c"]["topic"] == "Housing"
    assert set(facts) == {"a", "c", "d", "e", "f"}
    assert result["merged"] is True and result["removed"] == 1
    assert "Tampines" in model.prompts[0]


def test_the_merge_never_shows_a_sensitive_fact_while_the_setting_is_off(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _fact("a", "Rents a flat in Tampines", section="money", topic="Housing"),
            _fact("b", "Pays rent on the 1st", section="money", topic="Housing"),
            _fact("h", "Has a chronic condition", section="you", topic="About you", sensitive=True),
        ]
    )
    model = _use(monkeypatch, _Model())

    asyncio.run(memory_tidy.tidy())

    assert "Tampines" in model.prompts[0]
    assert "chronic condition" not in model.prompts[0]


def test_tidy_refuses_a_destructive_reply(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [_fact(f"f{i}", f"Distinct fact number {i}", topic="Misc") for i in range(4)]
        + [_fact("g", "Another subject", section="you", topic="About you")]
    )
    _use(monkeypatch, _Model({"updates": [], "remove": ["f0", "f1", "f2"]}))

    result = asyncio.run(memory_tidy.tidy())

    assert set(_by_id()) == {"f0", "f1", "f2", "f3", "g"}
    assert result["merged"] is False and result["refused"] is True


def test_tidy_runs_at_most_once_a_day(isolated, monkeypatch):
    MemoryStore().bulk_insert([_fact("a", "One thing"), _fact("b", "Another thing")])
    model = _use(monkeypatch, _Model())

    first = asyncio.run(memory_tidy.tidy())
    second = asyncio.run(memory_tidy.tidy())
    forced = asyncio.run(memory_tidy.tidy(force=True))

    assert (first["ran"], second["ran"], forced["ran"]) == (True, False, True)
    assert len(model.prompts) == 2
    assert MemoryConsolidateStateStore().get()["last_tidy"] == user_today().isoformat()


def _cited(fid: str = "m_cited") -> dict:
    """A fact cited lately — proof that usage tracking reaches the book, without which nothing
    is retired as unused."""
    return _fact(fid, "Cited the other day", created_at=_ago(300), last_used_at=_ago(3))


def test_a_failed_model_call_still_applies_the_deterministic_rules(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _fact("m_idle", "Likes window seats", created_at=_ago(120)),
            _fact("b", "Fresh fact"),
            _cited(),
        ]
    )
    _use(monkeypatch, _Model(fail=True))

    result = asyncio.run(memory_tidy.tidy())

    assert _by_id()["m_idle"]["state"] == "retired"
    assert result["ran"] is True and result["merged"] is False


def test_tidy_drops_what_has_been_retired_for_30_days(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _fact("gone", "Faded long ago", state="retired", retired_at=_ago(45)),
            _fact("kept", "Faded last week", state="retired", retired_at=_ago(7)),
            _fact("live", "Still here"),
        ]
    )
    _use(monkeypatch, _Model())

    asyncio.run(memory_tidy.tidy())

    assert set(_by_id()) == {"kept", "live"}


def test_tidy_is_a_no_op_while_memory_is_off(isolated, monkeypatch):
    MemoryStore().bulk_insert([_fact("m_idle", "Likes window seats", created_at=_ago(300))])
    MemorySettingsStore().set_enabled(False)
    model = _use(monkeypatch, _Model())

    assert asyncio.run(memory_tidy.tidy()) == {"ran": False}
    assert _by_id()["m_idle"]["state"] == "live" and model.prompts == []


def test_a_filed_legacy_fact_is_not_retired_as_unused(isolated, monkeypatch):
    from backend.ai.memory_write import apply_memory_change

    MemoryStore().bulk_insert(
        [
            {
                "id": "m_legacy",
                "content": "Prefers index funds",
                "state": "unsorted",
                "source": "implicit",
                "created_at": _ago(400),
                "updated_at": _ago(400),
            }
        ]
    )
    apply_memory_change(
        {"updates": [{"id": "m_legacy", "section": "money", "topic": "Investing"}]},
        source="implicit",
        strict=False,
    )
    _use(monkeypatch, _Model())

    asyncio.run(memory_tidy.tidy(force=True))

    assert _by_id()["m_legacy"]["state"] == "live"


def test_the_unused_boundary_is_90_days(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _fact("d89", "Eighty-nine days old", created_at=_ago(89)),
            _fact("d91", "Ninety-one days old", created_at=_ago(91)),
            _cited(),
        ]
    )
    _use(monkeypatch, _Model())

    asyncio.run(memory_tidy.tidy())

    assert (_by_id()["d89"]["state"], _by_id()["d91"]["state"]) == ("live", "retired")


def test_a_mention_or_a_user_edit_resets_the_unused_clock_but_being_shown_does_not(
    isolated, monkeypatch
):
    MemoryStore().bulk_insert(
        [
            _fact(
                "m_mentioned",
                "Said again lately, never cited",
                created_at=_ago(200),
                mentioned_at=_ago(1),
            ),
            _fact("m_edited", "Corrected by the user", created_at=_ago(200), edited_at=_ago(2)),
            _fact(
                "m_shown",
                "Only ever shown in the prompt",
                created_at=_ago(200),
                updated_at=_ago(1),
            ),
            _cited(),
        ]
    )
    _use(monkeypatch, _Model())

    asyncio.run(memory_tidy.tidy())

    facts = _by_id()
    assert facts["m_mentioned"]["state"] == "live"
    assert facts["m_edited"]["state"] == "live"
    assert facts["m_shown"]["retired_reason"] == "unused"


def test_nothing_retires_as_unused_while_no_fact_has_been_cited_lately(
    isolated, monkeypatch, caplog
):
    MemoryStore().bulk_insert(
        [
            _fact("m_old", "Never cited", created_at=_ago(200)),
            _fact(
                "m_long_ago", "Cited once, long ago", created_at=_ago(300), last_used_at=_ago(95)
            ),
        ]
    )
    _use(monkeypatch, _Model())

    with caplog.at_level("INFO", logger="backend.ai.memory_tidy"):
        result = asyncio.run(memory_tidy.tidy())

    assert [f["state"] for f in _by_id().values()] == ["live", "live"]
    assert result["retired"] == 0
    assert any("not retiring any as unused" in r.getMessage() for r in caplog.records)


def test_a_user_edit_through_the_dialog_counts_as_a_signal(isolated, monkeypatch):
    from backend.ai.memory_instruct import edit_fact

    MemoryStore().bulk_insert([_fact("m_fix", "Lives in a flat", created_at=_ago(200)), _cited()])
    edit_fact("m_fix", "Lives in a rented flat")
    _use(monkeypatch, _Model())

    asyncio.run(memory_tidy.tidy())

    assert _by_id()["m_fix"]["state"] == "live"


def test_a_legacy_plan_filed_by_the_sort_survives_the_next_tidy(isolated, monkeypatch):
    from backend.ai import memory_sort

    MemoryStore().bulk_insert(
        [
            {
                "id": "m_condo",
                "content": "Considering buying a condo in 2028",
                "state": "unsorted",
                "section": None,
                "topic": None,
                "tier": "durable",
                "source": "implicit",
                "expires_at": None,
                "created_at": _ago(200),
                "updated_at": _ago(200),
            },
            _cited(),
        ]
    )
    monkeypatch.setattr(
        memory,
        "_provider",
        lambda: _Model({"facts": [{"id": "m_condo", "section": "plans", "topic": "Condo"}]}),
    )
    asyncio.run(memory_sort.sort_unsorted())
    assert _by_id()["m_condo"]["state"] == "live"
    _use(monkeypatch, _Model())

    asyncio.run(memory_tidy.tidy(force=True))

    assert _by_id()["m_condo"]["state"] == "live"


def test_a_fact_with_no_created_at_is_left_alone(isolated, monkeypatch):
    MemoryStore().bulk_insert([_fact("m_undated", "No dates at all", created_at=None)])
    _use(monkeypatch, _Model())

    asyncio.run(memory_tidy.tidy())

    assert _by_id()["m_undated"]["state"] == "live"


def test_removing_exactly_half_a_topic_is_allowed(isolated, monkeypatch):
    MemoryStore().bulk_insert([_fact(f"f{i}", f"Distinct fact number {i}") for i in range(4)])
    _use(monkeypatch, _Model({"updates": [], "remove": ["f0", "f1"]}))

    result = asyncio.run(memory_tidy.tidy())

    assert set(_by_id()) == {"f2", "f3"} and result["merged"] is True


def test_a_reply_naming_ids_it_was_not_shown_changes_nothing_for_them(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _fact("a", "Fact a"),
            _fact("b", "Fact b"),
            _fact("legacy", "Old legacy fact", state="unsorted"),
        ]
    )
    _use(
        monkeypatch,
        _Model(
            {
                "updates": [
                    {"id": "legacy", "content": "Rewritten", "topic": "Elsewhere"},
                    {"id": "ghost", "content": "Invented"},
                    {
                        "id": "a",
                        "content": "Fact a, reworded",
                        "section": "you",
                        "state": "retired",
                    },
                ],
                "remove": ["legacy", "ghost"],
            }
        ),
    )

    asyncio.run(memory_tidy.tidy())

    facts = _by_id()
    assert set(facts) == {"a", "b", "legacy"}
    assert (
        facts["legacy"]["content"] == "Old legacy fact" and facts["legacy"]["state"] == "unsorted"
    )
    assert facts["a"]["content"] == "Fact a, reworded"
    assert facts["a"]["section"] == "other" and facts["a"]["state"] == "live"


def test_a_state_store_failure_is_logged_not_swallowed(isolated, monkeypatch, caplog):
    def boom(self):
        raise RuntimeError("state store down")

    monkeypatch.setattr(MemoryConsolidateStateStore, "get", boom)
    monkeypatch.setattr(MemoryConsolidateStateStore, "save", lambda self, doc: boom(self))
    _use(monkeypatch, _Model())

    with caplog.at_level("WARNING", logger="backend.ai.memory_tidy"):
        assert asyncio.run(memory_tidy.tidy())["ran"] is True

    assert sum("tidy date" in r.getMessage() for r in caplog.records) == 2

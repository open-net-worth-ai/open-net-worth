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


def test_a_failed_model_call_still_applies_the_deterministic_rules(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [_fact("m_idle", "Likes window seats", created_at=_ago(120)), _fact("b", "Fresh fact")]
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

"""Sorting the legacy facts: one memory-model call files the oldest unsorted facts into a
section and topic (or retires them with a reason) through the write choke. All model calls
are mocked; the facts are synthetic."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.ai import memory, memory_idle, memory_sort, memory_tidy
from backend.db import MemoryStore
from backend.db.memory_settings_store import MemorySettingsStore


@pytest.fixture
def isolated(db_isolated):
    MemorySettingsStore().set_enabled(True)


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _legacy(fid: str, content: str, age: int = 5, **extra) -> dict:
    return {
        "id": fid,
        "content": content,
        "section": None,
        "topic": None,
        "state": "unsorted",
        "source": "implicit",
        "expires_at": None,
        "created_at": _ago(age),
        "updated_at": _ago(age),
        **extra,
    }


class _Model:
    def __init__(self, reply=None, raw: str | None = None):
        self.reply = reply if reply is not None else {"facts": []}
        self.raw = raw
        self.calls: list[dict] = []

    async def complete(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return self.raw if self.raw is not None else json.dumps(self.reply)


def _use(monkeypatch, model: _Model) -> _Model:
    monkeypatch.setattr(memory, "_provider", lambda: model)
    return model


def _by_id() -> dict:
    return {f["id"]: f for f in MemoryStore().all_facts()}


def test_sort_files_into_a_section_and_topic(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _legacy("a", "Rents a flat in Tampines", source="explicit"),
            _legacy("b", "Prefers short answers"),
            {
                "id": "live1",
                "content": "Owns a car",
                "section": "money",
                "topic": "Housing",
                "state": "live",
                "source": "implicit",
                "expires_at": None,
                "created_at": _ago(3),
                "updated_at": _ago(3),
            },
        ]
    )
    model = _use(
        monkeypatch,
        _Model(
            {
                "facts": [
                    {"id": "a", "section": "money", "topic": " housing "},
                    {
                        "id": "b",
                        "section": "you",
                        "topic": "Preferences",
                        "content": "Prefers short answers.",
                    },
                ]
            }
        ),
    )

    out = asyncio.run(memory_sort.sort_unsorted())

    facts = _by_id()
    assert (facts["a"]["state"], facts["a"]["section"], facts["a"]["topic"]) == (
        "live",
        "money",
        "Housing",
    )
    assert facts["a"]["source"] == "explicit" and facts["a"].get("sorted_at")
    assert (facts["b"]["state"], facts["b"]["section"]) == ("live", "you")
    assert facts["b"]["content"] == "Prefers short answers."
    assert facts["b"]["source"] == "implicit"
    assert out["sorted"] == 2 and out["retired"] == 0 and out["remaining"] == 0
    (call,) = model.calls
    assert call["source"] == "memory_sort" and call["effort"] == "none"
    assert "a: Rents a flat in Tampines" in call["prompt"]
    assert "live1" not in call["prompt"] and "Housing" in call["prompt"]
    assert "Where a fact goes" in call["prompt"]


def test_sort_retires_with_reason_and_keeps_row(isolated, monkeypatch):
    MemoryStore().bulk_insert([_legacy("a", "Checking balance is high")])
    _use(monkeypatch, _Model({"facts": [{"id": "a", "retire": "ledger"}]}))

    out = asyncio.run(memory_sort.sort_unsorted())

    row = _by_id()["a"]
    assert (row["state"], row["retired_reason"]) == ("retired", "ledger")
    assert row.get("retired_at") and out["retired"] == 1 and out["sorted"] == 0


def test_sort_skips_bad_items_applies_rest(isolated, monkeypatch, caplog):
    MemoryStore().bulk_insert(
        [_legacy("a", "one", 9), _legacy("b", "two", 8), _legacy("c", "three", 7)]
    )
    _use(
        monkeypatch,
        _Model(
            {
                "facts": [
                    {"id": "a", "section": "nowhere", "topic": "X"},
                    {"id": "ghost", "section": "money"},
                    {"id": "b", "section": "other", "state": "retired", "source": "explicit"},
                    "not an object",
                ]
            }
        ),
    )

    with caplog.at_level("DEBUG", logger="backend.ai.memory_sort"):
        out = asyncio.run(memory_sort.sort_unsorted())

    facts = _by_id()
    assert facts["a"]["state"] == "unsorted" and facts["c"]["state"] == "unsorted"
    assert (facts["b"]["state"], facts["b"]["section"]) == ("live", "other")
    assert facts["b"]["source"] == "implicit" and "retired_reason" not in facts["b"]
    assert out["sorted"] == 1 and out["remaining"] == 2
    assert "nowhere" in caplog.text and "ghost" in caplog.text


@pytest.mark.parametrize(
    "raw",
    ["not json at all", '["a", "b"]', '{"facts": "oops"}', '{"other": []}', ""],
)
def test_sort_malformed_reply_changes_nothing(isolated, monkeypatch, raw):
    MemoryStore().bulk_insert([_legacy("a", "one")])
    _use(monkeypatch, _Model(raw=raw))

    out = asyncio.run(memory_sort.sort_unsorted())

    assert _by_id()["a"]["state"] == "unsorted"
    assert out["sorted"] == 0 and out["retired"] == 0 and out["remaining"] == 1


def test_sort_noop_when_memory_off_or_demo(isolated, monkeypatch):
    MemoryStore().bulk_insert([_legacy("a", "one")])
    model = _use(monkeypatch, _Model({"facts": [{"id": "a", "section": "other"}]}))

    MemorySettingsStore().set_enabled(False)
    assert asyncio.run(memory_sort.sort_unsorted())["sorted"] == 0

    MemorySettingsStore().set_enabled(True)
    monkeypatch.setattr(
        "backend.db.memory_settings_store.get_settings",
        lambda: SimpleNamespace(demo_mode=True),
    )
    assert asyncio.run(memory_sort.sort_unsorted())["sorted"] == 0

    assert model.calls == [] and _by_id()["a"]["state"] == "unsorted"


def test_sort_takes_at_most_25(isolated, monkeypatch):
    MemoryStore().bulk_insert([_legacy(f"f{i:02d}", f"fact {i}", age=100 - i) for i in range(30)])
    model = _use(monkeypatch, _Model())

    asyncio.run(memory_sort.sort_unsorted())

    prompt = model.calls[0]["prompt"]
    shown = [i for i in range(30) if f"f{i:02d}: fact {i}" in prompt]
    assert shown == list(range(25))

    model = _use(monkeypatch, _Model())
    asyncio.run(memory_sort.sort_unsorted(limit=3))
    assert sum(f"f{i:02d}: " in model.calls[0]["prompt"] for i in range(30)) == 3


def test_retired_purged_after_30_days(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _legacy("old", "x", state="retired", retired_at=_ago(31), retired_reason="stale"),
            _legacy("new", "y", state="retired", retired_at=_ago(29), retired_reason="stale"),
        ]
    )
    _use(monkeypatch, _Model({"updates": [], "remove": []}))

    asyncio.run(memory_tidy.tidy(force=True))

    assert set(_by_id()) == {"new"}


def test_sort_remaining_loops_until_done_and_stops_without_progress(isolated, monkeypatch):
    MemoryStore().bulk_insert([_legacy(f"f{i}", f"fact {i}", age=50 - i) for i in range(4)])

    class _Half(_Model):
        async def complete(self, prompt, **kwargs):
            await super().complete(prompt, **kwargs)
            ids = [line.split(":")[0] for line in prompt.splitlines() if line[:1] == "f"]
            return json.dumps({"facts": [{"id": ids[0], "section": "other"}]})

    model = _use(monkeypatch, _Half())
    out = asyncio.run(memory_sort.sort_remaining(limit=2))
    assert out["sorted"] == 4 and out["remaining"] == 0

    MemoryStore().bulk_insert([_legacy("z", "ignored")])
    model = _use(monkeypatch, _Model())
    out = asyncio.run(memory_sort.sort_remaining(limit=2))
    assert len(model.calls) == 1 and out["remaining"] == 1


def test_the_loop_sorts_while_unsorted_facts_remain(isolated, monkeypatch):
    calls: list[str] = []

    async def fake_sweep(now=None):
        return 0

    async def fake_tidy(force=False):
        return {"ran": False}

    async def fake_sort(limit=25):
        calls.append("sort")
        return {"sorted": 0, "retired": 0, "remaining": 0}

    async def fake_sleep(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(memory_idle, "sweep_idle_conversations", fake_sweep)
    monkeypatch.setattr("backend.ai.memory_tidy.tidy", fake_tidy)
    monkeypatch.setattr("backend.ai.memory_sort.sort_remaining", fake_sort)
    monkeypatch.setattr(memory_idle.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(memory_idle.memory_sweep_loop())
    assert calls == []

    MemoryStore().bulk_insert([_legacy("a", "one")])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(memory_idle.memory_sweep_loop())
    assert calls == ["sort"]


def test_dry_run_writes_nothing(isolated, monkeypatch, capsys):
    from backend.scripts import migrate_memory_topics

    MemoryStore().bulk_insert([_legacy("a", "Rents a flat"), _legacy("b", "Old balance note")])
    _use(
        monkeypatch,
        _Model(
            {
                "facts": [
                    {"id": "a", "section": "money", "topic": "Housing", "content": "Rents a flat."},
                    {"id": "b", "retire": "ledger"},
                ]
            }
        ),
    )
    before = MemoryStore().all_facts()

    def no_choke(*a, **k):
        raise AssertionError("the dry run must not call the write choke")

    monkeypatch.setattr(memory_sort, "apply_memory_change", no_choke)

    plan = asyncio.run(migrate_memory_topics.run(apply=False))

    printed = capsys.readouterr().out
    assert "a | money · Housing | Rents a flat." in printed
    assert "b | retire: ledger" in printed
    assert len(plan) == 2
    assert sorted(MemoryStore().all_facts(), key=lambda f: f["id"]) == sorted(
        before, key=lambda f: f["id"]
    )


def test_apply_sorts_everything(isolated, monkeypatch, capsys):
    from backend.scripts import migrate_memory_topics

    MemoryStore().bulk_insert([_legacy("a", "Rents a flat")])
    _use(monkeypatch, _Model({"facts": [{"id": "a", "section": "money", "topic": "Housing"}]}))

    asyncio.run(migrate_memory_topics.run(apply=True))

    assert _by_id()["a"]["state"] == "live" and "0 left" in capsys.readouterr().out

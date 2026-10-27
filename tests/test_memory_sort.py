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
    assert facts["b"]["content"] == "Prefers short answers"
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


class _FilesAllButSkipped(_Model):
    """Files every shown fact except the ones whose text says "skip me"."""

    async def complete(self, prompt, **kwargs):
        await super().complete(prompt, **kwargs)
        rows = [
            line.split(": ", 1) for line in prompt.splitlines() if line[:1] in "fg" and ": " in line
        ]
        return json.dumps(
            {
                "facts": [
                    {"id": fid, "section": "other"}
                    for fid, text in rows
                    if not text.startswith("skip me")
                ]
            }
        )


def test_sort_remaining_files_what_is_behind_a_batch_it_cannot_file(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [_legacy(f"f{i:02d}", f"skip me {i}", age=100 - i) for i in range(25)]
        + [_legacy(f"g{i}", f"fileable {i}", age=50 - i) for i in range(5)]
    )
    model = _use(monkeypatch, _FilesAllButSkipped())

    out = asyncio.run(memory_sort.sort_remaining())

    assert out["sorted"] == 5 and out["remaining"] == 25
    assert len(model.calls) == 2
    assert {f["id"] for f in MemoryStore().all_facts() if f["state"] == "live"} == {
        f"g{i}" for i in range(5)
    }


def test_sort_remaining_stops_when_the_model_fails(isolated, monkeypatch):
    MemoryStore().bulk_insert([_legacy(f"f{i}", f"fact {i}", age=50 - i) for i in range(4)])
    model = _use(monkeypatch, _Model(raw="garbage"))

    out = asyncio.run(memory_sort.sort_remaining(limit=2))

    assert len(model.calls) == 1 and out["remaining"] == 4


def test_the_sweep_waits_an_hour_after_a_run_that_filed_nothing(isolated, monkeypatch):
    MemoryStore().bulk_insert([_legacy("a", "one")])
    model = _use(monkeypatch, _Model())
    clock = {"now": 1000.0}
    monkeypatch.setattr(memory_sort, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    monkeypatch.setattr(memory_sort, "_retry_after", 0.0)

    assert asyncio.run(memory_sort.sort_tick()) is not None
    clock["now"] += 3599
    assert asyncio.run(memory_sort.sort_tick()) is None
    assert len(model.calls) == 1

    clock["now"] += 2
    model = _use(monkeypatch, _Model({"facts": [{"id": "a", "section": "other"}]}))
    assert asyncio.run(memory_sort.sort_tick())["sorted"] == 1
    assert memory_sort._retry_after == 0.0


def test_the_sort_files_but_never_edits_the_words(isolated, monkeypatch):
    MemoryStore().bulk_insert([_legacy("a", "Rents a flat"), _legacy("b", "Likes tea")])
    _use(
        monkeypatch,
        _Model(
            {
                "facts": [
                    {"id": "a", "content": "Rents a flat in Tampines"},
                    {"id": "b", "section": "you", "content": "Likes tea."},
                ]
            }
        ),
    )

    asyncio.run(memory_sort.sort_unsorted())

    facts = _by_id()
    assert (facts["a"]["state"], facts["a"]["content"]) == ("unsorted", "Rents a flat")
    assert (facts["b"]["state"], facts["b"]["content"]) == ("live", "Likes tea")


def test_the_script_says_why_when_memory_is_off(isolated, monkeypatch, capsys):
    from backend.scripts import migrate_memory_topics

    MemoryStore().bulk_insert([_legacy("a", "one")])
    model = _use(monkeypatch, _Model())
    MemorySettingsStore().set_enabled(False)

    assert asyncio.run(migrate_memory_topics.run(apply=True)) == []

    assert "Generate memory from chats" in capsys.readouterr().out
    assert model.calls == [] and _by_id()["a"]["state"] == "unsorted"


def test_the_loop_sorts_while_unsorted_facts_remain(isolated, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(memory_sort, "_retry_after", 0.0)

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
    assert "a | money · Housing | (text unchanged)" in printed
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


# ── final review: filing runs the guards a new fact meets (R24, R25) ─────────


def test_the_prompt_does_not_ask_for_a_sensitive_flag(isolated, monkeypatch):
    from backend.ai import memory_structure as ms

    MemoryStore().bulk_insert([_legacy("a", "Rents a flat")])
    model = _use(monkeypatch, _Model())

    asyncio.run(memory_sort.sort_unsorted())

    prompt = model.calls[0]["prompt"]
    assert "sensitive" not in prompt.lower()
    assert ms.SENSITIVE_TOPICS not in prompt


def test_a_long_fact_is_marked_and_filed_only_with_a_shorter_wording(isolated, monkeypatch):
    long_text = "Keeps every household receipt in a shoebox and reconciles them monthly. " * 5
    short = "Keeps every household receipt and reconciles them monthly."
    MemoryStore().bulk_insert([_legacy("long", long_text.strip()), _legacy("tiny", "Likes tea")])
    model = _use(
        monkeypatch,
        _Model(
            {
                "facts": [
                    {"id": "long", "section": "money", "topic": "Bookkeeping", "content": short},
                    {"id": "tiny", "section": "you", "topic": "Preferences", "content": "Tea!"},
                ]
            }
        ),
    )

    asyncio.run(memory_sort.sort_unsorted())

    facts = _by_id()
    assert (facts["long"]["state"], facts["long"]["content"]) == ("live", short)
    assert (facts["tiny"]["state"], facts["tiny"]["content"]) == ("live", "Likes tea")
    assert "long [over 280 characters]:" in model.calls[0]["prompt"]


def test_a_long_fact_without_a_short_enough_wording_stays_unsorted(isolated, monkeypatch):
    long_text = ("Keeps every household receipt in a shoebox. " * 8).strip()
    MemoryStore().bulk_insert([_legacy("long", long_text)])
    _use(
        monkeypatch,
        _Model(
            {
                "facts": [
                    {
                        "id": "long",
                        "section": "money",
                        "topic": "Bookkeeping",
                        "content": long_text + " Also more.",
                    }
                ]
            }
        ),
    )

    out = asyncio.run(memory_sort.sort_unsorted())

    row = _by_id()["long"]
    assert (row["state"], row["content"]) == ("unsorted", long_text)
    assert out["sorted"] == 0


def test_a_legacy_sensitive_fact_is_filed_live_while_the_setting_is_off(isolated, monkeypatch):
    from backend.db.memory_settings_store import MemorySettingsStore

    assert MemorySettingsStore().sensitive_topics() is False
    MemoryStore().bulk_insert([_legacy("h", "Has a chronic condition")])
    _use(
        monkeypatch,
        _Model({"facts": [{"id": "h", "section": "you", "topic": "About you", "sensitive": True}]}),
    )

    out = asyncio.run(memory_sort.sort_unsorted())

    row = _by_id()["h"]
    assert (row["state"], row["section"]) == ("live", "you")
    assert row.get("retired_reason") is None and "sensitive" not in row
    assert out["retired"] == 0 and out["sorted"] == 1


def test_a_legacy_fact_with_an_account_number_is_retired_as_identifier(isolated, monkeypatch):
    MemoryStore().bulk_insert([_legacy("acct", "Salary lands in account 123456789012")])
    _use(
        monkeypatch,
        _Model({"facts": [{"id": "acct", "section": "money", "topic": "Income & work"}]}),
    )

    asyncio.run(memory_sort.sort_unsorted())

    assert _by_id()["acct"]["retired_reason"] == "identifier"


def test_the_sweep_side_db_work_runs_off_the_event_loop(isolated, monkeypatch):
    import threading

    from backend.ai import memory_write

    threads: dict[str, list[bool]] = {"has_unsorted": [], "choke": []}
    real_has, real_choke = memory_sort.has_unsorted, memory_write.apply_memory_change

    def spy_has():
        threads["has_unsorted"].append(threading.current_thread() is threading.main_thread())
        return real_has()

    def spy_choke(*a, **k):
        threads["choke"].append(threading.current_thread() is threading.main_thread())
        return real_choke(*a, **k)

    monkeypatch.setattr(memory_sort, "has_unsorted", spy_has)
    monkeypatch.setattr(memory_sort, "apply_memory_change", spy_choke)
    monkeypatch.setattr(memory_tidy, "apply_memory_change", spy_choke)
    monkeypatch.setattr(memory_sort, "_retry_after", 0.0)
    MemoryStore().bulk_insert([_legacy("a", "Rents a flat"), _legacy("b", "Likes tea")])
    _use(monkeypatch, _Model({"facts": [{"id": "a", "section": "money", "topic": "Housing"}]}))

    asyncio.run(memory_sort.sort_tick())
    asyncio.run(memory_tidy.tidy(force=True))

    assert threads["has_unsorted"] == [False]
    assert threads["choke"] and not any(threads["choke"])

"""A one-line Summary per memory topic: generated in the idle sweep, display-only.

Only a topic whose facts changed (no row, or a different digest) is sent; one model call covers up
to 20 topics; a reply item that is not a known dirty topic, is too long, or carries an identifier
or a long digit run is skipped and retried next sweep; a topic with no live facts left loses its
row. The API returns the rows beside the facts, deleting a topic or forgetting everything removes
them, and the export puts each one under its topic's title. ADR
``2026-10-25-memory-keeps-only-what-has-no-other-home`` amendment 8. All model calls are mocked.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.ai import memory, memory_export, memory_summaries
from backend.db import MemoryStore
from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.memory_topic_summary_store import MemoryTopicSummaryStore


@pytest.fixture(autouse=True)
def _isolated(db_isolated):
    MemorySettingsStore().set_enabled(True)
    memory_summaries._attempted.clear()
    yield db_isolated
    memory_summaries._attempted.clear()


def _ago(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _fact(fid: str, content: str, section: str = "you", topic: str | None = "Profile", **extra):
    return {
        "id": fid,
        "content": content,
        "section": section,
        "topic": topic,
        "state": "live",
        "source": "implicit",
        "expires_at": None,
        "created_at": _ago(),
        "updated_at": _ago(),
        **extra,
    }


class _Model:
    """Answers every summary request with one line per topic it was shown, unless told what to
    say. Records each prompt and the topics it listed."""

    def __init__(self, override: list[dict] | None = None, fail: bool = False):
        self.override, self.fail = override, fail
        self.prompts: list[str] = []
        self.sent: list[list[tuple[str, str]]] = []
        self.sources: list[str] = []

    async def complete(self, prompt, **kwargs):
        self.prompts.append(prompt)
        self.sources.append(kwargs.get("source"))
        topics = json.loads(prompt.split("TOPICS (JSON):", 1)[1].split("\n\nReturn ONLY", 1)[0])
        self.sent.append([(t["section"], t["topic"]) for t in topics])
        if self.fail:
            raise RuntimeError("model down")
        items = self.override
        if items is None:
            items = [
                {"section": t["section"], "topic": t["topic"], "summary": f"About {t['topic']}"}
                for t in topics
            ]
        return json.dumps({"summaries": items})


def _use(monkeypatch, model: _Model) -> _Model:
    monkeypatch.setattr(memory, "_provider", lambda: model)
    return model


def _refresh() -> dict:
    return asyncio.run(memory_summaries.refresh_summaries())


def _rows() -> dict[tuple[str, str], dict]:
    return {(r["section"], r["topic_key"]): r for r in MemoryTopicSummaryStore().all()}


def _seed(*facts: dict) -> None:
    MemoryStore().bulk_insert(list(facts))


# ── what is sent ─────────────────────────────────────────────────────────────


def test_only_dirty_topics_are_sent_and_a_fresh_row_is_not_resent(monkeypatch):
    _seed(
        _fact("m1", "Works at Marlowe Analytics as a data engineer"),
        _fact("m2", "Speaks English and Mandarin at home"),
        _fact("m3", "Rents a flat in Tampines", section="money", topic="Housing"),
    )
    model = _use(monkeypatch, _Model())

    first = _refresh()
    assert sorted(model.sent[0]) == [("money", "Housing"), ("you", "Profile")]
    assert first["written"] == 2
    assert set(_rows()) == {("you", "profile"), ("money", "housing")}
    assert _rows()[("you", "profile")]["summary"] == "About Profile"
    assert _rows()[("you", "profile")]["topic"] == "Profile"

    second = _refresh()
    assert len(model.prompts) == 1, "nothing changed, so nothing is sent"
    assert second["written"] == 0


def test_open_items_unsorted_and_retired_facts_have_no_summary(monkeypatch):
    _seed(
        _fact("m1", "Waiting for the statement", section="open_items", topic=None),
        _fact("m2", "Old legacy fact", section=None, topic=None, state="unsorted"),
        _fact("m3", "Moved away", section="money", topic="Housing", state="retired"),
    )
    model = _use(monkeypatch, _Model())

    out = _refresh()

    assert model.prompts == [] and out["written"] == 0 and _rows() == {}


def test_a_changed_fact_makes_its_topic_dirty_again(monkeypatch):
    _seed(
        _fact("m1", "Works at Marlowe Analytics"),
        _fact("m3", "Rents a flat in Tampines", section="money", topic="Housing"),
    )
    model = _use(monkeypatch, _Model())
    _refresh()

    MemoryStore().update_by_id("m1", {"content": "Works at Kestrel Labs"})
    _refresh()

    assert model.sent[1] == [("you", "Profile")]


def test_a_topic_with_no_live_facts_left_loses_its_row(monkeypatch):
    _seed(
        _fact("m1", "Works at Marlowe Analytics"),
        _fact("m3", "Rents a flat in Tampines", section="money", topic="Housing"),
    )
    _use(monkeypatch, _Model())
    _refresh()

    MemoryStore().remove_by_id("m3")
    out = _refresh()

    assert out["deleted"] == 1
    assert set(_rows()) == {("you", "profile")}


def test_one_call_for_many_topics_and_at_most_twenty(monkeypatch):
    _seed(
        *(
            _fact(f"m{i}", f"Fact number {i}", section="other", topic=f"Topic {i}")
            for i in range(25)
        )
    )
    model = _use(monkeypatch, _Model())

    first = _refresh()
    assert len(model.prompts) == 1 and len(model.sent[0]) == 20
    assert model.sources == ["memory_summary"]
    assert first["written"] == 20 and first["dirty"] == 25

    _refresh()
    assert len(model.sent[1]) == 5, "the rest go on the next sweep"


# ── what is kept from the reply ──────────────────────────────────────────────


def test_a_bad_reply_item_is_skipped_and_backs_off_until_its_facts_change(monkeypatch):
    _seed(
        _fact("m1", "Works at Marlowe Analytics"),
        _fact("m2", "Rents a flat in Tampines", section="money", topic="Housing"),
        _fact("m3", "Plans a trip to Hokkaido", section="plans", topic="Hokkaido trip"),
        _fact("m4", "Likes science fiction", section="other", topic="Reading"),
        _fact("m5", "Banks with a credit union", section="money", topic="Banking"),
    )
    model = _use(
        monkeypatch,
        _Model(
            [
                {"section": "you", "topic": "profile", "summary": "  Who they are — work  "},
                {"section": "money", "topic": "Housing", "summary": "x" * 121},
                {"section": "plans", "topic": "Hokkaido trip", "summary": "Trip saving 20261"},
                {"section": "other", "topic": "Reading", "summary": "Card 4111 1111 1111 1111"},
                {"section": "other", "topic": "Invented", "summary": "Not a topic it was shown"},
                {"section": "money", "topic": "Banking"},
                "not even an object",
            ]
        ),
    )

    out = _refresh()

    assert set(_rows()) == {("you", "profile")}
    assert _rows()[("you", "profile")]["summary"] == "Who they are — work"
    assert out["written"] == 1 and out["skipped"] >= 4

    model.override = None
    again = _refresh()
    assert len(model.prompts) == 1, "a refused topic is not re-sent with the same facts"
    assert again["sent"] == 0

    MemoryStore().update_by_id("m2", {"content": "Rents a flat in Bedok"})
    _refresh()
    assert model.sent[1] == [("money", "Housing")], "a changed topic is tried again"
    assert ("money", "housing") in _rows()


def test_a_topic_the_model_leaves_out_backs_off_and_returns_after_the_cooldown(monkeypatch):
    _seed(_fact("m1", "Has a FY2026 plan", section="plans", topic="FY2026 plan"))
    model = _use(monkeypatch, _Model([]))
    _refresh()
    _refresh()
    assert len(model.prompts) == 1

    key = ("plans", "fy2026 plan")
    digest, at = memory_summaries._attempted[key]
    memory_summaries._attempted[key] = (digest, at - memory_summaries.RETRY_COOLDOWN_SECONDS - 1)
    _refresh()
    assert len(model.prompts) == 2


def test_a_never_tried_topic_goes_ahead_of_one_that_already_failed(monkeypatch):
    _seed(
        *(
            _fact(f"m{i}", f"Fact number {i}", section="other", topic=f"Topic {i}")
            for i in range(20)
        )
    )
    model = _use(monkeypatch, _Model([]))
    _refresh()
    for key, (digest, at) in list(memory_summaries._attempted.items()):
        memory_summaries._attempted[key] = (digest, at - memory_summaries.RETRY_COOLDOWN_SECONDS)
    _seed(_fact("m_new", "A newer fact", section="other", topic="Fresh", updated_at=_ago(30)))

    _refresh()

    assert model.sent[1][0] == ("other", "Fresh") and len(model.sent[1]) == 20


def test_a_failed_or_unparseable_call_writes_nothing_and_never_raises(monkeypatch):
    _seed(_fact("m1", "Works at Marlowe Analytics"))
    _use(monkeypatch, _Model(fail=True))
    assert _refresh()["written"] == 0

    class _Garbage:
        async def complete(self, prompt, **kwargs):
            return "no json here"

    monkeypatch.setattr(memory, "_provider", lambda: _Garbage())
    assert _refresh()["written"] == 0 and _rows() == {}

    model = _use(monkeypatch, _Model())
    _refresh()
    assert len(model.prompts) == 1, "a failed call is no reason to back off"


def test_a_no_op_while_memory_is_off(monkeypatch):
    _seed(_fact("m1", "Works at Marlowe Analytics"))
    MemorySettingsStore().set_enabled(False)
    model = _use(monkeypatch, _Model())

    out = _refresh()

    assert out == {"ran": False} and model.prompts == [] and _rows() == {}


def test_the_prompt_asks_for_a_description_without_numbers(monkeypatch):
    _seed(_fact("m1", "Works at Marlowe Analytics"))
    model = _use(monkeypatch, _Model())
    _refresh()
    prompt = model.prompts[0]
    assert "90 characters" in prompt
    assert "Who they are — location, employer, background, languages" in prompt
    assert "numbers" in prompt and "Works at Marlowe Analytics" in prompt


def test_one_data_event_per_pass_that_wrote(monkeypatch):
    from backend.core import data_events

    published: list[tuple] = []
    monkeypatch.setattr(data_events, "publish", lambda domains: published.append(tuple(domains)))
    _seed(
        _fact("m1", "Works at Marlowe Analytics"),
        _fact("m2", "Rents a flat in Tampines", section="money", topic="Housing"),
    )
    _use(monkeypatch, _Model())

    _refresh()
    assert published == [("memory",)]
    _refresh()
    assert published == [("memory",)], "a pass that wrote nothing announces nothing"


def test_the_summaries_never_reach_the_system_prompt(monkeypatch):
    from backend.ai.prompts import build_system_prompt

    _seed(_fact("m1", "Works at Marlowe Analytics"))
    _use(monkeypatch, _Model([{"section": "you", "topic": "Profile", "summary": "Zebra summary"}]))
    _refresh()

    assert _rows()[("you", "profile")]["summary"] == "Zebra summary"
    assert "Zebra summary" not in build_system_prompt()


# ── the API ──────────────────────────────────────────────────────────────────


def _client() -> TestClient:
    from backend.main import app

    return TestClient(app)


def test_the_facts_route_returns_the_topics_and_delete_topic_removes_its_summary(monkeypatch):
    _seed(
        _fact("m1", "Works at Marlowe Analytics"),
        _fact("m2", "Rents a flat in Tampines", section="money", topic="Housing"),
        _fact("m3", "Pays rent on the 3rd", section="money", topic="housing", updated_at=_ago(5)),
    )
    _use(monkeypatch, _Model())
    _refresh()
    client = _client()

    body = client.get("/api/memory/facts").json()
    topics = {(t["section"], t["topic"]): t for t in body["topics"]}
    assert set(topics) == {("you", "Profile"), ("money", "Housing")}, "named as its newest fact"
    assert all(t["summary"] and t["updated_at"] for t in body["topics"])
    assert set(body["topics"][0]) == {"section", "topic", "summary", "updated_at"}

    resp = client.delete("/api/memory/topics", params={"section": "money", "topic": "HOUSING"})
    assert resp.status_code == 200
    assert set(_rows()) == {("you", "profile")}


def test_forget_all_clears_every_summary(monkeypatch):
    _seed(
        _fact("m1", "Works at Marlowe Analytics"),
        _fact("m2", "Rents a flat in Tampines", section="money", topic="Housing"),
    )
    _use(monkeypatch, _Model())
    _refresh()

    assert _client().delete("/api/memory/facts").json()["deleted"] == 2
    assert _rows() == {}


# ── the export ───────────────────────────────────────────────────────────────


def test_forgetting_a_topics_last_fact_with_memory_off_drops_its_summary_at_once(monkeypatch):
    _seed(
        _fact("m1", "Works at Marlowe Analytics"),
        _fact("m2", "Rents a flat in Tampines", section="money", topic="Housing"),
    )
    _use(monkeypatch, _Model())
    _refresh()
    MemorySettingsStore().set_enabled(False)
    client = _client()

    assert client.delete("/api/memory/facts/m2").status_code == 200

    assert set(_rows()) == {("you", "profile")}
    topics = client.get("/api/memory/facts").json()["topics"]
    assert [(t["section"], t["topic"]) for t in topics] == [("you", "Profile")]


def test_the_forget_fact_tool_and_a_retire_drop_an_emptied_topics_summary(monkeypatch):
    from backend.ai.memory_write import apply_memory_change

    _seed(
        _fact("m1", "Works at Marlowe Analytics"),
        _fact("m2", "Rents a flat in Tampines", section="money", topic="Housing"),
    )
    _use(monkeypatch, _Model())
    _refresh()

    apply_memory_change(
        {"retire": [{"id": "m1", "reason": "stale"}]}, source="implicit", strict=False
    )
    assert set(_rows()) == {("money", "housing")}


def test_get_and_export_hide_a_summary_whose_topic_has_no_live_facts(monkeypatch):
    _seed(_fact("m1", "Works at Marlowe Analytics"))
    _use(monkeypatch, _Model([{"section": "you", "topic": "Profile", "summary": "Who they are"}]))
    _refresh()
    MemoryStore().remove_by_id("m1")  # bypasses the choke: the row is still stored

    assert set(_rows()) == {("you", "profile")}
    assert _client().get("/api/memory/facts").json()["topics"] == []
    assert "Who they are" not in "".join(_files(memory_export.build_memory_export()).values())


def test_the_sweep_prune_runs_with_memory_off(monkeypatch):
    from backend.ai import memory_idle

    _seed(_fact("m1", "Works at Marlowe Analytics"))
    _use(monkeypatch, _Model())
    _refresh()
    MemoryStore().remove_by_id("m1")
    MemorySettingsStore().set_enabled(False)

    asyncio.run(memory_idle.prune_topic_summaries())

    assert _rows() == {}


def test_a_reset_or_whole_book_import_wipes_the_summaries(monkeypatch):
    from backend.db import portable_bridge
    from backend.db.engine import get_engine

    _seed(_fact("m1", "Works at Marlowe Analytics"))
    _use(monkeypatch, _Model())
    _refresh()
    assert _rows()

    with get_engine().begin() as conn:
        portable_bridge._wipe_memory(conn)

    assert _rows() == {} and MemoryStore().all_facts() == []


def _files(blob: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return {n: archive.read(n).decode() for n in archive.namelist()}


def test_the_export_puts_the_summary_under_the_title(monkeypatch):
    _seed(
        _fact("m1", "Works at Marlowe Analytics"),
        _fact("m2", "Rents a flat in Tampines", section="money", topic="Housing"),
    )
    _use(monkeypatch, _Model([{"section": "you", "topic": "Profile", "summary": "Who they are"}]))
    _refresh()

    files = _files(memory_export.build_memory_export())

    assert files["you-profile.md"].startswith("# You · Profile\n\n_Who they are_\n\n- Works at")
    assert files["money-housing.md"].startswith("# Money · Housing\n\n- Rents")


def test_the_sweep_step_never_raises(monkeypatch):
    from backend.ai import memory_idle

    async def boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(memory_summaries, "refresh_summaries", boom)
    asyncio.run(memory_idle.refresh_topic_summaries())

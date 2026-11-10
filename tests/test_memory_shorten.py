"""An extracted fact over the hard limit gets one shorten retry instead of being dropped.
Synthetic conversations; the model is mocked."""

from __future__ import annotations

import asyncio
import json

from backend.ai import memory
from backend.ai import memory_structure as ms
from backend.ai.memory_shorten import shorten_long_facts
from backend.db import MemoryStore
from backend.db.memory_settings_store import MemorySettingsStore

QUOTE = "I applied, waiting for the result"
HISTORY = [
    {"role": "user", "content": "Should I take the Northwind personal loan?"},
    {"role": "assistant", "content": "Yes, the 24-month offer is the cheaper one."},
    {"role": "user", "content": f"Ok. {QUOTE} in the coming days."},
]
LONG = (
    "Applied for the Northwind personal loan (about $40,000 over 24 months) to fund the "
    "renovation instalments; the result is still to come. The next instalment falls due on "
    "the 20th of next month. Once approved, give the approved amount, rate and total interest "
    "so the funding plan can be updated. " * 2
).strip()
SHORT = "Applied for the Northwind personal loan (about $40,000 over 24 months); awaiting a result."


class _Model:
    """Replies to the extraction call, then to each shorten call, in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def complete(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return json.dumps(self.replies.pop(0))


def _extracted(content: str) -> dict:
    item = {"content": content, "section": "open_items", "quote": QUOTE}
    return {"new": [item], "updates": [], "remove": [], "mentioned": []}


def _run(monkeypatch, model: _Model) -> list[str]:
    MemorySettingsStore().set_enabled(True)
    monkeypatch.setattr(memory, "_provider", lambda: model)
    asyncio.run(memory.extract_and_merge(HISTORY))
    return [f["content"] for f in MemoryStore().all_facts()]


def test_a_fact_over_the_limit_is_shortened_once_and_saved(db_isolated, monkeypatch):
    assert len(LONG) > ms.FACT_MAX_CHARS
    model = _Model(_extracted(LONG), {"short": [{"n": 0, "content": SHORT}]})

    assert _run(monkeypatch, model) == [SHORT]
    assert [c["source"] for c in model.calls] == ["memory_extract", "memory_shorten"]
    assert LONG in model.calls[1]["prompt"]


def test_a_fact_still_too_long_after_the_retry_is_refused_and_its_text_logged(
    db_isolated, monkeypatch, caplog
):
    model = _Model(_extracted(LONG), {"short": [{"n": 0, "content": LONG + " More."}]})

    with caplog.at_level("WARNING", logger="backend.ai.memory_shorten"):
        assert _run(monkeypatch, model) == []
    assert LONG in caplog.text


def test_a_fact_within_the_limit_costs_no_extra_call(db_isolated, monkeypatch):
    model = _Model(_extracted(SHORT))

    assert _run(monkeypatch, model) == [SHORT]
    assert len(model.calls) == 1


def test_a_failed_shorten_call_leaves_the_ops_for_the_choke():
    async def boom(prompt):
        raise RuntimeError("provider down")

    ops = {"new": [{"content": LONG}], "updates": [{"id": "m_1", "content": "Fine"}]}
    out = asyncio.run(shorten_long_facts(ops, boom))
    assert out["new"][0]["content"] == LONG and out["updates"][0]["content"] == "Fine"


def test_updates_are_shortened_too_and_a_bad_reply_index_changes_nothing():
    async def ask(prompt):
        return {"short": [{"n": 0, "content": SHORT}, {"n": 7, "content": "stray"}]}

    ops = {"new": [], "updates": [{"id": "m_1", "content": LONG}]}
    assert asyncio.run(shorten_long_facts(ops, ask))["updates"][0]["content"] == SHORT

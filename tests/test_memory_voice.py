"""The house voice and the guard on rewording an old fact into it, plus the sort's use of it
(amendment 9). Synthetic facts; the model is mocked."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import memory, memory_sort
from backend.ai.memory_voice import keeps_facts, off_voice
from backend.db import MemoryStore
from backend.db.memory_settings_store import MemorySettingsStore


@pytest.mark.parametrize(
    "text",
    [
        "User wants shared-expense requests itemised",
        "The user's card is used for groceries",
        "Advisor suggested an ETF core",
        "As of 2026-09-30: rent is paid on the 1st",
        "Wants the client's statements first",
    ],
)
def test_old_framing_is_off_voice(text):
    assert off_voice(text)


@pytest.mark.parametrize(
    "text",
    ["Splits rent with Priya 60/40", "Prefers short answers", "Has a username on the forum"],
)
def test_the_house_voice_is_not(text):
    assert not off_voice(text)


OLD = (
    "User splits the S$2,400 rent with Priya (Priya Raman) 60/40 since 2026-03-01; 'Rent' category."
)


def test_a_rewording_that_keeps_every_number_name_and_quote_passes():
    new = (
        "Splits the S$2,400 rent with Priya (Priya Raman) 60/40 since 2026-03-01; 'Rent' category."
    )
    assert keeps_facts(OLD, new)


@pytest.mark.parametrize(
    "new",
    [
        "Splits the S$2,300 rent with Priya (Priya Raman) 60/40 since 2026-03-01; 'Rent' category.",
        "Splits the S$2,400 rent with Priya 60/40 since 2026-03-01; 'Rent' category.",
        "Splits the S$2,400 rent with Priya (Priya Raman) 60/40 since 1 Mar 2026; 'Rent' category.",
        "Splits the S$2,400 rent with Priya (Priya Raman) 60/40 since 2026-03-01; 'Rental' category.",
        "User splits the S$2,400 rent with Priya (Priya Raman) 60/40 since 2026-03-01; 'Rent'.",
        "x" * 501,
        "",
    ],
    ids=["number", "name", "date-format", "quote", "still-off-voice", "too-long", "empty"],
)
def test_a_rewording_that_changes_what_the_fact_says_is_refused(new):
    assert not keeps_facts(OLD, new)


@pytest.mark.parametrize(
    ("old", "new", "kept"),
    [
        ("User's flatmate Priya doesn't pay on time", "Flatmate Priya doesn't pay on time", True),
        ("As of 2026-09-30: prefers short answers", "Prefers short answers", True),
        ("User prefers 'Rent' as the category", "Prefers 'Rent' as the category", True),
        ("User's flatmate pays S$540", "Flatmate Priya pays S$540", False),
        ("User does not want a joint account", "Wants a joint account", False),
    ],
    ids=["apostrophes", "leading-as-of", "quoted", "invented-name", "lost-negation"],
)
def test_review_cases(old, new, kept):
    assert keeps_facts(old, new) is kept


@pytest.fixture
def isolated(db_isolated):
    MemorySettingsStore().set_enabled(True)


def _legacy(fid: str, content: str) -> dict:
    return {
        "id": fid,
        "content": content,
        "state": "unsorted",
        "source": "implicit",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


class _Model:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    async def complete(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return json.dumps(self.reply)


def _sort(monkeypatch, reply) -> tuple[dict, _Model]:
    model = _Model(reply)
    monkeypatch.setattr(memory, "_provider", lambda: model)
    asyncio.run(memory_sort.sort_unsorted())
    return {f["id"]: f for f in MemoryStore().all_facts()}, model


def test_the_sort_marks_an_old_framing_note_and_files_a_guarded_rewording(isolated, monkeypatch):
    MemoryStore().bulk_insert(
        [
            _legacy("a", "User wants requests to Priya itemised"),
            _legacy("b", "Prefers short answers"),
        ]
    )
    reply = {
        "facts": [
            {
                "id": "a",
                "section": "money",
                "topic": "Housing",
                "content": "Wants requests to Priya itemised",
            },
            {"id": "b", "section": "you", "topic": "Preferences", "content": "Likes it brief"},
        ]
    }
    after, model = _sort(monkeypatch, reply)
    assert "a [reword]: User wants" in model.prompts[0]
    assert "b: Prefers short answers" in model.prompts[0]
    assert after["a"]["content"] == "Wants requests to Priya itemised"
    assert after["b"]["content"] == "Prefers short answers"


def test_a_rewording_that_drops_a_name_keeps_the_old_words(isolated, monkeypatch):
    MemoryStore().bulk_insert([_legacy("a", "User wants requests to Priya itemised")])
    reply = {
        "facts": [
            {
                "id": "a",
                "section": "money",
                "topic": "Housing",
                "content": "Wants requests itemised",
            }
        ]
    }
    after, _ = _sort(monkeypatch, reply)
    assert (after["a"]["state"], after["a"]["content"]) == (
        "live",
        "User wants requests to Priya itemised",
    )


def test_the_sort_never_files_an_open_item(isolated, monkeypatch):
    MemoryStore().bulk_insert([_legacy("a", "Statement line still to match against the ledger")])
    after, model = _sort(monkeypatch, {"facts": [{"id": "a", "section": "open_items"}]})
    assert '"open_items"' not in model.prompts[0]
    assert (after["a"]["state"], after["a"]["retired_reason"]) == ("retired", "stale")

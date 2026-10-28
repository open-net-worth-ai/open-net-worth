"""An Open item needs the person's own words (amendment 9): the guard, and the extractor using it.
Synthetic conversations; the model is mocked."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import memory
from backend.ai.memory_open_items import guard, user_words
from backend.db import MemoryStore
from backend.db.memory_settings_store import MemorySettingsStore

HISTORY = [
    {"role": "user", "content": "Remind me next week to check the PayPal login alert."},
    {"role": "assistant", "content": "Want me to set a stop-loss on RIVN as well?"},
    {"role": "user", "content": "Hmm, maybe."},
]
SAID = user_words(HISTORY)


def _open(content: str, quote: str | None) -> dict:
    return {"content": content, "section": "open_items", "quote": quote}


def test_an_open_item_quoting_the_person_is_kept_and_the_quote_stripped():
    ops = guard(
        {"new": [_open("Check the PayPal login alert", "remind me next week to check the PayPal")]},
        SAID,
        {},
    )
    assert ops["new"] == [{"content": "Check the PayPal login alert", "section": "open_items"}]


@pytest.mark.parametrize(
    "quote",
    [None, "", "maybe", "Want me to set a stop-loss on RIVN as well?", "I'll call the insurer"],
    ids=["none", "empty", "too-short", "only-lucius-said-it", "never-said"],
)
def test_an_open_item_without_the_persons_words_is_dropped(quote):
    assert guard({"new": [_open("Set a stop-loss on RIVN", quote)]}, SAID, {})["new"] == []


def test_one_new_open_item_per_conversation_and_other_sections_pass():
    q = "Remind me next week to check the PayPal login alert"
    ops = guard(
        {
            "new": [
                _open("Check the PayPal login alert", q),
                _open("Check it again", q),
                {"content": "Prefers short answers", "section": "you", "quote": None},
            ]
        },
        SAID,
        {},
    )
    assert [i["content"] for i in ops["new"]] == [
        "Check the PayPal login alert",
        "Prefers short answers",
    ]
    assert all("quote" not in i for i in ops["new"])


def test_moving_a_fact_into_open_items_needs_a_quote_but_editing_one_does_not():
    current = {"p": {"id": "p", "section": "plans"}, "o": {"id": "o", "section": "open_items"}}
    ops = guard(
        {
            "updates": [
                {"id": "p", "section": "open_items", "content": "Chase the refund"},
                {"id": "o", "section": "open_items", "content": "Check the PayPal alert by Friday"},
            ]
        },
        SAID,
        current,
    )
    assert [u["id"] for u in ops["updates"]] == ["o"]


class _Model:
    def __init__(self, reply):
        self.reply = reply

    async def complete(self, prompt, **kwargs):
        return json.dumps(self.reply)


def test_the_extractor_drops_an_open_item_lucius_proposed(db_isolated, monkeypatch):
    MemorySettingsStore().set_enabled(True)
    reply = {
        "new": [
            _open("Decide on stop-losses for RIVN", "Want me to set a stop-loss on RIVN as well?"),
            _open(
                "Check the PayPal login alert",
                "Remind me next week to check the PayPal login alert",
            ),
        ],
        "updates": [],
        "remove": [],
        "mentioned": [],
    }
    monkeypatch.setattr(memory, "_provider", lambda: _Model(reply))
    asyncio.run(memory.extract_and_merge(HISTORY))
    (fact,) = MemoryStore().all_facts()
    assert (fact["content"], fact["section"]) == ("Check the PayPal login alert", "open_items")
    assert "quote" not in fact


def test_a_quote_that_shares_no_word_with_the_item_grounds_nothing():
    history = [
        {"role": "assistant", "content": "Shall I link the Lotus Bistro line to the 28 Sep entry?"},
        {"role": "user", "content": "Hmm, not sure about that one yet."},
    ]
    item = _open("Decide whether to link the Lotus Bistro line", "Hmm, not sure about that one yet")
    assert guard({"new": [item]}, user_words(history), {})["new"] == []


def test_an_attachment_or_harness_note_is_not_the_persons_words():
    history = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "[Attached file: bill.pdf] Please pay the invoice by Friday",
                },
                {
                    "type": "text",
                    "text": "«harness_note» remind me to renew the domain «/harness_note»",
                },
            ],
        }
    ]
    said = user_words(history)
    for quote, content in (
        ("Please pay the invoice by Friday", "Pay the invoice by Friday"),
        ("remind me to renew the domain", "Renew the domain"),
    ):
        assert guard({"new": [_open(content, quote)]}, said, {})["new"] == []


def test_an_update_without_an_id_does_not_break_the_pass():
    ops = guard({"updates": [{"section": "open_items", "content": "Chase the refund"}]}, SAID, {})
    assert ops["updates"] == []

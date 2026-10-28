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


def test_three_new_recent_entries_per_pass_and_other_sections_pass():
    q = "Remind me next week to check the PayPal login alert"
    items = [_open(f"Check the PayPal login alert, step {n}", q) for n in range(1, 5)]
    items.append({"content": "Prefers short answers", "section": "you", "quote": None})
    ops = guard({"new": items}, SAID, {})
    kept = [i["content"] for i in ops["new"]]
    assert kept == [
        "Check the PayPal login alert, step 1",
        "Check the PayPal login alert, step 2",
        "Check the PayPal login alert, step 3",
        "Prefers short answers",
    ]
    assert all("quote" not in i for i in ops["new"])


def test_an_automation_runs_prompt_never_grounds_a_recent_entry_but_a_typed_follow_up_does(
    db_isolated, monkeypatch
):
    MemorySettingsStore().set_enabled(True)
    history = [
        {"role": "user", "content": "Remind me to reconcile the card statement every Friday."},
        {"role": "assistant", "content": "Reconciled the card statement."},
        {"role": "user", "content": "Also remind me to chase Priya for the deposit on Friday."},
    ]
    reply = {
        "new": [
            _open("Reconcile the card statement", "Remind me to reconcile the card statement"),
            _open("Chase Priya for the deposit", "remind me to chase Priya for the deposit"),
        ],
        "updates": [],
        "remove": [],
        "mentioned": [],
    }
    monkeypatch.setattr(memory, "_provider", lambda: _Model(reply))
    asyncio.run(memory.extract_and_merge(history, run_prompt=True))
    assert [f["content"] for f in MemoryStore().all_facts()] == ["Chase Priya for the deposit"]


def test_an_automation_session_is_extracted_with_its_prompt_left_out(db_isolated, monkeypatch):
    from backend.ai import memory_idle
    from backend.db.session_store import SessionStore

    MemorySettingsStore().set_enabled(True)
    seen: dict = {}

    async def fake(history, session_id=None, *, run_prompt=False):
        seen[session_id] = run_prompt

    monkeypatch.setattr(memory, "extract_and_merge", fake)
    for sid, meta in (("automation-run-r1", {}), ("chat-1", {}), ("x-2", {"source": "automation"})):
        SessionStore().upsert_meta(sid, {"title": sid, **meta})
        SessionStore().append_messages(sid, [{"role": "user", "content": "hello there"}])
        asyncio.run(memory_idle.extract_session(sid))
    assert seen == {"automation-run-r1": True, "chat-1": False, "x-2": True}


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


TYPED = user_words(
    [
        {
            "role": "user",
            "content": "ok, for utliities since he only wnet for 2 days -> put $5 only",
        }
    ]
)


def test_a_quote_the_model_tidied_of_the_persons_typos_still_grounds():
    """Live 2026-10-29: the model quoted "utilities ... went" for the person's "utliities ...
    wnet", and the exact match dropped the one correct entry of the chat."""
    item = _open(
        "When Sam's PayNow arrives: book Utilities S$5",
        "for utilities since he only went for 2 days",
    )
    assert guard({"new": [item]}, TYPED, {})["new"] == [
        {"content": "When Sam's PayNow arrives: book Utilities S$5", "section": "open_items"}
    ]


@pytest.mark.parametrize(
    "quote",
    ["for groceries since she stayed for 3 weeks", "put the rest on the card"],
    ids=["same-shape-other-words", "never-said"],
)
def test_a_tidied_quote_is_still_held_to_what_the_person_typed(quote):
    item = _open("When Sam's PayNow arrives: book Utilities S$5", quote)
    assert guard({"new": [item]}, TYPED, {})["new"] == []


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


def test_a_quote_that_points_back_is_grounded_by_its_own_message():
    history = [
        {
            "role": "user",
            "content": "I haven't decided whether to dispute the deposit deduction. "
            "Can we come back to that next week?",
        }
    ]
    item = _open(
        "Decide whether to dispute the deposit deduction", "Can we come back to that next week?"
    )
    assert guard({"new": [item]}, user_words(history), {})["new"][0]["content"].startswith("Decide")


# ── Recent context (amendment 10): read by every run, closed by the run that acts on it ──────


def _live_fact(fid: str, content: str, section: str, age_minutes: int = 0) -> dict:
    stamp = f"2026-10-28T{10 + age_minutes // 60:02d}:{age_minutes % 60:02d}:00+00:00"
    return {
        "id": fid,
        "content": content,
        "section": section,
        "topic": None if section == "open_items" else "Housing",
        "state": "live",
        "source": "implicit",
        "expires_at": "2026-11-11" if section == "open_items" else None,
        "created_at": stamp,
        "updated_at": stamp,
    }


def test_a_run_closes_the_recent_entry_it_acted_on_by_its_handle(db_isolated):
    from backend.ai.memory_open_items import close_recent
    from backend.ai.memory_render import fact_handle

    MemoryStore().bulk_insert(
        [
            _live_fact(
                "mem_aa11bb22cc33", "When Priya's PayNow arrives, book Rent S$500", "open_items"
            ),
            _live_fact("mem_dd44ee55ff66", "Splits rent with Priya", "money"),
        ]
    )
    closed = close_recent([fact_handle("mem_aa11bb22cc33"), fact_handle("mem_dd44ee55ff66")])
    assert closed == ["When Priya's PayNow arrives, book Rent S$500"]
    rows = {f["id"]: f for f in MemoryStore().all_facts()}
    assert (rows["mem_aa11bb22cc33"]["state"], rows["mem_aa11bb22cc33"]["retired_reason"]) == (
        "retired",
        "resolved",
    )
    assert rows["mem_dd44ee55ff66"]["state"] == "live"
    assert close_recent(["mzzzzzz"]) == []


def test_past_ten_live_recent_entries_the_oldest_lapse(db_isolated):
    MemorySettingsStore().set_enabled(True)
    MemoryStore().bulk_insert(
        [_live_fact(f"mem_r{n:02d}", f"Entry {n}", "open_items", age_minutes=n) for n in range(12)]
    )
    memory.purge_expired(MemoryStore())
    live = sorted(f["id"] for f in MemoryStore().all_facts() if f["state"] == "live")
    assert live == [f"mem_r{n:02d}" for n in range(2, 12)]


def test_recent_leads_every_runs_memory_block_with_how_to_use_it():
    from backend.ai.memory_render import RECENT_HEAD, render_memory_block

    facts = [
        _live_fact("mem_aa11bb22cc33", "Splits rent with Priya", "money"),
        _live_fact(
            "mem_dd44ee55ff66", "When Priya's PayNow arrives, book Rent S$500", "open_items"
        ),
    ]
    text, _ = render_memory_block(facts)
    assert text.index(RECENT_HEAD) < text.index("Money:")
    assert "close_recent_items" in RECENT_HEAD
    briefing, _ = render_memory_block(facts, handles=False)
    assert "Recent:" in briefing and "close_recent_items" not in briefing


def test_an_expired_entry_holds_no_slot_under_the_cap(db_isolated):
    MemorySettingsStore().set_enabled(True)
    rows = [
        _live_fact(f"mem_r{n:02d}", f"Entry {n}", "open_items", age_minutes=n) for n in range(11)
    ]
    gone = _live_fact("mem_old", "Expired but recently edited", "open_items", age_minutes=59)
    gone["expires_at"] = "2020-01-01"
    MemoryStore().bulk_insert([*rows, gone])
    memory.purge_expired(MemoryStore())
    live = sorted(f["id"] for f in MemoryStore().all_facts() if f["state"] == "live")
    assert live == [f"mem_r{n:02d}" for n in range(1, 11)]


def test_a_run_cannot_add_a_recent_entry_with_remember_fact(db_isolated):
    from backend.ai.tools.memory import remember_fact
    from backend.core.chat_session import clear_session_id, set_session_id

    MemorySettingsStore().set_enabled(True)
    args = {"content": "Book every PayNow as Gifts", "section": "open_items"}
    try:
        set_session_id("capture-1a2b3c4d")
        refused = json.loads(asyncio.run(remember_fact(args))[0])
        set_session_id("chat-in-the-app")
        saved = json.loads(asyncio.run(remember_fact(args))[0])
    finally:
        clear_session_id()
    assert refused["ok"] is False and "close_recent_items" in refused["message"]
    assert saved["ok"] is True


def test_close_recent_items_says_when_nothing_matched(db_isolated):
    from backend.ai.tools.memory import close_recent_items

    MemorySettingsStore().set_enabled(True)
    out = json.loads(asyncio.run(close_recent_items({"handles": ["mzzzzzz"]}))[0])
    assert out == {"ok": False, "message": "No live Recent entry matches those handles."}

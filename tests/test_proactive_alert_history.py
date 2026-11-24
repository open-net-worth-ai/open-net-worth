"""A proactive alert is an ORDINARY message, appended when it is sent.

Owner-driven rewrite, 2026-08-24. Everything this file used to pin — coalescing by
``subject_key``, an admission budget, a synthetic "[Automated monitor]" trigger turn, a
per-event character cap, injection at the next turn's start — is GONE, and the defects that
machinery caused went with it:

  * the count cap sliced in arrival order and discarded the NEWEST alerts, so the owner's
    "Mail scan" alert never reached the model and Lucius said it could not find it;
  * the 600-char record cap cut 12 of 15 live alerts mid-URL;
  * injection stamped every alert with the moment the user next replied, not when it was
    sent;
  * the synthetic trigger turn NAMED a conversation ("[Automated monitor — the following
    message was delivered to").

All of it existed to dodge one storage fact: ``sessions.transcript`` was a single JSON
column a running turn rewrote wholesale, so an alert arriving mid-turn would be clobbered.
It could not be appended, so it was summarised. Since the transcript became
``session_messages`` (revisions 0051-0056) an append is a plain INSERT with a
database-assigned key, and ``record_proactive_message`` takes the same session lock a turn
already holds.
"""

from __future__ import annotations

import pytest

from backend.ai import assistant
from backend.ai.tool_text import harness_note, is_harness_note
from backend.db.session_io import _recovered_title


@pytest.mark.asyncio
async def test_an_alert_is_appended_as_an_ordinary_assistant_message(db_isolated):
    sid = "sess-alert"
    assistant.save_session_durable(sid, [{"role": "user", "content": "hi"}])
    await assistant.record_proactive_message(sid, "BTC crossed $90,000.")

    history = assistant._load_session(sid) or []
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[-1]["content"] == "BTC crossed $90,000."
    assert "harness_note" not in str(history[-1]["content"]), "no synthetic trigger turn"


@pytest.mark.asyncio
async def test_a_long_alert_is_never_truncated(db_isolated):
    """The 600-char record cap cut 12 of the owner's 15 live alerts, routinely mid-URL."""
    sid = "sess-long"
    assistant.save_session_durable(sid, [{"role": "user", "content": "hi"}])
    body = "A" * 20_000
    await assistant.record_proactive_message(sid, body)
    assert (assistant._load_session(sid) or [])[-1]["content"] == body


@pytest.mark.asyncio
async def test_every_alert_arrives_and_none_is_coalesced_away(db_isolated):
    """36 distinct alerts, the owner's live case. The old count cap admitted 20 in ARRIVAL
    order and dropped the 16 NEWEST — including the one they immediately asked about."""
    sid = "sess-many"
    assistant.save_session_durable(sid, [{"role": "user", "content": "hi"}])
    for i in range(36):
        await assistant.record_proactive_message(sid, f"alert-{i}", subject_key=f"k{i}")
    bodies = [
        m["content"] for m in (assistant._load_session(sid) or []) if m["role"] == "assistant"
    ]
    assert len(bodies) == 36
    assert bodies[0] == "alert-0" and bodies[-1] == "alert-35"


@pytest.mark.asyncio
async def test_repeats_of_one_subject_are_all_kept(db_isolated):
    """Coalescing collapsed repeats into one line with a count. They are separate messages
    now — the user really did receive each one."""
    sid = "sess-rep"
    assistant.save_session_durable(sid, [{"role": "user", "content": "hi"}])
    for i in range(5):
        await assistant.record_proactive_message(sid, f"BTC update {i}", subject_key="btc")
    assert len([m for m in (assistant._load_session(sid) or []) if m["role"] == "assistant"]) == 5


@pytest.mark.asyncio
async def test_an_alert_carries_its_provenance(db_isolated):
    sid = "sess-prov"
    assistant.save_session_durable(sid, [{"role": "user", "content": "hi"}])
    await assistant.record_proactive_message(
        sid, "NVDA down 6%", subject_key="nvda", report_ref="rep_1"
    )
    last = (assistant._load_session(sid) or [])[-1]
    assert last["_onw_proactive"] == {"subject_key": "nvda", "report_ref": "rep_1"}


@pytest.mark.asyncio
async def test_an_alert_is_stamped_when_it_is_SENT(db_isolated):
    """Injection used to stamp every alert with the moment the user next replied."""
    sid = "sess-when"
    assistant.save_session_durable(sid, [{"role": "user", "content": "hi"}])
    await assistant.record_proactive_message(sid, "sent now")
    assert isinstance((assistant._load_session(sid) or [])[-1].get("ts"), int)


@pytest.mark.asyncio
async def test_an_empty_alert_records_nothing(db_isolated):
    sid = "sess-empty"
    assistant.save_session_durable(sid, [{"role": "user", "content": "hi"}])
    await assistant.record_proactive_message(sid, "   ")
    assert len(assistant._load_session(sid) or []) == 1


# ── harness notes (unchanged behaviour, still pinned) ─────────────────────────────────


def test_a_system_note_is_harness_authored():
    assert is_harness_note(harness_note("[System: x]"))
    assert is_harness_note("[System: User confirmed and applied via chat: Book SGD 3.95]")
    assert not is_harness_note("what's my net worth?")


def test_a_harness_note_never_names_a_conversation():
    """The old synthetic trigger turn titled the owner's real Telegram conversation."""
    assert (
        _recovered_title(
            [
                {
                    "role": "user",
                    "content": harness_note("[System: User confirmed and applied via chat: x]"),
                },
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "what should I do?"},
            ]
        )
        == "what should I do?"
    )


@pytest.mark.asyncio
async def test_an_alert_reaches_a_conversation_already_cached_in_memory(db_isolated):
    """Live 2026-11-24: a Telegram conversation stayed in ``SESSIONS`` from yesterday's turn.
    The 12:02 briefing was appended to the DB only, so the next turn read the stale cached
    list — the model answered "yes, I transferred" about an old receipt — and that turn's
    save diffed the stale list against the DB and TRUNCATED the briefing away."""
    sid = "sess-cached"
    assistant.save_session_durable(sid, [{"role": "user", "content": "hi"}])
    _, cached = assistant.get_or_create_session(sid)  # an earlier turn cached it

    await assistant.record_proactive_message(sid, "Your card bill is due 30 Nov.")

    _, history = assistant.get_or_create_session(sid)  # the next turn
    assert history is cached, "the turn's live list keeps its identity"
    assert history[-1]["content"] == "Your card bill is due 30 Nov."

    history.append({"role": "user", "content": "Yah"})
    assistant._save_session(sid, history)
    stored = [m["content"] for m in assistant._load_session(sid) or []]
    assert stored == ["hi", "Your card bill is due 30 Nov.", "Yah"]

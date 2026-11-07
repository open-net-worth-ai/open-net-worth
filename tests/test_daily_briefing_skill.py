"""Regression pins for the daily-briefing starter skill."""

from __future__ import annotations

from backend.db.skill_store import STARTER_SKILLS

_RAW = next(s for s in STARTER_SKILLS if s["id"] == "daily-briefing")["body"]
BODY = " ".join(_RAW.split())


def test_needs_a_look_never_asks_again_what_they_have_already_done():
    # A briefing listed a charge the owner had already disputed with their bank as "still
    # uncategorized", two mornings running, with the dispute in its memory all along.
    assert "**Needs a look:**" in BODY
    assert "Check each one against what you remember first" in BODY
    assert "leave out what they've finished with" in BODY
    assert "give where it stands instead of asking them again" in BODY


def test_what_is_waiting_on_someone_gets_a_line_and_a_reminder_to_recheck():
    # The owner minded the wrong message, not hearing about it daily. Leaving the dispute out
    # stopped the wrong message, but then nothing told them to check for the reversal.
    assert "**Waiting on someone:**" in BODY
    assert "a dispute, a refund, a claim or an application" in BODY
    assert "Check the books for the outcome with `get_transactions` first" in BODY
    assert "If it's settled, say so once." in BODY
    assert "say where it stands in one short sentence" in BODY
    assert "Once its date has arrived, or a week has passed if it has none" in BODY
    assert "remind them what to recheck and with whom" in BODY


def test_where_it_stands_is_a_sentence_not_a_labelled_field():
    # Replays of the first wording opened the line with a literal "Still open:", copied from the
    # skill's own bold label, in a message meant to read as natural sentences.
    assert "nothing in front of it to label it" in BODY
    assert "Still open" not in BODY

"""Harness-authored messages are marked, hidden from the reader, and never name a chat.

Four live bugs, one missing distinction — a message the harness wrote is indistinguishable
from one the owner typed:

  * the completion nudge rendered as the owner's own message (2026-08-12)
  * a continuation re-declared its plan, forking the run's task list (2026-08-14)
  * attachment refs surfaced as raw message text (2026-08-16)
  * a proactive-alert marker became a month-old conversation's TITLE and dragged it to the
    top of the chat list (2026-08-21)

The wire role stays `user` — the only role every transport has. Anthropic grew a real
`system` message role, but not on Sonnet 5, and the CLI transports flatten to a text
transcript with no system channel at all. The marker is the portable common denominator.
"""

from __future__ import annotations

import json

import pytest

import backend.ai.assistant as assistant
from backend.ai.tool_text import harness_note, is_harness_note


def test_a_harness_note_is_recognisable_and_ordinary_text_is_not():
    assert is_harness_note(harness_note("You are not finished."))
    assert not is_harness_note("You are not finished.")
    assert not is_harness_note("")


def test_the_note_still_carries_its_text_to_the_model():
    """Hidden from the READER, never from the model — it is real context for the turn."""
    assert "You are not finished." in harness_note("You are not finished.")


def test_a_conversation_is_never_named_after_a_harness_message():
    """The 2026-08-21 bug: `[Automated monitor — …]`, instructions meant only for the model,
    shown to the owner as the name of their own thread."""
    preview = assistant._extract_user_preview(
        [
            {"role": "user", "content": harness_note("[Automated monitor — …]")},
            {"role": "user", "content": "What is your model? And who are you?"},
        ]
    )
    assert preview == "What is your model? And who are you?"


def test_a_real_message_still_names_the_conversation():
    assert (
        assistant._extract_user_preview([{"role": "user", "content": "scan the mailboxes"}])
        == "scan the mailboxes"
    )


@pytest.mark.parametrize(
    "content",
    [
        harness_note("You are not finished: 5 of 5 tasks are still open."),
        [{"type": "text", "text": harness_note("[Automated monitor — …]")}],
    ],
    ids=["string-form", "block-form"],
)
def test_a_harness_note_is_not_drawn_as_the_owners_message(content, monkeypatch, db_isolated):
    sid = "test-harness-note"
    assistant._store().upsert_meta(sid, {"id": sid})
    monkeypatch.setattr(assistant, "_rehydrated_turn_usage", lambda *a, **k: {})
    monkeypatch.setattr(
        assistant,
        "_load_session",
        lambda _s: [
            {"role": "user", "content": "log the emails"},
            {"role": "user", "content": content},
            {"role": "assistant", "content": [{"type": "text", "text": "Logged 8 rows."}]},
        ],
    )
    rendered = assistant.rehydrate_messages(sid) or []
    assert [m["role"] for m in rendered] == ["user", "assistant"]
    assert rendered[0]["text"] == "log the emails"
    assert not any("harness_note" in (m.get("text") or "") for m in rendered)


# ── the live path ─────────────────────────────────────────────────────────────


async def test_a_live_nudge_never_reaches_the_client_as_an_event(monkeypatch, db_isolated):
    """The persisted path is filtered by `rehydrate_messages`, but a nudge fires DURING a
    turn — so the question is whether the live stream carries it too.

    It cannot: `live_sessions` replays PUBLISHED EVENTS, not history, and the writers append
    to history without yielding anything. This test pins that, because "it happens not to be
    yielded today" is a property one stray `yield` would silently break — and the failure
    would be invisible until a nudge showed up in someone's chat.
    """
    import backend.ai.live_sessions as live_sessions
    import backend.ai.pending_injection as pending_injection
    import backend.ai.run_plan as run_plan
    import backend.ai.turns as turns
    import backend.services.profile as profile_service
    from backend.ai.tool_text import HARNESS_NOTE_OPEN

    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(profile_service, "read_profile", lambda: {})

    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)

    class _Provider:
        def __init__(self):
            self.calls = 0

        async def stream_chat(self, *a, **kw):
            self.calls += 1
            yield {"type": "text_delta", "text": "All 1 task completed."}
            yield {"type": "done", "stop_reason": "end_turn"}

    provider = _Provider()
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)

    sid = "test-harness-note-live"
    assistant.SESSIONS.pop(sid, None)
    token = run_plan.begin()
    try:
        run_plan.add_tasks([{"subject": "Fetch the last day of mailbox alerts"}])
        events = [ev async for ev in assistant.run(sid, [{"role": "user", "content": "go"}], "s")]
    finally:
        run_plan.reset(token)
        assistant.SESSIONS.pop(sid, None)
        assistant._SESSION_LOCKS.pop(sid, None)
        live_sessions._reset_for_tests()
        turns.set_inline_for_tests(False)

    assert provider.calls > 1, "the nudge must actually have fired for this to prove anything"
    blob = json.dumps(events)
    assert HARNESS_NOTE_OPEN not in blob
    assert "You are not finished" not in blob

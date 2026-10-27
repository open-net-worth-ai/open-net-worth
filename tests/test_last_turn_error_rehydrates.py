"""A turn that failed while nobody was watching still shows its error on reload.

Live 2026-08-16: an Automation run failed with

    Claude Code CLI reported an error: success API Error: 400
    messages: text content blocks must be non-empty

The run card rendered that in red. The conversation beside it rendered COMPLETELY EMPTY —
just the "Ran Log trade & transactions from emails" header and nothing under it.

Both halves of the live error path fail for a reader who was not watching:

- ``chatReducer``'s ``error`` branch attaches the message to the OPEN assistant bubble, in
  client memory only. Nothing persists it, so a reload loses it — in ordinary chat too.
- That turn produced no text at all, so there was no open bubble to attach it to. Even a
  live watcher would have seen nothing.

An Automation conversation is only ever opened afterwards, so it hits both, every time.
"""

from __future__ import annotations

import pytest

import backend.ai.assistant as assistant

_TRANSCRIPT = [
    {"role": "user", "content": [{"type": "text", "text": "scan the mailboxes"}]},
]


@pytest.fixture
def _session(monkeypatch, db_isolated):
    """A session whose transcript holds a user message and no assistant reply — the exact
    shape a run that died before emitting anything leaves behind."""
    sid = "automation-run-run_test"
    assistant._store().upsert_meta(sid, {"id": sid, "source": "automation"})
    monkeypatch.setattr(assistant, "_load_session", lambda _s: list(_TRANSCRIPT))
    monkeypatch.setattr(assistant, "_rehydrated_turn_usage", lambda *a, **k: {})
    return sid


def _rendered(sid: str) -> list[dict]:
    return assistant.rehydrate_messages(sid) or []


def test_a_failed_turn_renders_its_error_after_a_reload(_session):
    """The regression: this conversation used to come back empty."""
    assistant.upsert_session_meta(
        _session, last_turn_status="error", last_turn_error="API Error: 400 must be non-empty"
    )
    messages = _rendered(_session)
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["error"] == "API Error: 400 must be non-empty"


def test_the_error_bubble_carries_an_id_the_chat_panel_can_page_from(_session):
    """A conversation that is only the stub (a first turn that failed outright) crashed the
    chat panel on reload: it reads `messages[0].id` to decide whether older history exists."""
    assistant.upsert_session_meta(_session, last_turn_status="error", last_turn_error="boom")
    stub = _rendered(_session)[-1]
    assert stub["id"] == f"{_session}-turn-error"


def test_the_error_bubble_is_its_own_message_not_a_stamp_on_an_earlier_answer(
    _session, monkeypatch
):
    """A continuation that dies instantly leaves the PREVIOUS turn's answer as the last
    message. Merging the error into it would mark a good answer as failed — a worse lie
    than the missing bubble."""
    monkeypatch.setattr(
        assistant,
        "_load_session",
        lambda _s: [
            *_TRANSCRIPT,
            {"role": "assistant", "content": [{"type": "text", "text": "Logged 8 rows."}]},
        ],
    )
    assistant.upsert_session_meta(_session, last_turn_status="error", last_turn_error="boom")
    messages = _rendered(_session)
    assert messages[-2]["text"] == "Logged 8 rows."
    assert not messages[-2].get("error"), "an earlier successful answer must stay clean"
    assert messages[-1]["error"] == "boom"


@pytest.mark.parametrize("status", ["timeout", "interrupted"])
def test_the_other_ways_a_turn_dies_unwatched_also_render(_session, status):
    """A 20-minute timeout and a worker killed mid-run leave the same empty conversation."""
    assistant.upsert_session_meta(_session, last_turn_status=status, last_turn_error="stopped")
    assert _rendered(_session)[-1]["error"] == "stopped"


@pytest.mark.parametrize("status", ["ok", "awaiting_approval", "running"])
def test_a_turn_that_did_not_fail_renders_nothing_extra(_session, status):
    """A turn that is fine, or still going, says nothing.

    ``cancelled`` used to be in this list on the reasoning that "you stopped it yourself and
    already know". It moved out on 2026-08-22: a server restart cancels turns identically
    and nobody pressed anything, so that reasoning covered only one of the causes. It is
    still not a FAILURE — it renders as a notice, covered by
    ``test_a_cancelled_turn_explains_itself_in_the_conversation``.
    """
    assistant.upsert_session_meta(_session, last_turn_status=status, last_turn_error="stale")
    messages = _rendered(_session)
    assert [m["role"] for m in messages] == ["user"], "no bubble may be invented"
    assert not any(m.get("error") for m in messages)


def test_a_later_successful_turn_clears_the_error(_session):
    """The reason the field is stamped on EVERY status, not only on failure: a conversation
    that recovered must stop rendering the error it moved past."""
    assistant.upsert_session_meta(_session, last_turn_status="error", last_turn_error="boom")
    assert _rendered(_session)[-1].get("error") == "boom"

    assistant.upsert_session_meta(_session, last_turn_status="ok", last_turn_error="")
    assert not any(m.get("error") for m in _rendered(_session))


def test_an_old_session_with_no_stored_error_is_untouched(_session):
    """Metas written before this field existed must not grow an empty bubble."""
    assistant.upsert_session_meta(_session, last_turn_status="error")
    assert not any(m.get("error") for m in _rendered(_session))


# ── a crashed turn keeps its transcript ───────────────────────────────────────────


async def test_a_crashed_turn_keeps_the_messages_it_already_produced(monkeypatch, db_isolated):
    """Live 2026-08-16, run_2c606ddf3dc7: the CLI threw a 400 mid-run. Its run row recorded
    24 tool calls and 5 of 6 tasks completed; its session kept ONE message, the opening
    prompt. `del history[rollback_history_len:]` had deleted the rest, so the card showed a
    run that had clearly done a great deal beside a conversation showing none of it — and the
    task list only survived because it is projected onto the run ROW, not the transcript.

    Those tool calls really ran, and this automation writes transactions, so the rollback was
    destroying the only readable account of a turn that moved money. The cancellation branch
    already refuses to roll back for exactly this reason; the crash branch now matches it.
    """
    import backend.ai.live_sessions as live_sessions
    import backend.ai.pending_injection as pending_injection
    import backend.ai.turns as turns

    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)

    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)

    class _TextThenCrash:
        """One good round, then the transport dies — the shape of the live failure."""

        def __init__(self):
            self.calls = 0

        async def stream_chat(self, *a, **kw):
            self.calls += 1
            if self.calls == 1:
                yield {"type": "text_delta", "text": "Fetched 41 emails."}
                yield {"type": "done", "stop_reason": "end_turn"}
                return
            raise RuntimeError("Claude Code CLI reported an error: API Error: 400")

    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _TextThenCrash())

    sid = "test-crash-keeps-history"
    assistant.SESSIONS.pop(sid, None)
    try:
        async for _ in assistant.run(sid, [{"role": "user", "content": "one"}], "s"):
            pass
        # The failure reaches the caller either re-raised or mapped to an `error` event
        # (`turns._drive` handles both); which one is not what this test is about.
        try:
            async for _ in assistant.run(sid, [{"role": "user", "content": "two"}], "s"):
                pass
        except Exception:
            pass
        history = assistant.get_or_create_session(sid)[1]
    finally:
        assistant.SESSIONS.pop(sid, None)
        assistant._SESSION_LOCKS.pop(sid, None)
        live_sessions._reset_for_tests()
        turns.set_inline_for_tests(False)

    texts = [
        b.get("text")
        for h in history
        for b in (h.get("content") if isinstance(h.get("content"), list) else [])
        if isinstance(b, dict)
    ]
    assert "Fetched 41 emails." in texts, "the completed round must survive the crash"
    assert any(
        (h.get("content") if isinstance(h.get("content"), str) else "") == "two" for h in history
    ), "the message that opened the failed turn must survive too"


def test_a_cancelled_turn_explains_itself_in_the_conversation(_session):
    """ "cancelled" used to render nothing, on two pieces of reasoning that were both wrong.

    "The user stopped it themselves and already knows" — a server restart cancels turns the
    same way, and nobody pressed anything. "The run card covers it" — the card is a
    different surface; opening the conversation from the sidebar, the dock or the Lucius tab
    shows no card. So the transcript ended on a wall of tool pills with no explanation
    anywhere (owner, 2026-08-22).
    """
    assistant.upsert_session_meta(
        _session,
        last_turn_status="cancelled",
        last_turn_error="Did not finish — the server restarted while this turn was working.",
    )

    messages = assistant.rehydrate_messages(_session)

    last = messages[-1]
    assert "the server restarted" in last["error"]
    assert last["notice"] is True, (
        "a cancellation is not a failure — it renders as a neutral notice, not a red error"
    )


def test_a_failed_turn_is_still_an_error_not_a_notice(_session):
    """The notice branch must not soften a real failure into a muted aside."""
    assistant.upsert_session_meta(
        _session, last_turn_status="error", last_turn_error="API Error: 400"
    )

    last = assistant.rehydrate_messages(_session)[-1]

    assert last["error"] == "API Error: 400"
    assert "notice" not in last


def test_a_cancelled_turn_with_no_recorded_text_stays_quiet(_session):
    """Nothing stored, nothing invented — the pre-fix rows have an empty ``last_turn_error``
    and must not sprout an empty bubble."""
    assistant.upsert_session_meta(_session, last_turn_status="cancelled", last_turn_error="")

    messages = assistant.rehydrate_messages(_session)

    assert not any(m.get("error") for m in messages)

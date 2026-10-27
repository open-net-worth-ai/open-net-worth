"""A model round that called no tools has not necessarily FINISHED the turn.

Every case here is a real Automation run off this instance, 2026-03-02 → 2026-03-04. The
loop's exit condition treated "the model emitted no tool_use" as "the model is done", so
three different ways of producing nothing actionable all ended the turn silently:

1. The model wrote its tool call as TEXT — ``<call>{"name": "update_run_task", …}`` — so
   nothing ran, and the turn ended on the sentence describing the call it never made.
2. ``cli_tool_protocol`` caught a malformed call and emitted its corrective notice, whose
   own docstring says the model "has something to correct against" next round. There was
   no next round: the notice was the last text of the run.
3. The model narrated a finished run ("✅ All 5 run tasks completed") having called two
   tools, neither of which was ``fetch_emails``. Its own task list was untouched.

In all three the run's declared work was abandoned mid-flight and the task-list guard
(``automation_runner_adapter``) reported it as failed AFTERWARDS. This gate is the part
that was missing: give the model the correction while the turn is still open.
"""

from __future__ import annotations

import pytest

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.pending_injection as pending_injection
import backend.ai.run_plan as run_plan
import backend.ai.turns as turns
import backend.services.profile as profile_service
from backend.ai.tool_text import UNPARSED_CALL_NOTICE, looks_like_unexecuted_tool_call
from backend.ai.turn_completion import MAX_COMPLETION_NUDGES, completion_nudge

# ── the detector ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # Verbatim from run_4a7c91ff2e08 (haiku, 2026-03-02 09:14) — note the stray brace.
        '<call>{"name": "update_run_task", "input": {"task_id": "2", "status": "completed"}}}',
        # Verbatim from run_b0f3d825a917 (haiku, 2026-03-02 09:26).
        '<call>{"name": "list_categories", "input": {}}</call>  Now let me process the emails',
        # Verbatim from run_1c6e4a8fd2b3 (haiku, 2026-03-02 09:31) — truncated mid-write.
        'Apologies — I crashed mid-turn. Let me restart and process the emails. <call> {"',
        '<tool_call>{"name": "telegram_send_message", "input": {}}</tool_call>',
        '<function_calls><invoke name="telegram_send_message">',
        # GLM's NATIVE dialect: the function name sits bare between the opening tag and the
        # first child. run_47f8605fdd98 follow-ups (glm-5.3-flash, 2026-09-22): three rounds
        # of 15–17 output tokens — the exact cost of one of its real tool calls — that left
        # no text, no thinking and no tool_use. `strip_tool_syntax` erased the markup and
        # this detector, which demanded `{` or a child tag IMMEDIATELY after the opening tag,
        # never saw an attempt — so the turn ended `ok` on nothing.
        "<tool_call>read_doc\n<arg_key>doc_id</arg_key>\n<arg_value>features/importing</arg_value>\n</tool_call>",
        "<tool_call>find_tools<arg_key>query</arg_key><arg_value>upload attachment</arg_value></tool_call>",
        "Let me check the doc.\n<tool_call>read_doc\n<arg_key>doc_id</arg_key>",
        # A ZERO-argument call in the XML dialect has no body to demand: the close tag
        # follows the open tag directly, so it slipped past and the turn ended `ok`.
        '<invoke name="list_accounts"></invoke>',
        'Let me check your accounts.\n<invoke name="list_accounts">\n</invoke>\n```',
    ],
)
def test_a_tool_call_written_as_text_is_recognised(text):
    assert looks_like_unexecuted_tool_call(text) is True


def test_the_cli_protocols_own_unparsed_notice_counts(monkeypatch):
    """run_82fa019cde46 (haiku, 2026-03-03 14:47) ended on exactly this string. The notice
    exists to be corrected against, so its presence is proof the turn is unfinished."""
    assert looks_like_unexecuted_tool_call(f"Let me log that. {UNPARSED_CALL_NOTICE}") is True


@pytest.mark.parametrize(
    "prose",
    [
        "I'll update the automation now.",
        "Done — the S$29.50 Gojek charge is removed.",
        "The value is 3 < 5 and 9 > 2.",
        "Use the telegram_send_message tool to alert me.",
        # A mention of the syntax is not an attempt at it: no JSON body, no child tag.
        'The XML dialect writes <invoke name="telegram_send_message"> for that.',
        "Logged 'Invoice 4471 <parameter>' as $4,471 to Meridian Bank.",
        # An empty pair with no name names no tool, so it is talk about tags, not a call.
        "Wrap it in <invoke></invoke> tags.",
        "",
    ],
)
def test_ordinary_prose_is_not_mistaken_for_a_call(prose):
    """A false positive costs a wasted model round, so the detector demands the shape of a
    real attempt — an opening call tag followed by a JSON body or a parameter child."""
    assert looks_like_unexecuted_tool_call(prose) is False


# ── the gate ──────────────────────────────────────────────────────────────────────


def _plan(*statuses: str) -> list[dict]:
    return [
        {"id": str(i + 1), "label": f"task {i + 1}", "status": s} for i, s in enumerate(statuses)
    ]


def test_a_turn_that_really_is_done_is_not_nudged():
    assert completion_nudge("All set.", _plan("completed", "completed"), nudges_used=0) is None


def test_a_chat_turn_has_no_plan_and_is_left_alone():
    """``run_plan.get_tasks()`` is empty outside an Automation run, so rule 2 can never fire
    on an ordinary conversation — the human is the one who says "you missed something"."""
    assert completion_nudge("Here's the answer.", [], nudges_used=0) is None


def test_an_unexecuted_call_is_nudged_even_with_no_plan():
    """A tool call that did not run is broken in chat too: the reply describes a call that
    never happened."""
    nudge = completion_nudge('<call>{"name": "telegram_send_message"}', [], nudges_used=0)
    assert nudge is not None
    assert "DID NOT RUN" in nudge


def test_the_unexecuted_call_nudge_tells_the_model_to_retry_without_narrating():
    """Persona QA (2026-10-14): after this note the user read "The categorisation did not run,
    so I am sending it again now." — the note is the model's, never the person's."""
    nudge = completion_nudge('<call>{"name": "x"}', [], nudges_used=0)
    assert nudge is not None
    assert "SILENTLY" in nudge
    assert "do not mention the failed attempt" in nudge


def test_outstanding_declared_tasks_are_nudged():
    """run_5d3f8b2ae071: 'The automation run has completed successfully' — five tasks, none
    of them touched."""
    nudge = completion_nudge(
        "The automation run has completed successfully with a silent result.",
        _plan("pending", "pending", "pending", "pending", "pending"),
        nudges_used=0,
    )
    assert nudge is not None
    assert "5 of 5" in nudge
    assert "task 1" in nudge


def test_a_deliberately_failed_task_counts_as_resolved():
    """Same rule the run guard uses: resolved means completed OR failed, not succeeded."""
    assert completion_nudge("Done.", _plan("completed", "failed"), nudges_used=0) is None


def test_the_unexecuted_call_takes_precedence_over_the_plan():
    """Both true at once is the common case; the mechanical failure is the one to report,
    since fixing it is usually what finishes the plan."""
    nudge = completion_nudge('<call>{"name": "x"}', _plan("pending"), nudges_used=0)
    assert nudge is not None and "DID NOT RUN" in nudge


def test_the_nudge_is_bounded():
    """A model that will not comply must not be able to bill an unbounded number of rounds
    at this automation's ~400k-token context."""
    args = ('<call>{"name": "x"}', _plan("pending"))
    assert completion_nudge(*args, nudges_used=MAX_COMPLETION_NUDGES - 1) is not None
    assert completion_nudge(*args, nudges_used=MAX_COMPLETION_NUDGES) is None


# The fourth shape, run_47f8605fdd98's follow-up turns (glm-5.3-flash, 2026-09-22 03:43 and
# 03:46): asked to attach emails to five transactions, the model called two lookup tools,
# then answered the tool result with 17 tokens that came out EMPTY — no text, no call — and
# ended its turn. Asked "Done?", it produced a thinking block ("I need to actually do it
# now…") and ended again. Neither rule above matched (no prose-written call, and a chat
# follow-up has no task list), so both turns closed as `ok` with nothing to show.


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_an_empty_reply_is_nudged(text):
    """A round with no text and no tool call answered nothing; it is not a completion."""
    nudge = completion_nudge(text, [], nudges_used=0)
    assert nudge is not None
    assert "empty" in nudge.lower()


def test_an_empty_reply_is_nudged_on_a_finished_run_too():
    """The live shape: a follow-up on a run whose own tasks are all resolved."""
    assert completion_nudge("", _plan("completed", "completed"), nudges_used=0) is not None


def test_outstanding_tasks_take_precedence_over_the_empty_rule():
    """Both true at once: the task nudge names the actual work left, which is the more
    useful correction than "you said nothing"."""
    nudge = completion_nudge("", _plan("pending"), nudges_used=0)
    assert nudge is not None and "1 of 1" in nudge


def test_the_empty_nudge_is_bounded():
    assert completion_nudge("", [], nudges_used=MAX_COMPLETION_NUDGES) is None


def test_a_reply_that_is_only_call_residue_is_not_a_completion():
    """The gate reads the RAW text (the call-attempt rule needs the evidence the strip
    removes), so "empty" must mean empty AFTER the strip: a fragment of markup that is
    not a recognisable attempt still leaves the reader nothing, and must not pass as an
    answer just because the raw string had characters in it."""
    residue = "</arg_key><arg_value>features/importing</arg_value></tool_call>"
    assert completion_nudge(residue, [], nudges_used=0) is not None


# ── the loop actually re-enters ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, db_isolated):
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *a, **k: None)
    monkeypatch.setattr(profile_service, "read_profile", lambda: {})

    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)
    yield
    live_sessions._reset_for_tests()
    pending_injection._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()


def _cleanup(sid: str) -> None:
    assistant.SESSIONS.pop(sid, None)
    assistant._SESSION_LOCKS.pop(sid, None)


async def _drain(gen):
    return [ev async for ev in gen]


def _user_texts(history: list[dict]) -> list[str]:
    """Every user-role text in the transcript. Content is a bare string for the message the
    test seeds and a block list for anything the loop appends, so both shapes are read."""
    out: list[str] = []
    for entry in history:
        if entry.get("role") != "user":
            continue
        content = entry.get("content")
        if isinstance(content, str):
            out.append(content)
            continue
        out.extend(b.get("text") or "" for b in content or [] if isinstance(b, dict))
    return out


class _ScriptedProvider:
    """Answers with the given texts in order, ending the turn each time.

    ``on_round`` runs before each answer, so a test can model the model COMPLYING with a
    correction — the compliance that matters is a tool call, and a scripted text provider
    has no other way to express one. A text given as ``("thinking", …)`` is streamed as a
    thinking delta instead — a round that reasons and then stops without saying anything.
    """

    def __init__(self, *texts, on_round=None):
        self.texts = list(texts)
        self.calls = 0
        self.on_round = on_round

    async def stream_chat(self, *a, **kw):
        if self.on_round is not None:
            self.on_round(self.calls)
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        if isinstance(text, tuple):
            yield {"type": "thinking_delta", "text": text[1]}
        elif text:
            yield {"type": "text_delta", "text": text}
        yield {"type": "done", "stop_reason": "end_turn"}


async def test_a_text_form_call_reopens_the_turn_instead_of_ending_it(monkeypatch):
    """The regression. Round 1 writes the call as text; the turn must NOT end there."""
    provider = _ScriptedProvider(
        '<call>{"name": "telegram_send_message", "input": {}}</call>', "Done."
    )
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)

    sid = "test-nudge-call"
    _cleanup(sid)
    try:
        events = await _drain(assistant.run(sid, [{"role": "user", "content": "go"}], "s"))
        history = assistant.get_or_create_session(sid)[1]
    finally:
        _cleanup(sid)

    assert provider.calls == 2, "the model must get another round to make the call for real"
    assert len([e for e in events if e.get("type") == "turn_done"]) == 1

    nudges = [t for t in _user_texts(history) if "DID NOT RUN" in t]
    assert len(nudges) == 1, "the correction is in the transcript, not just in a log line"


async def test_an_abandoned_task_list_reopens_the_turn(monkeypatch):
    """run_5d3f8b2ae071's shape: a confident summary over an untouched plan."""

    # Round 2 models the model COMPLYING: it resolves the task it had abandoned. Without
    # this the scripted model never complies, correctly earns a second nudge, and the turn
    # runs to the bound — which is what `test_a_model_that_never_complies_still_ends` pins.
    def _comply(round_index: int) -> None:
        if round_index == 1:
            run_plan.update_task("1", "completed")

    provider = _ScriptedProvider(
        "All 5 tasks completed successfully.", "Done — task 1 marked.", on_round=_comply
    )
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)

    sid = "test-nudge-plan"
    _cleanup(sid)
    token = run_plan.begin()
    try:
        run_plan.add_tasks([{"subject": "Fetch the last day of mailbox alerts"}])
        await _drain(assistant.run(sid, [{"role": "user", "content": "go"}], "s"))
        history = assistant.get_or_create_session(sid)[1]
    finally:
        run_plan.reset(token)
        _cleanup(sid)

    assert provider.calls == 2, "one nudge, then the model complies and the turn ends"
    assert any("1 of 1" in t for t in _user_texts(history))


async def test_a_blank_round_reopens_the_turn(monkeypatch):
    """run_47f8605fdd98, 03:43: the tool result was answered with nothing at all."""
    provider = _ScriptedProvider("", "Attached all five.")
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)

    sid = "test-nudge-blank"
    _cleanup(sid)
    try:
        events = await _drain(assistant.run(sid, [{"role": "user", "content": "go"}], "s"))
        history = assistant.get_or_create_session(sid)[1]
    finally:
        _cleanup(sid)

    assert provider.calls == 2, "a blank round is not a finished turn"
    assert len([e for e in events if e.get("type") == "turn_done"]) == 1
    assert any("empty" in t.lower() for t in _user_texts(history))


async def test_a_glm_native_call_written_as_text_reopens_the_turn(monkeypatch):
    """The 03:43 / 03:50 rounds as they most plausibly reached the loop: the call in GLM's
    own markup, as text. It must be corrected as an unexecuted call — and must not vanish
    from the transcript into a silent `ok`."""
    provider = _ScriptedProvider(
        "<tool_call>read_doc\n<arg_key>doc_id</arg_key>\n<arg_value>features/importing</arg_value>\n</tool_call>",
        "Attached.",
    )
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)

    sid = "test-nudge-glm-native"
    _cleanup(sid)
    try:
        await _drain(assistant.run(sid, [{"role": "user", "content": "attach them"}], "s"))
        history = assistant.get_or_create_session(sid)[1]
    finally:
        _cleanup(sid)

    assert provider.calls == 2
    assert any("DID NOT RUN" in t for t in _user_texts(history))


async def test_a_thinking_only_round_reopens_the_turn(monkeypatch):
    """run_47f8605fdd98, 03:46, asked "Done?": 'I need to actually do it now. Let me read
    the doc…' — then end_turn. Reasoning about an action is not the action."""
    provider = _ScriptedProvider(("thinking", "I need to actually do it now."), "Done.")
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)

    sid = "test-nudge-thinking-only"
    _cleanup(sid)
    try:
        await _drain(assistant.run(sid, [{"role": "user", "content": "Done?"}], "s"))
        history = assistant.get_or_create_session(sid)[1]
    finally:
        _cleanup(sid)

    assert provider.calls == 2
    assert any("empty" in t.lower() for t in _user_texts(history))


async def test_a_clean_turn_costs_exactly_one_round(monkeypatch):
    """The gate must not add a round to every ordinary turn in the app."""
    provider = _ScriptedProvider("Here you go.")
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)

    sid = "test-nudge-clean"
    _cleanup(sid)
    try:
        await _drain(assistant.run(sid, [{"role": "user", "content": "hi"}], "s"))
    finally:
        _cleanup(sid)

    assert provider.calls == 1


async def test_a_model_that_never_complies_still_ends(monkeypatch):
    """Bounded: MAX_COMPLETION_NUDGES corrections, then the turn ends and the run guard
    reports the failure exactly as it does today."""
    provider = _ScriptedProvider('<call>{"name": "telegram_send_message"}')
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: provider)

    sid = "test-nudge-bound"
    _cleanup(sid)
    try:
        events = await _drain(assistant.run(sid, [{"role": "user", "content": "go"}], "s"))
    finally:
        _cleanup(sid)

    assert provider.calls == MAX_COMPLETION_NUDGES + 1
    assert len([e for e in events if e.get("type") == "turn_done"]) == 1

"""Whether a model round that called no tools has actually FINISHED the turn.

The tool loop's exit condition asked one question — "did the model emit a tool_use?" — and
read "no" as "the model is done". Those are different states, and the gap between them cost
13 of the 34 Automation-run failures recorded on this instance between 2026-08-08 and
2026-08-12. Three shapes, all ending the turn silently:

- The call written as TEXT. ``<call>{"name": "update_run_task", …}`` in the prose stream is
  not a tool call; nothing runs. The turn ended on a sentence describing work that never
  happened (run_3155d6b40bb3, run_eee6b81cbdc3, run_575fa3129dc5 — all haiku).
- The unparsed call. ``cli_tool_protocol`` already writes a corrective notice when a call's
  JSON will not parse, and its comment says the model gets "something to correct against"
  on the next round. There was no next round; the notice was the last text of the run
  (run_957510adad74).
- The abandoned plan. "The automation run has completed successfully… ✅ All 5 run tasks
  completed" over a task list where none of the five had been touched and ``fetch_emails``
  was never called, against a mailbox holding 41 messages (run_e06a0a64bb5e).
- The empty round. Asked to attach emails to five transactions, the model called two
  lookup tools, answered the second result with 17 tokens that came out as nothing — no
  text, no call — and ended. Asked "Done?", it produced only a thinking block ("I need to
  actually do it now…") and ended again (run_47f8605fdd98 follow-ups, glm-5.3-flash,
  2026-09-22). A follow-up on a finished run has no open tasks, so nothing above fired.

The task-list guard in ``automation_runner_adapter`` already catches the first three — but
only afterwards, as a failed run. This gate is the missing half: hand the model the
correction while the turn is still open, so a recoverable mistake costs a round instead of
the run.

Deliberately NOT a retry of the model's own reasoning. It states the mechanical fact the
model got wrong — the call did not run, the list is not finished — and lets it proceed.

Bounded, because the failing models are the ones least likely to comply: after
``MAX_COMPLETION_NUDGES`` corrections the turn ends and the run is reported failed exactly
as it is today. An Automation round here carries ~400k tokens of context, so an unbounded
"are you sure?" loop would be a real bill.
"""

from __future__ import annotations

from backend.ai.tool_text import looks_like_unexecuted_tool_call, strip_tool_syntax

# Two: enough for a model that mis-formats a call to see the correction and fix it, few
# enough that a model which simply will not comply cannot bill for a third full context.
MAX_COMPLETION_NUDGES = 2

_UNEXECUTED_CALL_NUDGE = (
    "Your last message contained a tool call written as text. It DID NOT RUN — no tool was "
    "invoked, no result exists, and anything you said about its outcome is invented. Make "
    "the call properly now and retry SILENTLY: the person never saw this note, so do not "
    "mention the failed attempt, a retry or an error to them — carry on as if the first "
    "call were this one. Only if you truly cannot make it, say plainly that you cannot and "
    "why."
)

_EMPTY_REPLY_NUDGE = (
    "Your last message was EMPTY — no answer and no tool call, so nothing happened and the "
    "person is still waiting. Reasoning about the next step is not the step. Make the tool "
    "call you were about to make, or answer them in plain text now. Do not end your turn "
    "with neither."
)


def _resolved(task: dict) -> bool:
    """Resolved means completed OR failed — the same rule the run guard uses. A run may
    deliberately fail a task ("no trades found" is a real outcome) and still be finished."""
    return task.get("status") in {"completed", "failed"}


def completion_nudge(
    assistant_text: str,
    tasks: list[dict],
    *,
    nudges_used: int,
    reply_given: bool = False,
) -> str | None:
    """The correction to feed back, or None if the turn may end.

    ``tasks`` is this run's declared task list (``run_plan.get_tasks()``), which is empty on
    an ordinary chat turn — so the outstanding-work rule is structurally unreachable there.
    ``reply_given`` says an earlier round of the turn already showed the reader an answer and
    this one is empty only because a hidden marker was stripped: that is a finished turn, so
    the empty-reply rule is skipped.
    The unexecuted-call rule applies to both: a reply describing a call that never ran is
    the same lie to a human as it is to a run card, and the human only gets to notice it.
    So does the empty-reply rule: ``assistant_text`` is the round's visible text, so a
    round that only THOUGHT arrives here blank too — and no flow in the app asks for an
    empty reply (the one "nothing to report" contract requires writing "None.").

    ``assistant_text`` is the RAW round text — the call-attempt rule needs the markup the
    transcript strip removes. "Empty" is therefore judged AFTER that same strip: a
    fragment of call syntax the detector does not recognise still leaves the reader
    nothing, and must not pass as an answer because the raw string had characters in it.
    """
    if nudges_used >= MAX_COMPLETION_NUDGES:
        return None

    # Checked first when both are true, which is the common case. The unexecuted call is the
    # mechanical failure underneath the abandoned plan, and naming it is what lets the model
    # fix the plan too — telling it "4 tasks outstanding" when the real problem is that its
    # calls are being written as prose just invites the same prose again.
    if looks_like_unexecuted_tool_call(assistant_text):
        return _UNEXECUTED_CALL_NUDGE

    outstanding = [t for t in tasks if not _resolved(t)]
    if outstanding:
        return (
            f"You are not finished: {len(outstanding)} of {len(tasks)} tasks on your own "
            f'list are still open (first: "{outstanding[0].get("label") or "task"}"). '
            "Count your successful tool results, not your intentions — nothing you have "
            "described as done was recorded unless the call actually ran. Do the remaining "
            "work now, or call update_run_task to mark what genuinely failed. Do not reply "
            "with another summary."
        )

    if not reply_given and not strip_tool_syntax(assistant_text).strip():
        return _EMPTY_REPLY_NUDGE

    return None

---
title: A Claude CLI round that has written its tool calls is cut after a grace and they run
status: Accepted
date: 2026-10-19
deciders: Repo owner, Claude
amends: 2026-10-14-a-silent-cli-is-abandoned-and-retried-once
---

# A Claude CLI round that has written its tool calls is cut after a grace and they run

## Context

Automation run `run_6955df3357ac` (2026-10-19, "Log trade & transactions from emails",
`claude_cli:sonnet`): the second round wrote `update_run_task` and `email_search_messages`,
then the CLI never sent its `result` event. The host runs a CLI round's calls only after that
event, so both waited until the 300 s ceiling killed the round and were marked failed; no
mail was read. The stall watchdog never fired because output kept coming, and nothing logged
what it was, so the five minutes could not be explained afterwards. Every nudge in the tool
loop (`turn_completion`, the narration retry) acts on a round that FINISHED, so none applied.

## Decision

1. **A post-call grace.** `stream_chat` reads the CLI through `_until_call_grace`: once the
   round's newest parsed tool call is `ONW_CLAUDE_CLI_CALL_GRACE` seconds old (default 30)
   and no `<tool_call>` block is open, the round ends there, as `stop_reason=tool_use`, and
   the host runs its calls. The clock restarts at every parsed call, so a reply with several
   calls keeps them all, and never runs mid-block, so a long call is never cut in half.
2. **The cut round's session is retired.** A CLI cut mid-reply cannot take the next send, so
   the live session is released as failed and the next round starts a fresh process with
   the full transcript. Its usage is unknown (no `result` event): recorded as zero tokens
   with `cost_reported` false.
3. **What streamed is logged.** `cli_call_grace.PostCallWatch` tallies the events since the
   last parsed call (or since the round began): kinds, text and thinking characters, and the
   first 160 characters of text. The tally is logged on a cut and on every CLI timeout or
   stall.

## Consequences

- A round like the one above costs about 30 s instead of the whole run.
- Text the model wrote after its calls is still shown and kept in the transcript, as before;
  the cut only stops the wait.
- A model that deliberately thinks for over 30 s between two calls in ONE reply loses the
  second call; raise `ONW_CLAUDE_CLI_CALL_GRACE`. Thinking between calls normally happens in
  the next round, after the results.

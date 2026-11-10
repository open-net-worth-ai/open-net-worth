---
title: A call keeps one ordered log of what was heard and one record per request
status: Accepted
date: 2026-11-06
deciders: Claude (voice calls session) + owner
---

# A call keeps one ordered log of what was heard and one record per request

Builds on `2026-11-03-voice-calls-are-a-fast-front-voice-over-lucius`, whose two layers stay.

## Context

The owner only knows Lucius, so they expect the chat to hold what they heard, in that order. Three
things stood in the way:

- A running turn holds the transcript in memory and writes it whole when it saves, so a line written
  beside it is lost.
- A line is only reported once it has played, by when Lucius's answer can be in the chat. Appended
  then, it landed after the answer, so lines said during a request were dropped, and the owner saw
  their bubbles go away.
- What a request had done and how it ended were flags on the call, reset by the next request, and a
  carried-on request was recognised by comparing its text.

## Decision

We will keep one `Task` per request (`call_log.py`) that owns what it did and how it ended. Carrying
on or correcting one that hasn't finished keeps its id and adds a revision.

We will keep lines said beside Lucius's work in a `CallLog` in the worker, each with a place relative
to its request: `before` it began (from LiveKit's speech start time), `in` (ahead of the answer), or
`after` the answer was heard. They are written when no turn is running (`desk.record_lines`), and not
before their request has started, as its message has to be there first.

A written line is a field of the message it goes with (`voice_lines`, found through a `voice_task`
mark on the message the request's turn opens with), never an entry of its own. `rehydrate_messages`
draws it as a message on either side of that entry. In the browser a line stays on screen until the
saved copy arrives.

## Consequences

### Positive

- Nothing heard is skipped, and order no longer depends on when a save happened.
- No message moves, so checkpoints, fold windows and cost chips, all addressed by position, still hold.
- A line can't reach a model, a summary or memory, as those read only a message's role and content.
- A line can't split a tool call from its result, or follow a tool call waiting on approval.

### Negative

- Lucius doesn't see small talk said while he worked, as those words are lines too.
- A line can't be searched in past conversations.
- Live lines and saved lines are matched by their words, so a line can briefly hide when an answer
  starts with the same words. It shows once saved.
- A line said as the call closes can still be lost, as it arrives after the last write.

### Follow-ups

Decided by the same research, not built here: the speech queue, the check for a figure Lucius never
gave, and an app-side rule and classifier for continue, correct or separate, tested on real calls.

## Alternatives considered

- **Drop filler lines:** the first fix. It broke "everything you hear is in the chat".
- **Save at the end of a turn, appended:** still after the answer, in the wrong order.
- **Entries inserted in the transcript:** built first and thrown out after review. They shifted the
  raw positions checkpoints and fold windows store, so Restore would rewind to the wrong message.
- **Write lines into the running turn's history:** right order, but a line could land between a tool
  call and its result, and `_fix_dangling_tool_uses` reads the last message to tell a paused call from
  a crashed one.
- **A separate table merged on read:** a migration and a second source of truth for one chat.

## Agent provenance

- **Decided by:** Claude, voice calls session, branch `voice/call-log`, on the owner's direction to
  build the task record and call log first.
- **Alternatives weighed:** as above, plus external architectures read second-hand through a research
  agent (OpenAI's chat-supervisor, LiveKit, Pipecat).
- **Driving artifacts:** owner's reports of 2026-11-06 ("those bubbles just go away", "nothing should
  be skipped"); LiveKit's `agent_activity.py`, which stamps a line's `created_at` with its speech start;
  an independent code review of the first build.
- **Why this way:** the worker knows the order heard, the app knows the transcript, and neither has to
  write at a moment it isn't safe to.

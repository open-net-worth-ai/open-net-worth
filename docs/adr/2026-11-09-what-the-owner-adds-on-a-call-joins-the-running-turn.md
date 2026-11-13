---
title: What the owner adds on a call joins the turn Lucius is running
status: Accepted
date: 2026-11-09
deciders: orchestrating agent (Claude) + repo owner
---

# What the owner adds on a call joins the turn Lucius is running

## Context

On a call, the owner says "they're legit card charges", pauses for about a second and a half, and
goes on: "can you please help me categorize them to Startup Costs?". The pause ended the first
request, so it went to Lucius as a change. The front voice correctly marked the second sentence
as carrying on from the first. But the call could only do two things with words said while a
request ran:

- **Stop and restart:** stop the running turn and start again on both sentences. That was refused
  for anything labelled a change, even one that hadn't changed anything yet (`_can_stop`).
- **Queue:** run the new words as a request of their own once the first had answered.

So the second sentence queued. The first answer asked where to file the charges, which the owner
had just said, and only then did Lucius see the follow-up (owner's call, 2026-11-09).

Typed chat already solves this with mid-turn injection (`ai/pending_injection.py`). A message sent
while a turn runs is spliced into that same turn at its next checkpoint, with a trusted fence, and
the turn answers everything at once. It is gated by the `chat_multi_turn_enabled` profile setting,
which is off by default (ADR `2026-08-10-multi-turn-chat-defaults-off`).

The owner's direction: we need a way to join, cancel and re-steer, not to wait longer before
starting. On a call it should always be able to join.

## Decision

We will send whatever the owner says while the call's own turn is running into that turn, through
the mid-turn injection typed chat uses (`voice.desk.join_turn`). A turn on the `chat:voice`
surface always drains such messages, whatever `chat_multi_turn_enabled` says.

- **Nothing is stopped or restarted.** A change already under way finishes, and Lucius reads the
  correction, the addition or the new question at his next step, then answers all of it once.
- **Only the owner's own transcribed words are injected.** The front model's reading of them is
  not, and the injection is fenced as trusted user text.
- **Only the call's own turn takes them.** A turn the owner typed is never joined, and neither is
  anything while an approval card is waiting.
- **Words the turn never read come back.** If the turn ends before taking them in, the worker
  withdraws them (`pending_injection.withdraw`) and they run next as their own request. If they
  can't be withdrawn, a turn already has them, so they never run twice.
- **Text Lucius wrote before taking them in is not read out.** It answered less than the owner
  had by then asked.

## Consequences

### Positive

- One answer covers what the owner said across a pause, as it would with a person.
- No added wait before Lucius starts.
- A change is never half done and restarted, because nothing is cancelled.
- The joining mechanics (fence, abort of a stale card, audit rebind, durable adoption) are typed
  chat's, already reviewed and tested, and not reimplemented for calls.

### Negative

- A correction to a question no longer discards the work in progress. Lucius carries on from
  where he is and adjusts, which can take a step longer than starting fresh.
- A different question asked mid-turn is answered together with the first, not after it.
- A voice turn drains a typed message queued on its conversation when Multi-turn chat is on. That
  was already the case for any running turn.

### Follow-ups

- The stop-and-restart path (`_continue_last`, `_replace_last`, `stop_call_turn`) now runs only
  when joining can't, for example behind a card or a request still waiting. It can be simplified
  once calls show the queue is rarely used.
- Check on real calls whether Lucius's own voice reaching the microphone (suspected on the same
  call) cuts answers off. One sample isn't enough to tell echo from speech by text alone.

## Alternatives considered

- **Wait longer after the owner stops before starting a change.** Rejected: it slows every
  request and still fails when they pause longer.
- **Allow stopping a change that hasn't changed anything yet.** Rejected: it still restarts the
  work, and once a change has begun the follow-up would queue again.
- **Send everything the owner says to Lucius and drop the front voice's routing.** Rejected for
  now: the front voice routed this call correctly. The failure was in what the call did with its
  decision, and small talk stays instant.

## Agent provenance

- **Decided by:** orchestrating Claude agent, with the owner's direction ("we just need a way to join
  things, cancel things going on and adjust, resteer"; "for call turns, it should always be able to
  join")
- **Lane / branch:** `voice/steer-running-turn`
- **Alternatives weighed:** a longer end-of-turn wait; stopping changes not yet made; everything
  to Lucius; joining the running turn
- **Driving artifacts:** owner's call of 2026-11-09 (conversation "Yesterday's spending summary");
  ADR `2026-11-03-voice-calls-are-a-fast-front-voice-over-lucius`; ADR
  `2026-08-10-multi-turn-chat-defaults-off`
- **Why this way (one line):** the turn already running is the one place that knows what the
  owner asked and what has been done, so their next words belong there.

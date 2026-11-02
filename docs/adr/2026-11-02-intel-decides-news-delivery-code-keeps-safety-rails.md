---
title: Intel decides news delivery; code keeps safety rails
status: Accepted
date: 2026-11-02
deciders: senior-engineer lane (Claude) + repo owner
---

# Intel decides news delivery; code keeps safety rails

## Context

Intel reaches the user's phone through three senders — the analyst's news push
(`monitoring/analyst._push_analysis`), price alerts and crash-watch
(`price_monitor._push_external_alert`) and the watch analyst's explained alert
(`watch_analyst._push`) — and `push_retry` only re-sends. Each decides alone, with a low bar and no
memory of what the others sent. One real day: seven news pushes, five on a single watched topic,
two near-identical ten minutes apart, one that called itself unverified, two crypto price moves,
several at 2 to 6 am. Rules added per sender (duplicate, silent materiality, stale, grounded,
cross-subject claim) stop repeats but cannot say whether *this person* needs *this* now.

## Decision

- **One decision step.** For every candidate notification Lucius decides **now**, **later** or
  **skip**, seeing everything the three senders found, what the person was already told in the last
  48 hours, who they are (nickname, local time, what they hold and watch) and their own words
  (`delivery_instructions`, Settings → Intelligence). "Later" items reach them together in a
  roundup, and via `get_untold_news` in the daily briefing. "Skip" stays in the Intel tab, as every
  story already does.
- **One seam, one flag.** `monitoring/delivery.route(candidate, legacy)` is the only place the
  `intel_delivery_decider` flag is read. Off, `legacy()` runs, the sender's own push function,
  untouched. On, the candidate is recorded and nothing is pushed by the sender. A candidate is
  created exactly where a push would have fired, after every upstream rail, so the new path can
  never push MORE than today. An unreadable flag, no configured channel or a failed write falls
  back to the legacy push.
- **A ledger, not memory.** `news_deliveries` (revision `202611021200`), written only through
  `services/news_delivery.apply_delivery_change` (`submit`, `decide`, `mark_told`, `expire`,
  `set_roundup_state`), holds the status machine `pending → waiting|told|skipped`, `superseded`
  (a newer candidate for the same story replaces an unsent one) and `expired` (a week). Exact
  transitions and exhaustive reads ("everything told in 48 hours", "everything waiting") are what a
  fuzzy memory fact cannot give. Runtime scheduler state: checkpoint-excluded (a /rewind must not
  turn a told item back into an unsent one), wiped on reset.
- **Judgement in the prompt, only safety rails in code.** `monitoring/delivery_decider`:
  a missing or invalid decision waits (never goes out now); a model failure sends a level the
  user set (price alert, crash-watch) as written and holds everything else; an item is told at most
  once, and only after a send that reached one channel (otherwise it goes to `push_retry`); a
  message never carries a link the model was not given; a roundup goes out at most every three
  hours; waiting items expire after seven days; every decision is logged with its reason.
  `decide()` (one model call, no database reads) and `build_prompt()` are public so an evaluation
  harness runs the same prompt through other models.
- **One tick, one call.** The analysis sweep (every minute) calls `run_tick`: candidates settle for
  45 seconds (so two sources on one event are decided together) unless they are a level the user set;
  one model call decides every candidate and, in the same breath, whether the waiting pile should
  go out as a roundup or be looked at again at `revisit_at`.

## Consequences

### Positive
- The judgement lives where it can be improved: a prompt, an eval, the user's own sentence.
- Near-duplicates across senders and sources are seen together, and what the person was told is
  part of the question.
- Flag off is byte-for-byte today's behaviour; the table is additive.

### Negative
- One more model call per tick with work (bounded: 12 candidates, 30 waiting, 40 told).
- Latency: a normal candidate waits about 45 seconds plus the call. A level the user set is
  decided immediately.
- A failing model holds everything but user-set alerts for the week (they stay in the Intel tab);
  there is no code-written roundup fallback.
- A zero-delivery `now` stays `pending` in the ledger while `push_retry` owns the send, so the
  ledger does not record it as told even if the retry later succeeds.

### Follow-ups
- A "Not urgent" button on a pushed message that feeds `delivery_instructions`.
- Alert ladders (a price alert series on one name) folded into one message.
- Other notification sources (Automations, reminders) opting in to the same seam.
- The briefing marking the items it mentions as told (today only a roundup does).
- Retire the flag and the legacy path by 2027-01-31 or move `remove_by` with a reason.

## Alternatives considered

- **Decide inside each analysis call** — rejected: each call sees one story, not the other
  senders, the told record or the waiting pile, and costs a call per story instead of one batched call.
- **More per-sender rules** — rejected: that is the status quo that produced the noisy day; every
  rule is a guess about a person the code does not know.
- **Remember what was told as a memory fact** — rejected: memory is context, never truth
  (CLAUDE.md); a status machine needs exact transitions and an exhaustive read.
- **Code-enforced "user-set always now"** — rejected: the person may say "ignore crypto alerts";
  only failure and omission are guarded, an explicit decision is honoured.

## Agent provenance

- **Decided by:** senior-engineer lane (Claude)
- **Lane / branch:** `feat/intel-delivery-decider`
- **Alternatives weighed:** per-analysis decision; more per-sender rules; memory fact; code-enforced user-set
- **Driving artifacts:** owner report of a seven-push day; `docs/FEATURE_FLAGS.md`
- **Why this way (one line):** one batched decision with the full picture, behind one seam, with the model's judgement bounded only by rails that cannot lose a stop the user set.

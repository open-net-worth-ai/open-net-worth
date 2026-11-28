---
title: The context window starts where the fold ends, and moves on token pressure
status: Accepted (behind flag `token_context_window`, default off)
date: 2026-11-24
deciders: repo owner + Claude
---

# The context window starts where the fold ends, and moves on token pressure

## Context

Lucius sends the last `MAX_HISTORY_MESSAGES = 60` messages verbatim, advanced in steps of
`_WINDOW_SLIDE_CHUNK = 10`, with the folded summary in front. The first fold is unconditional
once the window moves; later re-folds wait for 55% token pressure (ADR
`2026-08-27-age-decay-is-token-gated-not-message-count-gated`, whose token gate only ever
reached the re-fold, never the window).

Two defects, both measured:

1. **A message is not a size.** A chat of short messages compacts at 70 messages on a 1M-token
   model. The owner noticed a compaction on a conversation that was a small fraction of the
   window.
2. **Every slide after the first fold opens a hole.** The window keeps sliding every ten
   messages; the re-fold does not follow unless pressure is high, so `history[folded:start]`
   is sent in neither form — and `get_earlier_conversation_detail` cannot reach it either (it
   reads only up to `folded_through_idx`). The 2026-08-27 "fold before the turn" fix closed
   this for the FIRST crossing only. Replaying this instance's 1,459 conversations
   (`backend/scripts/eval_context_window.py`, 200K window): 29 conversations lost 2,130
   messages this way.

Why the window counted messages, and what each reason still demands:

| Reason | Still required |
|---|---|
| Prompt cache: a start that moves every turn rewrites the whole cached conversation at 1.25× (`_WINDOW_SLIDE_CHUNK`). A reused Claude CLI session also restarts when its prefix changes (`cli_live_session.acquire` fingerprints). | The start moves rarely. |
| Live incident 2026-08-09: an Automation's own tool calls pushed its "successful writes" report out of a 30-message window before the owner asked about it (30 → 60). | Recent exchanges always stay verbatim, however large. |
| ADR 2026-08-27: nothing inside the window is stubbed, decayed or truncated — mechanical shrinking destroyed "did I already add this?". | No tool-result clearing (OpenClaw's session pruning and Hermes' phase-1 pruning both do this; we do not). |
| ADR 2026-08-16 amendment: the context-budget preflight refused a routine 84-message chat and was removed. | Never refuse a turn; a prompt that cannot shrink goes out whole. |
| Fold runs awaited, before the turn (2026-08-27). | Unchanged. |

## Decision

Behind `token_context_window` (`ai/context_window.py`; the switch point is one `if` in
`assistant._run_turn`; off = the message-count code, untouched):

1. **The window start IS the fold's `folded_through_idx`.** There is no second number, so
   nothing can fall between the window and the summary. A fold is trusted only if it still
   describes this conversation: its index is within the history and its new
   `boundary_digest` (hash of the last folded message) matches — a rewind below the fold
   followed by new messages is detected and the fold ignored, never reused. A fold written
   before the digest existed is trusted by length.
2. **Fold only under pressure.** This turn's prompt ≈ the overhead (the last provider-reported
   prompt minus the estimate of the history it carried; 25K before any measurement) + the
   byte/4 estimate of what would be sent. Fold when it passes `min(55% of the window, 160K)`.
3. **Fold far, so the next fold is far.** Cut at the earliest real user message after which
   the history fits `min(30% of the window, 90K) − overhead` (and never more than half of
   what fits under the trigger). About 25% of the window separates two folds; the cache and a
   reused CLI session survive every ordinary turn.
4. **What is never folded:** the last 20 messages, this turn and the two exchanges before it. A turn resumed after an approval never folds: its window was settled when it began.
   A cut lands only on a user message that is not a tool result, so a call and its result
   are never separated. A fold that frees less than `max(4K, 10% of the window)` does not run
   — on a small window the overhead alone sits near the trigger, and folding a little every
   turn would break the cache every turn.
5. **Large ranges fold in steps** of at most 240K characters of conversation text, each on a
   turn boundary, each extending the previous summary. A failed step leaves the start at the
   last step that succeeded.
6. **The 160K cap** on 1M-window models: past it each turn costs real money and latency even
   on cached reads, and long-context recall degrades. Claude Code, Codex, OpenClaw
   (`min(window − 20K, 85%)`) and Hermes Agent (50%) all compact well before the hard limit.

Data is additive: `boundary_digest` is one more key in the existing `fold` JSON. Off → on →
off is safe: the old path ignores the key; the new path re-derives everything from the fold.

## Evidence

`tests/test_token_context_window.py` — one class per reason above, plus a replay of random
long sessions (32K / 200K / 1M windows, with 40% of folds failing) asserting the window and
the fold always meet. `backend/scripts/eval_context_window.py` replays real conversations
read-only and prints aggregates only:

| Window | Policy | Conversations folding | Window moves | Conversations with holes | Messages lost |
|---|---|---|---|---|---|
| 200K | count | 38 | 266 | 29 | 2,130 |
| 200K | tokens | 30 | 66 | 0 | 0 |
| 1M | count | 38 | 266 | 32 | 3,400 |
| 1M | tokens | 12 | 23 | 0 | 0 |
| 128K | count | 38 | 266 | 15 | 1,290 |
| 128K | tokens | 58 | 150 | 0 | 0 |

## Not done here

- The flag-off path keeps its hole. Fixing it there means re-folding on every slide; the flag
  is the fix.
- A provider's context-overflow error does not trigger a fold-and-retry (OpenClaw does).
- A session whose PROTECTED tail alone is over the trigger (every exchange carries huge tool
  results) can fold on each turn that adds at least the reclaim minimum. Each such fold is real
  savings, so no turn-count rate limit was added; revisit if the eval shows it.
- A failing summary model is retried on every turn (no backoff), as on the old path.
- A single prompt larger than the window (the largest replayed: ~800K estimated tokens, in its
  protected tail) still goes to the provider whole, as before.

## Agent provenance

- **Decided by:** Claude (Opus), with the owner, after the owner asked why compaction counted
  messages and how Claude Code, Codex, OpenClaw and Hermes Agent do it.
- **Lane / branch:** `feat/token-context-window`, worktree `.claude/worktrees/token-window`.
- **Alternatives weighed:** clearing old tool results before summarising (OpenClaw, Hermes,
  Claude Code) — rejected by ADR 2026-08-27; fixing the hole on the count path by re-folding
  every slide — rejected, it compacts more often for the same wrong trigger; a fixed
  token-sized tail (OpenClaw `keepRecentTokens` 20K, Hermes 2.5% of the window) — rejected,
  this codebase keeps far more verbatim by owner choice.

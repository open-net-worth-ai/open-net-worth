# Context folding ("compacting")

> **On-demand reference.** Not loaded into every turn — read it when you touch this area.
> Decisions and their reasoning live in [`docs/adr/`](../adr/); this file states current truth.
> Design background: `.claude/rfcs/context-folding-v2.md` (RFC Context Folding v2).

A long conversation's older messages are periodically replaced with a persisted, LLM-written
summary plus a verbatim ledger of every real user message — never truncated, never dropped
silently. "Compacting" is the user-facing name for this (progress bar, status line); "fold" is
the code/RFC name for the same mechanism. All backend logic lives in `backend/ai/assistant.py`.

## Sliding window + trigger

- `MAX_HISTORY_MESSAGES = 60` — the verbatim window kept in every turn, unconditionally, no
  matter tool, size, or repetition. `_WINDOW_SLIDE_CHUNK = 10` — the window start only advances
  in steps of 10, not message-by-message, so an ordinary turn doesn't shift the prefix and bust
  the whole conversation's prompt cache. `_compute_turn_prefix_state`/`_prepare_messages` compute
  `window_start` once per turn and pin it for every round-trip in that turn.
- **The window is the thread, alerts included.** Alerts and briefings are ordinary assistant
  messages and count toward the 60, however long ago the person last wrote. The window used to be
  cut to its first user message, so a reply after a burst of alerts reached the model with nothing
  in front of it. A provider still needs a user message first: that is the fold summary, or
  `WINDOW_OPENS_ON_LUCIUS_MARKER` when there is none. A reused CLI session steps over only its own
  reply (`cli_live_session.pending_turn`), so an alert pushed after that reply reaches it too.
- **Fold is triggered by real token pressure, not a message count**
  (`_should_refold`/`_token_pressure_over_fraction`): the first fold ever, unconditionally; every
  fold after that only when the session's real usage exceeds `FOLD_TRIGGER_FRACTION = 0.55` of
  the model's context window. Prefers the last real provider-reported prompt size
  (`session_chat_records`'s most recent cost-log row); falls back to a local
  `_approx_token_count` byte-estimate (`len(text) / 4`, ceil) when no usage row exists yet. A
  fixed message-count threshold (`FOLD_REFOLD_EVERY_K`) that used to bootstrap the "no usage
  recorded yet" case is retired (2026-08-27) — the local estimate covers that gap directly.
- **Everything else that used to shrink history is retired (2026-08-27, owner-directed):** age
  decay, supersession shrink (stubbing an older call of a tool once a fresher one exists in the
  window), and free-text head-truncation (`fetch_page`/`web_search` results cut to a char cap).
  All three were mechanical rules with no understanding of content, and all three destroyed the
  model's own ability to reason about or explain something from its conversational memory (found
  via a real automation-run incident). Fold is not affected by this retirement — it is an LLM
  reading real content and producing a real summary, not a blind mechanical rule. See ADRs
  `2026-08-27-age-decay-and-supersession-shrink-are-both-retired.md`,
  `2026-08-27-free-text-head-truncation-is-also-retired.md`,
  `2026-08-27-age-decay-is-token-gated-not-message-count-gated.md`.

## Fold generation and storage

- `_generate_fold_summary` (an async generator) summarises `history[folded_through_idx:
  window_start]` — new prose since the last fold, or the full pre-window range on the first
  fold — into a structured summary under fixed headings (`_FOLD_SUMMARIZE_SYSTEM`: Decisions &
  preferences / Figures & facts asserted / Actions taken / Automations triggered / Open
  questions / In progress; every heading always present, `'None.'` when empty). Run on
  `MEMORY_MODEL` with `effort="none"` (mechanical, not worth paying to reason) and a
  `max_tokens=8000` safety ceiling.
- **Verbatim user messages are preserved separately, never paraphrased**
  (`_verbatim_user_messages`) — the single highest-leverage design choice in the RFC: a short
  reply ("no") answering a write confirmation is exactly as short as a "no" that means nothing,
  and no text-only heuristic can reliably tell them apart, so every real user message in the
  folded range is kept verbatim rather than filtered.
- Storage: a nullable `fold` column on the session's own row (`_load_fold`/`_save_fold` →
  `_store().get_fold`/`set_fold`), **not** a sidecar file (pre-2026-08-24 it was
  `SESSIONS_DIR/{id}.fold.json`). Best-effort — a save/load failure degrades to unfolded
  context, it never raises into the turn. `db/session_store.py` never fires the DB write hook
  for any session column, so a fold summary is exactly as re-generatable/uncaptured by
  `/rewind` as it was as a file.
- Injection: when a fold summary exists, `_prepare_messages` prepends it as a synthetic user
  text block, marked with the literal constant `FOLD_SUMMARY_MARKER =
  "[CONVERSATION SUMMARY SO FAR]"` — a named constant so a reader recognises the same text on
  both the write side and any future read side, never a copy that can drift. Placement is in
  `messages[]`, not `system[]`, so the tools/static/semi-stable system prompt breakpoints stay
  byte-identical across turns (cache-friendly).
- **Escape hatch**: `get_earlier_conversation_detail` (`ai/tools/misc.py`) backs onto
  `_conversation_slice_for_recall` — a bounded, most-recent-first read into whatever a fold
  summary paraphrased away, for when the model (or a user asking "what did I say about X
  earlier") needs more than the summary kept.

## Turn integration and SSE events

- **The turn that crosses the fold boundary pays fold's latency itself, awaited, before it
  renders anything** (`_stream_turn_fold_update`, fixed 2026-08-27). Previously fold ran
  fire-and-forget *after* a turn had already responded, while `window_start` advanced
  synchronously the moment history crossed `MAX_HISTORY_MESSAGES` — on the turn that first
  crossed that boundary (or any turn after a swallowed fold failure), the messages `window_start`
  had just excluded were represented by neither raw content nor a fold summary: a silent
  context hole, with no signal to the model that anything was missing. The fix makes the
  invariant hold by construction: `window_start` can never be "ahead of" what fold has actually
  covered.
- A cheap pre-check (`_should_refold` against already-loaded data, no LLM call) decides whether
  the turn is genuinely about to pay the slow real cost, so the compacting signal only fires
  when a real fold is happening — not on every ordinary turn once a session happens to be long.
- SSE events, in order, only emitted when a real fold is running this turn:
  `{"type": "compacting_start"}` → zero or more
  `{"type": "compacting_progress", "percent": int, "elapsed_ms": int, "output_tokens": int}`
  (streamed as the summary generates) → `{"type": "compacting_done"}`.
  `percent`'s denominator (`_FOLD_SUMMARY_TARGET_CHARS = 1500 tokens × 4 bytes/token`) is an
  ESTIMATE of expected summary length, not the 8000-token safety cap — every real fold on this
  instance's logs landed at 200–690 tokens, so using the cap as the denominator would show every
  fold stuck at 3–9% for its whole duration, then jump straight to 100. `percent` is clamped to
  95 while streaming; it only reaches 100 on actual completion, so an unusually long summary
  holds near-full rather than lying about being done.

## Frontend

- `ConvSlice.compacting: { percent: number; outputTokens?: number } | null`
  (`state/chatSessions.ts`) — non-null only during the pre-turn fold phase. Set on
  `compacting_start`, updated on each `compacting_progress`, cleared on `compacting_done` (and
  defensively on `turn_done` too, in case a reconnect dropped the `compacting_done` event) —
  handled in `state/chatReducer.ts`.
- `components/chat/CompactingActivity.tsx` renders it: the same row-1 `.lx-activity`
  dot+shimmer+meta-line language `TurnActivity` uses (so the two read as one continuous family),
  but with a static label ("Compacting conversation…") and a real `Meter` progress bar
  underneath instead of `TurnActivity`'s rotating word — compacting has a real backend
  percentage, so nothing needs to be invented. Elapsed time and the latest token count tick on a
  1s client clock (bounding the `aria-live` announcement rate); the `Meter` itself has no such
  limit. Mounted in both `ChatPanel.tsx` (side panel) and `LuciusDock.tsx` (floating dock),
  preempting `TurnActivity` in the exact slot it would otherwise occupy.
- CSS: `.lx-compacting`/`.lx-compact-bar` in `frontend/src/lucius.css`; the `Meter` component's
  `prefers-reduced-motion: reduce` handling (no fill transition) is in `frontend/src/index.css`.

## Testing this without a real long conversation

There is no way to trigger a real fold on demand — it needs 60+ messages plus genuine token
pressure. A `/compact` slash command that would make this easier to trigger for real was
considered and explicitly declined (2026-08-27) in favor of a dev-only mock instead.

`window.__mockCompacting(percent?, outputTokens?)` (`state/chatSessions.ts`, `import.meta.env.DEV`
only — statically stripped from a production build) drives every open conversation's
`compacting` slice directly, bypassing the backend entirely:

```js
__mockCompacting(35)        // 35%, no token count
__mockCompacting(35, 820)   // 35% · ↓ 820 tokens
__mockCompacting()          // clears it (same as a real compacting_done)
```

**Known gap**: as of 2026-08-27 the whole frontend surface (`CompactingActivity`, the reducer
wiring, the CSS) has only ever been exercised via this mock — it has not yet been verified
against a real conversation that actually crosses the fold boundary and streams real
`compacting_*` events end to end.

---
title: A notable move is measured against the instrument's usual move — one definition
status: Accepted
date: 2026-11-24
deciders: orchestrating agent (Claude) + repo owner
---

# A notable move is measured against the instrument's usual move

## Context

- "Is today's move notable?" had several answers. The Intel watch rules scored a move against
  the stock's own usual daily move (σ of its last 60 daily returns: notable at ≥ 2σ and ≥ 1.5%,
  fixed per-class floors without history). The Notable Price Movement timeline highlighted any
  move ≥ 2%, for every instrument. Its live paragraph was rewritten whenever the day's move
  travelled ≥ 1 percentage point.
- A fixed percentage means different things per instrument: 2% is an ordinary day for a volatile
  chip stock, a big one for a utility or bond fund, and noise for crypto. The same day could be
  "unusual" on the Intel pulse and not on the asset page, or the reverse.
- Owner-reproduced 2026-11-23: on a +30% day for a volatile stock, the fixed 1-point rewrite
  trigger re-fired every five minutes while the page was open (the paragraph, which said
  "surging", stayed true), so the card sat on "Updating…" and spent a model call each time.

## Decision

1. **One definition, in `services/move_significance.py`** (pure): the usual daily move, the
   thresholds (`Z_NOTABLE`, `Z_HIGH`, `Z_MATERIAL`, `MIN_ABS_MOVE_PCT`), the per-class floors
   without history, and `is_notable(day_pct, sigma, asset_class)`. `watch_stats`,
   `watch_history_rules` and `watch_rules` read it; their old constants are re-exports.
2. **The Notable Price Movement highlight uses it.** Each row's usual move is computed from the
   sessions BEFORE that row's day (never the day itself), stamped on the wire as
   `usual_move_pct`, and `notable` comes from `is_notable`.
3. **The live rewrite trigger scales with it.** Today's paragraph is rewritten when the move has
   travelled at least one usual daily move of the instrument, never less than 1 point
   (`monitoring.price_movements.rewrite_delta`). Milestones, the stale-with-new-evidence rule and
   the 5-minute floor are unchanged.

## Consequences

- A calm instrument's 2% day is now highlighted; a volatile one's 2–5% day no longer is. Without
  60-ish sessions of history, a stock needs 5% (commodity 6%, crypto 10%), up from 2%.
- A volatile instrument's live paragraph is rewritten less often on a big day; a calm one's keeps
  the 1-point cadence.
- Not changed: the portfolio brief's "today" signal (a whole-portfolio move, a different fact)
  and crash-watch thresholds ("your holding is cratering", deliberately higher). A future
  "notable" reader (alerts, briefing) should call `move_significance`, not add a threshold.

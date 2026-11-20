---
title: Paper Autopilot proposes; the app sizes and places
status: Accepted
date: 2026-11-21
deciders: repo owner + orchestrating agent (Claude)
---

# 2026-11-21. Paper Autopilot proposes; the app sizes and places

## Context

A paper Autopilot review was one Lucius turn that placed its own orders, sized however the model
chose. Reviews tended to reason about each holding alone: they sold stop-losses, then sat on the
cash, and never weighed adding to a holding against buying a new name. Open-source trading agents
(TradingAgents, ai-hedge-fund) split the work: analysts and a debate propose, a risk manager and a
portfolio manager decide how much. We wanted the size of an order to come from a rule the owner
can read, not from the model's arithmetic, and to set up a bull/bear check and a risk review
(plan: `docs/design/paper-autopilot-upgrades.md`, steps 3 and 4) that both need the orders as
proposals before anything is placed.

## Decision

Behind the flag `autopilot_committee` (off by default; off = the review places its own orders, as
before, from the same code):

- **The review proposes.** It is offered `propose_paper_orders` instead of `place_paper_orders`.
  Each proposal is buy (a new name or more of a holding), trim or sell, with a one-sentence why
  and, for a buy or trim, a conviction: low, medium or high. A later call replaces the list.
- **The app sizes.** Conviction maps to a target share of the portfolio: low 4%, medium 7%, high
  10% (`services/paper_trading/autopilot_sizing.py`), never above the mandate's per-holding cap.
  A buy fills the gap to that target as far as cash above the mandate's floor allows, planned 2%
  under its room so slippage at the fill never breaks a limit; a trim sells down to it; a sell
  exits in full. Whole shares for stocks, fractions for the rest.
- **The app places, sells first.** After the turn, only if the review wrote its memo,
  `autopilot_proposals.execute` places sells and trims, re-values, then buys from the most
  convinced down — each through the normal paper choke with origin `autopilot`, so the mandate
  still refuses anything that breaks it. A holding and a new name compete for the same cash on
  the same terms.
- **Every proposal is logged with what happened to it**: placed (with its order), skipped (with
  why: already at target, no cash above the floor, at the maximum holdings) or refused (the
  choke's reason). One failing never stops the others. History shows them.
- A ticker the app already knows resolves locally first, so a proposal never mints a duplicate
  instrument; only an unseen one goes through the instrument front door.
- **Two checks before any buy** (`autopilot_committee.py`), each its own turn with its own small
  tool offer and budget, at low effort, inside the same paper-only context:
  - a **challenge** argues the strongest honest case against each buy and records keep, shrink
    (one conviction step: high → medium → low → dropped) or drop, with the case against;
  - a **risk review** judges the buys left as a set, with what the portfolio holds, and records
    approve or trim (one step), with the reason.
  Neither can add an order or raise a size, and neither touches a sell or a trim. **Fail-safe,
  never fail-open:** a challenge that doesn't finish shrinks every buy, a buy it gives no verdict
  is shrunk; a risk review that doesn't finish drops every buy, a buy it gives no verdict is
  trimmed. Each turn is skipped when there are no buys left to judge.

## Consequences

- An order's size is now explainable in one line ("to 7%, medium conviction"), and the same rule
  sizes an add and a new position.
- The model can no longer size a position by hand. A view it holds strongly but briefly (a
  catalyst trade) gets at most 10%, or the mandate's cap if lower.
- Orders are placed after the turn, so the model can't read a fill and react within the same
  review; the next review sees the result in its track record.
- The tool-schema budget rises by the three new tools' size (flag-hidden, review-only, not in a
  chat's default offer).
- A review that proposes a buy costs up to three model turns instead of one; the two checks run
  at low effort on the review's model. A review that only holds or sells costs one.
- Every buy carries its case for (why, case), its case against and the risk note into History,
  so a reader can see why it was bought, shrunk or dropped.

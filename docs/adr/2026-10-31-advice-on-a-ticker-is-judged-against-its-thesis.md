---
title: Advice on a ticker is judged against its investment thesis — one judgment, one guard
status: Accepted
date: 2026-10-31
deciders: orchestrating Claude agent + repo owner
---

# Advice on a ticker is judged against its investment thesis

## Context

ADR `2026-10-27-an-investment-thesis-lives-on-the-instrument` gave every instrument a thesis
(role, reason, breaks-if, stop / buy zone / fair value / trim) and deferred using it. Lucius kept
suggesting a sale after a 15–20% drop on a name whose thesis said "speculative, holding 3+ years":
every surface that says what to do read the move alone. Those surfaces are separate model calls
with separate prompts — the Analyst (crash / news alerts), price-alert narration, the Intel scan,
the daily briefing, the watched-ticker write-ups — plus Lucius's tools in chat.

## Decision

- **One judgment:** `services/thesis_judgment.judge(thesis, price, held=…)` places the price
  against each level and derives ONE stance (`below_stop`, `above_trim`, `near_trim`,
  `in_buy_zone`, `below_buy_zone`, `above_buy_zone`, `near/above_fair_value`, `intact`). Every
  surface renders it the same way (`prompt_block`, `cite_line`, `compact` for tools) and the
  shared `THESIS_RULE` instruction tells each model how to use it. The holding period is read
  from the reason text (`holding_period`) — no horizon field, by the owner's choice.
- **One guard, deterministic, after the model:** `services/thesis_guard` drops or replaces advice
  that contradicts the thesis — `guard_actions` / `guard_text` for one ticker,
  `guard_book_actions` / `guard_book_text` for prose naming several (the briefing). Sell advice
  stands only when the price is at/below the stop, or when the advice itself says the thesis
  broke ("thesis broken: …" — the model's reading of `breaks_if` against the news, which no rule
  can check). Trim advice stands at/above the trim level. A replaced line becomes the thesis's
  own advice, cited. Prompts alone were not enough: the model is told, and the guard makes the
  outcome testable.
- **Cited wherever advice is given:** actions about a thesis ticker carry the citation (role,
  reason, breaks-if, price against every level); the Analyst's pushed message appends it after
  the length cap, like its sources.
- **No thesis ⇒ no-op.** Every function returns its input unchanged and no prompt gains a block.
- **Not alerts.** Stop and trim levels raise no alert or story. The held pulse shows them as
  findings (at the stop, at / near trim, nearing fair value); whether thesis crossings should
  become alerts, and replace duplicate manual price alerts, is a separate open question.

## Alternatives rejected

- **Prompt instructions only** — the model still writes "consider selling" often enough, and the
  behaviour couldn't be pinned by a test.
- **Refuse all sell language for a thesis ticker** — a broken thesis or a crossed stop is exactly
  when the user wants to hear it.
- **A per-surface judgment** — the same price would read "intact" on one screen and "near trim"
  on another; one authority, as with concentration and today's move.

## Consequences

- A model that genuinely sees the `breaks_if` event must say "thesis broken:" for its sell advice
  to survive; the shared rule tells it to.
- The guard is regex-based: a sell phrased in a way it doesn't recognise passes through. The word
  list lives in `thesis_guard._SELL_RE`; extend it there.

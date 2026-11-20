# Paper Autopilot upgrades — plan

Status: step 1 (memory, with lessons) built 2026-11-21; steps 2–4 proposed.

## Why

Today one review is one Lucius turn: it reads the portfolio, maybe searches, and places orders
itself. Open-source trading agents (TradingAgents, ai-hedge-fund) split that work into roles —
analysts, a bull/bear debate, a risk team, a portfolio manager — and learn from past calls.
Autopilot already has two things they lack: the mandate is enforced in code, and the review
can only touch the paper book. This plan adds what it is missing, without giving up either.

Caveat that shapes the design: nobody has independently shown that more agents make more
money (LLM backtests are inflated by look-ahead bias, arXiv 2601.13770). So each step must make
the decision **better explained and easier to check**, not just longer.

## The shape after all four steps

```
            ┌──────────────────────────────────────────────────────────────┐
 schedule → │ 1 Research turn   reads + searches + track record (memory)   │
 or         │                   → proposes orders, each with conviction    │
 Review now │ 2 Challenge turn  argues against each proposed buy           │
            │                   → keep / shrink / drop, with the reason    │
            │ 3 Risk turn       looks at the whole set together            │
            │                   → approve / trim, with the reason          │
            │ 4 Code            sizes from conviction, enforces the mandate│
            │                   → places the orders, assembles the memo    │
            └──────────────────────────────────────────────────────────────┘
```

The model **proposes and argues**; code **sizes and places**. The paper choke still refuses
anything that breaks the mandate.

## Step 1 — Memory: a track record every review reads

**What:** before deciding, each review reads a short, code-built track record:

- its last 5 reviews: date, what it did, what it said it was watching;
- every paper buy and how it has done since (`results._trades` already computes this);
- every sell and how the stock has moved **since** it sold (new: did the stop save money,
  or did it sell the bottom?);
- the "watching" items from the last review, so it follows up on them first.

**Where:** new `services/paper_trading/autopilot_memory.py` builds the block (pure read, bounded:
filter then cap, per the tool-results rule); `autopilot_run.prompt` includes it. No new store —
the run log (last 20) and orders already hold everything.

**Done when:** a review's memo can say "I bought Broadcom at 337, now 352" or "the IONQ stop
saved 8%" from facts it was given, not ones it re-derived. Tests: the block's contents for a
seeded book; it stays under its size cap with 20 runs and 50 trades.

**Size:** S. Prompt + one read module. No UI change.

## Step 2 — Propose, then code places: holdings compete, conviction sets the size

**What:** the research turn stops placing orders. It calls a new tool, `propose_paper_orders`,
with one entry per idea:

- `symbol`, `side` (buy / add / trim / sell), `why` (one sentence), `conviction` (low / medium /
  high), and for a buy the case in two or three sentences;
- **a current holding is a candidate on the same terms** — "add 3 points to Marvell" competes
  with "open Vertiv" for the same cash.

Code turns conviction into a target weight (proposal: low 4%, medium 7%, high 10% of the
portfolio, never above the mandate's per-holding cap, never below the cash floor), computes the
quantity, and places the order through the existing choke. Sells keep their own rule: a broken
limit is sold first, in full, without waiting for the later steps.

**Where:** `ai/tools/paper_trading.py` — `propose_paper_orders` (`mutates=("paper",)`, writes
only the running review's draft; refused outside a review, like `record_autopilot_memo`).
`services/paper_trading/autopilot_sizing.py` — conviction → quantity (all money math in
services). `autopilot_run.review` — after the turn, read the draft, size, place.
`place_paper_orders` leaves the review's tool offer (the owner and chat still use it).

**Done when:** a review that wants to add to a holding and open a new name gets both sized by
the same rule; a proposal that would breach the cap is trimmed to fit, not refused; the memo
lists every proposal and what happened to it. Tests: sizing table, cap/floor clamps, a
breaking-limit sell still goes first.

**Size:** M. One tool, one service, run-loop change, History shows proposals.

## Step 3 — Challenge turn: the case against each buy

**What:** a second, separate turn gets each proposed buy/add with the research behind it and
argues the strongest case **against** it (valuation, crowding, recent news, what the market
already prices in). For each it records `keep`, `shrink` (one conviction step down) or `drop`,
with a one-sentence reason. Few tools: reads, fundamentals/technicals, at most 3 searches.

**Where:** `record_challenge` tool (draft-only, like the memo). `autopilot_run` runs the
challenge turn between research and placing, with its own small budget.

**Done when:** History shows each buy with its bull case and bear case side by side, and the
outcome. A `drop` never places; a `shrink` places one step smaller. Tests: each verdict's
effect on sizing; a failed challenge turn falls back to "shrink everything one step" (fail
safe, never fail open into the full size).

**Size:** M. Costs one extra model turn per review that proposes a buy.

## Step 4 — Risk turn: the orders as a set

**What:** a third turn sees the whole set after the challenge — the portfolio as it would be
after the orders — and checks what single-name rules miss: two names that are the same bet
(sector, theme, a shared customer), how much of the book rides on one story, how much cash is
left for the next dip. It records `approve` or `trim` per order, with the reason. It cannot add
orders or raise a size.

**Where:** `record_risk_review` tool; the turn runs last, before code places. Reads only, no
searches.

**Done when:** a set of buys that each pass the cap but together put 50% into one theme gets
trimmed with a stated reason. Tests: trim-only (it can never grow an order), a failed risk turn
places nothing new that review (sells for broken limits still go).

**Size:** M. One more model turn, only when there are buys left after step 3.

## Cross-cutting

- **Feature flag** `autopilot_committee` (`core/feature_flags.py`), off by default: off = today's
  single-turn review, untouched. Step 1 (memory) ships without a flag — it only adds context.
- **Cost:** up to three turns per review instead of one. Settings already pick the model and
  effort; the challenge and risk turns default to the same model at `low` effort.
- **Time:** the whole review keeps its 10-minute ceiling; each turn gets its own budget.
- **Safety unchanged:** paper-only guard, mandate enforced at the choke, no new outbound tools.
- **UI:** the Autopilot tab's History gains, per review, the proposals with their bull case,
  bear case, risk note and outcome. One expandable row per review; the memo stays the summary.
- **Docs:** `docs/product/features/paper-trading.md` per step; one ADR for "the model proposes,
  code sizes and places" (a durable decision about who owns order size).

## Order and checkpoints

1. Memory — build, then run one review and read the memo together.
2. Propose + sizing — build behind the flag, run with it on for AI Theme.
3. Challenge — build, compare a week of reviews with and without it.
4. Risk — build last; it matters most once there are several buys a review.

## Questions for you

1. **Sizes:** are 4% / 7% / 10% for low / medium / high conviction right for AI Theme's 20% cap?
2. **Veto:** may the challenge turn drop a buy outright, or only shrink it?
3. **Cost:** OK for a review that buys to take up to three model turns?
4. **Flag default:** keep the committee off until you have compared a week of reviews?

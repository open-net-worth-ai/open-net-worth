# Paper Trading: mandate and autopilot (proposal, 2026-11-17)

Status: proposal, not built. Paper first; nothing here touches the real book or a broker.

## How an investment team decides when to trade

| Step | What a fund does | Who |
|---|---|---|
| Mandate | A written policy (an Investment Policy Statement): goal, benchmark, what it may buy, limits (max per name, per sector, cash range, max drawdown), how often it is reviewed. | PM + committee, rarely changed |
| Thesis per position | Why we own it, entry range, fair value or target, stop, **what would prove us wrong**, a review date. | Analyst |
| Pitch and approve | An idea is pitched with its thesis and size; the committee says yes, no or smaller. | Analyst → committee |
| Pre-trade check | Every order is checked against the mandate before it goes out (compliance). Mechanical, not a judgement call. | Compliance system |
| Execute | Orders are placed and worked. | Trader |
| Monitor | Alerts on price levels, thesis breaks, limit breaches, drawdown; scheduled reviews (weekly, monthly); rebalance when a weight drifts past its band. | Risk + PM |
| Review | A decision journal: what we thought, what happened, was the thesis right; attribution against the benchmark. | Everyone, quarterly |

## How it maps onto ours

| Fund step | Ours today | To build |
|---|---|---|
| Mandate | Nothing at portfolio level. | **Mandate** on a paper portfolio (below). |
| Thesis per position | Per-ticker investment theses already exist (buy zone, stop, trim, fair value), and each paper fill already records its stance at trade time (`paper_trading/thesis.py`). | Add "what would prove it wrong" and a review date; reuse as is. |
| Pitch and approve | You ask Lucius in chat. | Autopilot **Suggest** mode: proposals you approve. |
| Pre-trade check | Cash and quantity checks only. | Deterministic mandate check in the paper choke: refuse or flag an order that breaks a limit. Server code, never the model's judgement. |
| Execute | The paper fill engine. | Nothing new. |
| Monitor | Price alerts, Intel. | Autopilot's triggers: schedule, buy zone reached, stop hit, drawdown, drift past band. |
| Review | Activity with its Why; buy-zone hit rate. | Decision journal per autopilot run; "You vs Lucius" (parked doc). |

## 1. Mandate: the thesis of the portfolio

Stored on the paper portfolio (`settings.mandate`, additive), edited in **⋯ → Edit** and shown as a
**Mandate** tab on the page. Lucius reads it on every paper turn about this portfolio.

- **Goal**, in words: "Beat QQQ over a year with AI infrastructure names."
- **Benchmark** (exists).
- **Universe**: tickers, a watchlist, sectors, or "anything".
- **Limits**: max weight per name (e.g. 15%), per sector (40%), cash between 5% and 30%, max open
  positions, stop rule (sell a name down 20% from cost unless its thesis says otherwise).
- **Rebalance band**: act when a weight drifts more than N points from target.
- **Review cadence**: weekly, monthly.

The pre-trade check reads the limits: a manual order that breaks one asks "this breaks your
mandate (ACME would be 22%, limit 15%), place anyway?"; an autopilot order that breaks one is
refused.

## 2. Autopilot: a feature of the paper portfolio, not an Automation

On the portfolio page, an **Autopilot** control with three states:

| State | What happens |
|---|---|
| **Off** (default) | Only you and chat requests trade. Today's behaviour. |
| **Suggest** | On each review, Lucius writes a short memo and proposes trades. They wait as **Proposals** on the page: Approve, Edit, Reject. Nothing fills until you approve. |
| **Auto** | Same memo; the trades that pass the mandate check are placed. You see them in Activity, each with its Why and the memo it came from. |

- **When it runs**: the mandate's review cadence, plus triggers: a holding reaches its buy zone,
  trim level or stop; drawdown past the limit; a weight drifts past its band; earnings.
- **Hard limits** (server-side, not the model): only this portfolio; only within the mandate; max
  trades per run; one run at a time; a model-cost ceiling per month; pause on two failed runs.
- **Kill switch**: Off stops it at once; open orders it placed stay until you cancel them.
- **Every run leaves a memo** (what it looked at, what it decided, which rule each trade cites,
  and "no trades" runs too), so the review step has something to read.

Under the hood it can reuse the headless-turn runner and the scheduler that Automations use (one
engine, already sandboxed to Paper only), but the user never sees or manages it as an Automation:
it lives on, and is deleted with, the portfolio.

## Order to build

1. **Mandate** (fields, Edit, Mandate tab, Lucius reads it, pre-trade check). Useful without
   autopilot: it makes your own and Lucius's chat trades consistent.
2. **Autopilot Suggest** with proposals and memos.
3. **Autopilot Auto**, once Suggest's proposals look right.
4. **Review**: decision journal, mandate-breach log, thesis hit rate, You vs Lucius.

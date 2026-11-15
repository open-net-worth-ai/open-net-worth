---
title: Paper trading is a simulated book, never an order path
status: Accepted
date: 2026-11-15
deciders: implementing Claude agent + repo owner (design approved 2026-11-15)
---

# Paper trading is a simulated book, never an order path

## Context

The owner wants to try trades, copies of followed portfolios and Lucius-run strategies with
pretend money at real market prices, for every kind of investable asset, before any real money
moves (`docs/design/paper-trading/PRD.md`). Two earlier decisions constrain how:

- ADR `2026-07-19-remove-alpaca-trading-half` removed the app's own order path: real orders go
  through a broker's MCP tools, each behind that tool's own permission.
- ADR `2026-11-10-followed-portfolios-are-information-copying-is-an-automation` rejected a
  "second ledger fed by guesses" that mirrored a real broker account the app could only infer.

Every real figure (net worth, cash on hand, Ready to Assign, Investments, Intel risk, theses'
held state) is computed from accounts, transactions, positions and lots. A paper figure in any
of them is the worst failure the PRD names.

## Decision

We will build paper trading as a **separate simulated book**: its own tables (`paper_portfolios`,
`paper_contracts`, `paper_orders`, `paper_events`, `paper_values`), its own write choke
(`services.paper_trading.service.apply_paper_change`, one `locked_unit_of_work` over the paper
tables), and its own fill engine priced from the market data the app already fetches.

- **Never an order path.** No code in `services/paper_trading` can send an order anywhere. It has
  no connector, no `external_effect`, and no broker client.
- **Never part of the real book.** It never writes `accounts`, `transactions`, `positions` or
  `lots`. Two import-linter contracts enforce it: only its router, its Lucius tools, the
  Automations service and the scheduler import it; and it imports no real write choke (ledger,
  lots, positions, accounts, planning, options, owned assets, aggregation). Today's holdings
  reach it only through the read-only `services.holdings_snapshot`. A property test shows that
  random paper operations leave every real figure byte-identical.
- **The append-only event ledger is the truth.** Holdings, lots, cash per currency and realised
  P&L are always derived from `paper_events` (`book.derive`), using the same pure lot math as
  the real book (`services.lot_math`, lifted out of `services.lots`).
- **Every asset kind is an adapter** behind one protocol (quotes, session, decide, value,
  lifecycle). Adding a kind adds an adapter, not a branch.

Why this is not the rejected "second ledger": that ledger mirrored a real account the app could
only guess. A paper book is the only authority on its own trades; nothing in it is guessed.

## Consequences

### Positive
- The owner and Lucius can rehearse a trade or an Automation with no path to real money.
- Paper and real can never disagree about lot math or compare figures: both call
  `services.lot_math` and `services.portfolio_compare`.
- Paper writes can be seeded Always allow (`SIMULATION_DOMAINS`), since no money moves.

### Negative
- A second, smaller valuation path exists (adapters valuing paper holdings). It must stay out of
  every real-figure reader, which the contracts guard but a reviewer still has to keep in mind.
- Fills are simulated against the latest quote: no order book, no partial fills. The help page
  and every fill's recorded basis disclose it.

### Follow-ups
- Phase 2 (options, bonds, cash yield) and phase 3 (margin, shorts, futures) add adapters only.
- `simulation_only` Automations get a dispatch-time refusal of every non-paper write.

## Alternatives considered
- **A Real/Paper mode over the whole app** — rejected: every investment screen needs two sources,
  and mode confusion is how real orders get placed by mistake.
- **Paper as an account type** — rejected: the seven account types are the only classification
  axis and every account counts in net worth.
- **A broker's own paper account (Alpaca paper) as the engine** — rejected: US-only, needs an
  account, covers one broker's assets; "every kind of asset" rules it out.
- **A standalone paper-broker MCP server** — rejected: it would copy prices, FX, instruments,
  theses and storage, and a `place_order` on two servers blurs paper and real. The inbound MCP
  already exposes the paper tools.

## Agent provenance

- **Decided by:** implementing Claude agent, following the owner-approved PRD/RFC
- **Lane / branch:** `claude/paper-trading-3c6b9e` (worktree `paper-trading-3c6b9e`)
- **Alternatives weighed:** whole-app mode, account type, broker paper account, separate MCP
- **Driving artifacts:** `docs/design/paper-trading/PRD.md`, `docs/design/paper-trading/RFC.md`
- **Why this way (one line):** a separate book with import-enforced isolation is the only design
  where no bug in paper trading can touch a real figure or reach a broker.

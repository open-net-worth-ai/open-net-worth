---
title: An investment account is valued by its brokerage total or its live holdings, and a linked total syncs without AI
status: Accepted
date: 2026-11-10
deciders: orchestrating agent (Claude) + repo owner
---

# An investment account is valued by its brokerage total or its live holdings, and a linked total syncs without AI

## Context

A user linked a brokerage through Plaid. Plaid reported the account at a six-figure total, but the
app showed $0.00 and left it out of net worth. Plaid's Transactions product doesn't cover investment
accounts, and the app values an investment account as **holdings + ledger balance**. With no
holdings and no transactions, both halves were zero. Linking did nothing for the account.

The same formula causes a quieter bug on unlinked accounts. A user types a $12,345.67 balance, the
brokerage's total, when they create the account. Adding a holding later with no funding account
counts that holding on top, so the account reads about twice its real value (#70).

Most people know their brokerage's total. Few will enter every position. The app must be right in
both cases, and must not ask the user to classify their own number. The owner settled the model and
its wording in a clickable prototype (2026-11-07 to 11-09):

- Typing "the current balance" of an investment account means the **total**, holdings and cash
  together.
- Adding a holding later must neither ask a question nor count twice.
- Names: **Brokerage total** and **Live holdings**, chosen in Edit account under "Value this
  account by".

ADR `2026-09-05-connectors-are-data-sources-lucius-books` removed scheduled connector ingest, so that
only Lucius books what a connector returns. YNAB's two-way sync is the one recorded exception
(`2026-10-20`).

From Plaid's docs, 2026-11-09:

- On `/accounts/get`, `balances.current` for an investment account is "the total value of assets as
  presented by the institution".
- That endpoint is free. Its balances are cached and update "about once a day" while the Item is
  healthy.
- `last_updated_datetime` is not populated for brokerages.

## Decision

We will value every investment and retirement account by one of two **value sources**, stored on the
account as `value_source`:

| | Brokerage total (`total`) | Live holdings (`holdings`) |
|---|---|---|
| The account is worth | the brokerage's total | the sum of its holdings plus cash |
| Holdings are | a breakdown of that total | the source of the value |
| The ledger balance is | the part of the total not tracked as holdings | the account's cash |
| Set by | the user, or the linked connector | the user |

Valuation stays **one formula**: holdings + ledger balance, on every surface. Only what the ledger
balance means differs.

1. **Default.** Each account gets its source once, and it's then stored. A later holding can never
   flip it.
   - A new account starts as Brokerage total. The Add account field for these types reads "Total
     value".
   - An existing account with no stored source reads as Live holdings if it holds anything.
     Otherwise it reads as Brokerage total.
   - So does a linked account at an institution split across several accounts in the app, because
     the connector's total would count the others twice.
   - The first write that depends on the source stores it.
2. **Carve-out.** On a Brokerage total account, a lot write that moves no money keeps the total where
   it was. Its change in holdings value, at last-known prices, is booked as a `kind=adjustment` from
   or to the untracked part.
   - "Moves no money" means: an acquire with no funding account, deleting or amending a lot with no
     linked purchase, or undoing a disposal with no proceeds leg.
   - A write with a cash leg changes nothing extra. The leg itself moves the total correctly: from
     another account (in) or to another account (out).
   - A sale on a Brokerage total account with no proceeds account credits the proceeds to the same
     account, because the cash stays at the brokerage.
3. **Linked total sync.** A Brokerage total account linked through a connector that reports balances
   (Plaid, SimpleFIN, Yodlee, Alpaca, YNAB) follows that connector's figure.
   - A scheduler tick (`linked_account_totals`) runs at most every 6 hours, and within a minute for
     an account that has never been read.
   - It reads each provider's free account list. For Plaid that's `/accounts/get`.
   - When the reported total differs from the last one applied, it books one adjustment:
     `target = total − holdings`.
   - An unchanged total writes nothing, so between reads the tracked holdings move with the market.
   - The sync writes only through `ledger_service.create_transaction`, under the audit surface
     `account_value_sync`. It books no transaction rows from the provider and calls no model.
   - A Live holdings account's reading is recorded for comparison and never written to the ledger.
4. **Safety.** Each of these writes nothing:
   - A missing balance, a currency that differs from the account's, or a provider error. The last
     applied value stands, and the state is shown on the account.
   - A reported 0 that would change the value, a drop of more than a quarter, or a jump past 4×.
     The reference is the last applied total, or the account's current value for the first
     reading on a link, so linking the wrong provider account can't silently rewrite a typed
     total. An account worth nothing yet takes its first total. The reading is held back until
     the user confirms it (`POST /api/accounts/{id}/value-sync/confirm`).
   - A currency that's missing or isn't an ISO code (Plaid's crypto codes, SimpleFIN's custom
     currencies) is never treated as the account's own. A non-finite balance is "no balance".
     A reading that fails is recorded, so it isn't retried every minute.
5. **Reconcile** stays on every account. On a Brokerage total account it asks for the real total and
   books `total − holdings − balance`. On a linked one, the connector's next changed figure replaces
   it.
6. **Switching sources.**
   - To Brokerage total: no write.
   - To Live holdings: an untracked part of 2% of the total or less is kept as cash. A larger one is
     zeroed by one adjustment, after a warning, because it isn't cash.
7. The state lives on the account row: `value_source` (caller-settable) and `value_sync`
   (server-owned, no credential). The sync's last run time lives in `runtime_state`, namespace
   `account_value_sync`, which checkpoints exclude. Everything is behind the release flag
   `investment_value_source`. With it off, nothing reads the new fields and the old code runs.

This amends `2026-09-05` for **balances only**. A connector's transactions are still booked only by
Lucius. Whether Plaid gets a built-in transaction sync is still open (#67, PLD-4).

## Consequences

### Positive

- Linking a brokerage is enough to count it in net worth, with no model configured.
- A typed total and holdings added later never count twice.
- One formula still values every account, so net worth, the Dashboard and Lucius can't drift apart.
- Partial holdings work: the total stays right while the user adds positions at their own pace.

### Negative

- A Brokerage total account's untracked part is still reported in the `settlement_cash` band (for
  investment-type accounts). The rebalance plan and Lucius's class breakdown read it as brokerage
  cash. Net worth is right; that label isn't. A dedicated band is follow-up work.
- The carve-out commits after the lot write, not in the same database transaction. If it fails, the
  write result says so and the account reads high by that holding's value until the user reconciles.
- The total is as fresh as the connector's cache: about a day for Plaid, and less often for some
  retirement plans.
- Between reads, the displayed total drifts with the tracked holdings' prices. The account card
  explains it.

### Follow-ups

- A dedicated net-worth band for untracked holdings, read by allocation, rebalance and Lucius.
- PLD-9 (import holdings from Plaid) switches an account to Live holdings when it imports.
- Measure Plaid's real refresh cadence on a live investment Item. The docs say only "about once a
  day".

## Alternatives considered

- **Switch to holdings at the first holding.** Rejected because entering one of ten positions would
  drop the account's value until the user finished.
- **Ask, at the first holding, whether the typed number was the total or the cash.** Rejected by the
  owner: a question at that moment is confusing, and the typed number is the total.
- **A "statement value" mark beside the ledger, the way owned assets are valued.** Rejected because
  it's a second valuation formula, so every surface would need to learn it.
- **Treat the typed number as cash.** Rejected because it's wrong for the user who typed the total,
  which is nearly everyone.
- **Use `/accounts/balance/get` for fresher balances.** Rejected because it's billed per call. It
  could come later as an opt-in.
- **Store the sync state in its own table.** Rejected because it's one small value per account with
  no credential. Keeping it on the account row keeps it in backups and needs no migration.

## Agent provenance

- **Decided by:** orchestrating Claude agent, with the owner choosing the model and names through
  the prototype.
- **Lane / branch:** `worktree-plaid-products-and-sync`.
- **Alternatives weighed:** first-holding switch; ask at first holding; valuation mark; typed number
  as cash; billed real-time balance; own table.
- **Driving artifacts:** issues #67, #68, #69, #70; the user report of a $0.00 linked brokerage.
- **Why this way (one line):** the number people type and the number brokerages report are both the
  total, so the total is the default and holdings are carved out of it.

## Amendment 1 (2026-11-10): findings of the user-acceptance pass

A 250-scenario user-acceptance pass, written as user journeys with exact figures, found these gaps
before merge. The owner approved each fix.

- **Every remove goes through the carve-out.** `DELETE /api/positions/{id}` called
  `remove_position` directly and dropped a Brokerage total account by the holding's value. It now
  uses `apply_position_change("remove")`. A remove whose carve-out can't be booked reports
  `value_carve_out_error`, as a lot write does, instead of raising after the holding is gone.
- **A moved holding keeps both totals** (owner decision). Moving a position to another account
  moves no money. Each Brokerage total account on either side books a **Holding moved — total
  kept** adjustment; a Live holdings account it joins grows by the holding's value.
- **An unpriced holding is trued up when priced.** A holding with no price yet is carved out at
  cost (or 0). `services/account_value_cost.py` remembers that figure, and the scheduler tick
  books the difference once, as **Holding priced — total kept**, when the first price lands.
  Before this, the account drifted by (market − cost) × quantity for good: 5 ACME bought at 400
  and priced at 604 read 9,020 against an 8,000 total. The remembered figures are runtime state,
  and they're dropped when the holding is gone or the account is no longer Brokerage total.
- **A switch is one database transaction** with the rest of the account edit: `plan_switch`
  reads before it, and `write_switch` writes the source and the adjustment on the edit's
  connection. Before, the source was stored before the adjustment was booked.
- **Sync holds:**
  - A negative reading is held unless the last applied total was negative.
  - A figure the user kept their value over is forgotten once the feed reports the applied total
    again, so a later genuine 0 is asked about instead of ignored for good.
  - A missing reported currency fails closed even against an account with none stored.
  - Yodlee marks a currency it inferred (`currency_inferred`), and the sync treats it as missing.
- **`real_total` refuses NaN and Infinity** at the REST body and the tool input.
- **An amended sale keeps its cash home too.** `dispose_amend` rebooks the sale from the edit's
  own fields, so on a Brokerage total account it gets the same rule as a sale: no proceeds account
  means the cash stays in the account. Before, amending such a sale dropped the account by its
  proceeds.
- **Lucius is never told to book the cash twice.** `acquire_lots` on a Brokerage total account
  with no funding account no longer says "REQUIRED: call add_transaction"; the total already holds
  the purchase. `add_positions` (and `POST /api/positions`) now return the carve-out, or why it
  failed, as lot writes do.
- **One sync run at a time.** A second `run()` while one is in progress skips instead of booking
  the same difference twice. `max_instances=1` covered only the scheduler's own tick.
- **The screens refresh.** A switch that books an adjustment, and **Use** on a held total,
  broadcast a ledger change, so net worth updates on every screen without a reload.

Known limitation, left open:
- **A user edit landing mid-tick.** If a holding or opening balance changes in the milliseconds
  between the tick's read of the account and its booking, the booked difference is off by that
  change until the connector's next changed total corrects it. The window is a few milliseconds
  every 6 hours. Two strict xfail tests document it (UAT ROB-01, ROB-02). Closing it means
  reading and booking under one unit of work that holds the positions, lots and transactions
  locks.

Not changed, by design:
- **Share vests.** Both live vest paths mint the lot on their own connection, so a vest raises the
  account's value and books no transaction, as CLAUDE.md requires. A test pins it.
- **Switching with holdings worth more than the total.** The account becomes worth its holdings;
  the negative untracked part isn't cash.
- **"Not updating" after 7 days.** An investment total unchanged for 7 days means a stuck feed.
- **Booking only on a changed figure.** Re-booking every tick would chase tracked holdings' price
  moves; the next changed figure corrects any drift.

## Amendment 2 (2026-11-10): the untracked part is its own band, Untracked holdings

The first version filed a Brokerage total account's ledger balance under `settlement_cash` (or
`retirement` for a retirement account), the band every reader takes as cash. A linked brokerage
at $123,456.78 with no holdings entered read as 100% settlement cash on the Dashboard, the Assets
card, the allocation, the rebalance plan and in Lucius's answers. Net worth and cash on hand were
right; only the bucketing was wrong. The owner approved this design.

- **The band.** With the flag on, a Brokerage total investment or retirement account's ledger
  balance bands as `untracked_holdings`: invested at the brokerage, holdings not entered, no asset
  class. The one seam is `ledger.account_kind_and_band(account, untracked)`, with the set from
  `services/untracked_holdings.account_ids`, which asks `account_value.source_of`. A Live
  holdings account is unchanged. There are no cash holdings in the data model, so on a Brokerage
  total account all of the ledger balance is untracked: settlement cash there is only cash the
  app knows about, which today is none.
- **History uses the current source.** Every chart point bands each account by its value source
  today, so switching an account doesn't make the chart jump on that day.
- **Retirement stays retirement.** A retirement account's untracked part is in `retirement_base`
  and is subtracted by `net_worth_excl_retirement` as before. `retirement_untracked_base`
  (present only when non-zero) names that part so the Assets cards can split Retirement from
  Investments.
- **Readers.** The Assets Investments and Retirement cards show an **Untracked holdings** row,
  and the composition counts it under the right card. The Dashboard allocation counts it as
  invested (`portfolio_rows` gives the account row the band). The net-worth chart draws it as its
  own band. The report and the mention descriptor label it, and Lucius's snapshot and tool
  descriptions say it's invested, not cash.
- **Book-level figures leave it out and say so.** The investments book (weights, sections,
  concentration, today's move), the Overview's drift and `rebalance_plan` are worked out over
  broken-down holdings only. An unknown slice in the denominator would make every known class
  look underweight. Each reports `untracked_excluded` (one fold of the projected rows, so no
  second valuation). `project_invested`, `concentration_by_name` and `today_move` are unchanged:
  they already took only instrument-backed rows. The Investments tab shows a **Not broken down**
  slice (its share of everything invested) and a line under Concentration and Rebalancing.
- **Flag off** leaves every output as it was. The new keys are absent, not zero.

This resolves the first Negative consequence and the first follow-up above.

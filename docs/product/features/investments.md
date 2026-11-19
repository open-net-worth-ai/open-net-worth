---
title: Investments
description: Add holdings, record buys, sales and dividends, and track performance, income and options across every account.
category: features
order: 90
audience: [user]
status: stable
related: [features/equity-compensation, features/accounts, concepts/net-worth, concepts/base-currency-and-fx, concepts/positions-lots-and-cost-basis]
keywords: [investments, holdings, add holding, buy, sell, dividends, options, portfolio, performance, twr, mwr, rebalance, lots]
---

# Investments

Investments shows everything you own in brokerage, retirement and crypto accounts — what it's
worth, how it's performing, the income it pays, and every trade behind it. It uses the same
holdings as the rest of the app, so it always agrees with your net worth.

There are three ways to get your holdings in:

- **Type them in** with **Add** on the Holdings tab (below).
- **Tell Lucius**: *"I bought 10 VTI at $245 in my Fidelity account yesterday, paid from my
  checking."*
- **Import a broker statement** or **connect a brokerage** — see
  [Importing statements](importing.md) and [Bank & brokerage connections](../connectors/bank-brokerage.md).

## Add a holding (record a buy)

1. Open **Investments → Holdings** and click **Add** (or **Add holding** when you have none yet).
2. Fill in:
   - **Symbol** — the ticker, e.g. `AAPL`.
   - **Account** — the brokerage account that holds it. You can type a new account name.
   - **Acquired date** and **Quantity**.
   - **Unit cost** *or* **Total spent**, in the currency you paid, plus any **Fees**.
   - **Kind** — **Buy**, **Dividend reinvest**, **Transfer in**, **Grant** or **Opening
     balance** (use Opening balance for shares you've held since before you started tracking).
   - **Note** — optional.
3. **Deduct from** — pick the cash account the money came from to book the payment there too, or
   leave it on **Don't deduct from an account** (your cash balance won't change).
4. Click **Add holding**.

Each buy becomes a *lot* with its own date and cost, which is how gains are worked out when you
sell. Adding more of something you already own adds another lot.

**Cost in a different currency.** Enter the cost in the currency you actually paid, even if the
stock is quoted in another (a USD stock bought with SGD keeps its SGD cost). Historical cost is
never converted at today's rate. If you don't know the cost, leave it out rather than guessing.

## Record a sale

1. On **Holdings**, open the **⋯** menu on the holding's row and click **Record sell**.
2. Enter the **Quantity**, **Proceeds / unit**, **Fees** and **Date**.
3. **Lot selection** — leave it on **FIFO** (oldest lots first) or switch to **Specific lots** and
   enter how many to sell from each lot.
4. **Deposit proceeds to** — pick the cash account the money landed in. If you leave it on
   **Don't add to an account**, the sale is recorded but no cash is added, so your net worth drops
   by the sale value. The cash row it adds says what you sold in its description — **Sold 4
   MSFT @ 550.00 USD**. A buy with **Deduct from** set does the same: **Bought 10 VOO @ 500.00 USD**.
5. Click **Record sale**. The realised gain or loss is shown straight away.

**Moving shares between your own accounts, gifting them, or writing them off?** In the same form,
tick **Not a sale — transferring out, gifting, or writing off**. For a move to another of your
accounts, choose it under **Moved to**, then add the shares there with **Add** (Kind **Transfer
in**, same cost). Activity shows a move as one **Transfer** row, not a sale.

## Record a dividend or interest payment

1. On **Holdings**, open the row's **⋯** menu and click **Record dividend**.
2. Choose the **Type** (Dividend, Interest or Distribution), then enter the **Gross amount**, any
   **Tax withheld**, and **Paid on**.
3. Pick the **Cash account** it was paid into — or tick **Reinvest (DRP)** and enter the **Units
   reinvested** to add a new lot instead of cash.
4. Click **Record**.

To correct or delete one, open the holding's **Position** tab and click the payment under
**Dividends**. You can change its date, type, gross amount, tax withheld, note and the account it
was paid into. Its cash transaction is corrected to match and keeps its category and cleared
status. **Don't add to an account** removes that transaction; the form says so before you save.
**Delete** removes the payment and its cash transaction. A cash transaction you turned into a
transfer in the ledger stays either way. A reinvested dividend has no cash: deleting it leaves
its shares as a lot, which you correct or remove under **Acquisition lots**.

## The summary at the top

| Figure | What it is |
|---|---|
| **Market value** | What your holdings are worth right now, in your base currency. It is not the money you put in: your cost is what **Unrealized P&L** is measured against. |
| **Today** | How much your holdings moved today, as an amount and as a percentage. It uses the latest price, including a pre-market or after-hours one while that session is trading. |
| **YTD vs benchmark** | Your return since 1 January beside your benchmark's. |
| **Unrealized P&L** | The gain or loss on what you still hold, against what you paid. |

## The tabs

| Tab | What it shows |
|---|---|
| **Holdings** | One row per holding: value, weight, profit/loss, price and today's move. Filter by asset class or account, group by class, account or currency, and click a row to open the asset's page (chart, news, and **Position** with every lot). |
| **Watchlists** | The tickers on your [Intel](intel.md) watchlists, as cards or a table. |
| **Analysis** | **Allocation** (it becomes **Allocation vs target**, with target markers and drift, once you set a mix with **Set targets**; until then it shows no target), **Sector & geography**, **Concentration & risk** (set your **Single-name guideline** under **Risk settings**; a diversified fund such as an all-world index ETF is not one company, so it is never flagged; the **20%** guideline is a default, and the Dashboard's note states the share instead of telling you to trim) and **Rebalancing**, which proposes trades toward your target. On a wide screen this sits beside Holdings instead of being a separate tab. |
| **Performance** | **TWR** (time-weighted — your investment choices alone), **MWR** (money-weighted — includes when you added or withdrew money) and **Period gain**, with a chart you can compare against a benchmark. |
| **Income** | Recent dividends and interest, where your income comes from, whether it's growing, and **Upcoming payments**. |
| **Activity** | Every buy, sale, dividend and transfer, filterable by type and date range, with search. |
| **Options** | Option contracts you've written or bought (see below). |
| **Equity Compensation** | RSU/ESOW/LTIP grants and vests — see [Equity compensation](equity-compensation.md). |

The benchmark you pick on **Performance** becomes your benchmark everywhere, including Intel's
briefs.

## Options

1. Open **Investments → Options** and click **Record option**.
2. Pick a **Strategy** — Covered call, Cash-secured put, Long call, Long put, Protective put, or
   Custom (then choose **Type** and **Side**; short means you wrote/sold it).
3. Enter the **Underlying**, **Account**, **Strike**, **Expiry**, **Contracts**, **Multiplier**
   (shares per contract), **Premium / share**, **Currency** and **Open date**.
4. **Book premium cash leg to** — pick an account to record the premium received or paid as a
   transaction, or leave it on **Don't book a cash leg**.
5. Click **Record option**.

To close one, click **Close** on its row, enter the **Close date** and buyback or sale price, and
optionally a **Book cash leg to** account. **Delete** removes an entry recorded by mistake.

The app records the trade and takes no view on how it's taxed.

## What Lucius can do here

- Record a buy, sale, dividend, interest payment or share transfer from a plain description.
- Correct a recorded purchase — its date, quantity, unit cost, fees or kind — for example "I
  actually bought that VTI in March 2023" — even after you've sold some or all of it. Before you
  approve, the card shows how each sale's gain changes. Shares from a vest are corrected through
  the vest.
- Open, close, update or cancel an option, including its premium.
- Explain your allocation, suggest a rebalance, and answer concentration and risk questions.
- Work out realised gains, fees paid, and returns over any period, with or without a benchmark.
- List upcoming dividends and forecast your income.

## Links to Intel

- A small number beside a ticker means Intel has live stories about it (highlighted when one is
  unread). Tap it to open Intel at the newest story.
- Concentration and today's move are calculated once and shared, so Investments' risk card and
  Intel's risk stories always agree — including for a stock you hold at two brokers.

## Things that trip people up

- **A sale without "Deposit proceeds to" lowers your net worth** — the shares leave, but no cash
  arrives until you record it.
- **Moving shares onto a holding you already have is a transfer, not a move.** Moving a holding
  refiles it, with all its lots, under another account; if that account already holds the same
  stock, the move is refused. Record a transfer instead — a **Not a sale** disposal here, plus a
  **Transfer in** lot on the existing holding at the same cost. Tell Lucius the shares moved and
  he does both.
- **"Last 12 months" income only counts payments already made.** A declared dividend you haven't
  received yet isn't included.
- **To receive a dividend you must own the shares before the ex-dividend date.**
- **Unvested equity never appears here** — see [Equity compensation](equity-compensation.md).
- **A Brokerage total account's holdings you haven't entered aren't in the shares.** With the
  experimental **Brokerage total** valuation on, the rest of such an account's total is
  **Untracked holdings**: invested, but in no asset class. Allocation shows it as a **Not broken
  down** slice of everything invested, under the class bars. The class shares, concentration,
  today's move and **Rebalancing** are worked out over the holdings you've entered, because an
  unknown slice would make every class look underweight; each says how much it left out ("…
  isn't broken into holdings — add holdings to rebalance it"). With no holdings entered at all,
  Holdings says **No holdings entered yet** and names the amount. See
  [Accounts](accounts.md#investment-and-retirement-accounts-brokerage-total-or-live-holdings).
- **Bought shares already managed by lots or a grant?** Correct the lot (from the asset's
  **Position** tab) or the grant, not the holding's total.

## Related pages

- [Equity compensation](equity-compensation.md)
- [Accounts](accounts.md)
- [Intel](intel.md)
- [Net worth](../concepts/net-worth.md)
- [Base currency & FX](../concepts/base-currency-and-fx.md)
- [Positions, lots & cost basis](../concepts/positions-lots-and-cost-basis.md)

## Guides and comparisons

Longer write-ups on the Open Net Worth blog:

- [Ghostfolio Alternative: Open Net Worth vs Ghostfolio](https://opennetworth.ai/blog/ghostfolio-alternative/): portfolio trackers compared with Open Net Worth's holdings

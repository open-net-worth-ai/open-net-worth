---
title: Positions, lots, and cost basis
description: How Open Net Worth tracks what you hold and what you paid — holdings, lots, cost basis, which lots a sale uses, and realized versus unrealized gains.
category: concepts
order: 70
audience: [user]
status: stable
related: [features/investments, concepts/net-worth, concepts/base-currency-and-fx, troubleshooting/prices-arent-updating]
keywords: [position, holding, lot, cost basis, fifo, specific lots, realized gain, unrealized gain, add lot, record sale]
---

# Positions, lots, and cost basis

Open Net Worth tracks your investments at two levels:

- a **holding** (or position) — how much of one thing you hold in one account: "100 VTI in my
  brokerage", "0.5 BTC on my exchange";
- the **lots** inside it — each separate purchase, with its own date, quantity and cost.

Property, cars and other things without a market price aren't holdings — track those on the
**Assets** screen. See [Assets](../features/assets.md).

## Holdings

A holding's value is its quantity times the latest price, converted to your base currency. It's
worked out fresh every time you look, so it always reflects the current price. Prices come
from Yahoo Finance and CoinGecko; physical gold, silver, platinum and palladium are valued at
the spot price. See [Prices aren't updating](../troubleshooting/prices-arent-updating.md).

**A holding's quantity always comes from its records — you never type it in.** For shares you
own, it's the sum of its lots. For equity grants it comes from the grant: unvested units from
the vesting schedule, and vested-but-not-yet-delivered units from the vests waiting for release.
To change how much you hold, record what happened — a buy, a sale, a transfer, or a correction
to a lot — and the holding follows. Moving a holding to another account moves its lots with it.

A holding's **Position** tab opens with a **Held at** table naming the account it's in — one row
per account when you hold the same thing in more than one. Each row shows the quantity, market value, **Avg cost** per unit,
**Cost basis** (what you paid in total for that account's units, fees included) and unrealized
profit/loss. A per-unit cost under one unit of currency is shown at full precision, so a coin
bought at $0.0000241 never reads as $0.00.

## Lots: what you actually paid

Every purchase is a **lot**: an acquired date, a quantity, and a **cost basis** — what you paid,
fees included. Fees are spread evenly across the lot's units, never dropped. Lots are what let
the app show not just what a holding is worth, but how much you're up or down on each part of
it.

If you don't know what you paid (old shares, a transfer from another broker), leave the unit
cost blank. The lot still counts toward your holding, but no gain or loss is shown for it.

## Recording a purchase

Click **Add** on **Investments** (or **Add lot** on a holding's **Position** tab), then fill in
**Symbol**, **Account** (or **+ New account…**), **Acquired date**, **Quantity**, and either
**Unit cost** or **Total spent** — enter one and the other is worked out. **Kind** records how
you got it: Buy, Dividend reinvest, Transfer in, Grant, or Opening balance.

**Deduct from** is optional. Pick a cash account and the purchase is taken from it in the same
step. Choose **Don't deduct from an account** and only the lot is recorded — your cash balance
doesn't change, and the form tells you so.

The acquired date can't be in the future — a lot records shares you already hold. If you deduct
the cost from an account and the purchase is dated before that account's starting balance, the
form says its balance won't change: transactions before an account's starting balance are left
out of it (see [Accounts](../features/accounts.md)).

Full steps: [Investments](../features/investments.md).

**Added a holding through Lucius without a purchase date?** It's recorded as an **Opening
balance** lot dated the day you added it — you held it when tracking started, like an account's
starting balance. Your past net-worth history isn't rewritten, and returns count the holding at
its market value that day, so an old purchase price doesn't show up as a gain this month. When
you know the real date, click the lot and set it — or tell Lucius ("I bought that in March
2023") — and history and the holding period follow.

## Selling: which lots are used

A sale has to come out of specific lots, because different purchases cost different amounts.
On the holding's **Position** tab, click **+ Record sale**, then under **Lot selection**:

- **FIFO** (the default) — sells your oldest lots first ("first in, first out").
- **Specific lots** — you pick which lots and how much from each, for example to sell the ones
  with the highest cost for tax reasons. The amounts must add up to the sale quantity; if they
  don't, the form says *"the picked quantities must sum to the sale quantity"*.

**Deposit proceeds to** adds the money to a cash account in the same step, or choose **Don't add
to an account**. Transferring shares out, gifting or writing off works the same way — choose
the **Not a sale** option.

## Editing lots and sales

- **Acquisition lots** lists every lot you've recorded, oldest first — including ones you've
  sold completely, which stay in the list, greyed out and marked **Sold**, so your buy history
  never disappears. The header counts both, for example *1 open · 5 closed*. A sold lot has no
  unrealized gain; its gain is under **Realized history**.
- Click any lot to correct its date, quantity, cost, fees or kind — including a lot you've sold
  from. If you deducted its cost from an account, that purchase is corrected to match; changing
  only the note leaves it alone.
- Correcting a sold lot keeps every sale exactly as recorded (which lots it used, how many,
  the price and the cash). Only that lot's lines on the sale follow: a new date updates their
  holding period, and a new cost or fees updates the sale's gain. A sold lot can't be made
  smaller than what's already been sold from it — edit the sale for that.
- **Remove lot** is for a lot nothing has been sold from. To remove a sold one, undo the sale
  first.
- Click the small arrow on a sale under **Realized history** to see the lots it used: each
  lot's date, quantity, cost, holding period and gain. A sale that used lots with different
  holding periods shows its term as **Mixed**.
- Click a sale under **Realized history** to edit it. The app undoes the old sale and records
  the new one in one step, so lots, gains and cash stay in agreement.
- Sold everything and bought back later at the same account? The buy reopens the same holding,
  however it's recorded — the **Add lot** form, Lucius, or adding the position again — so its
  sales, dividends and sold lots stay with it. A holding split in two by a rebuy recorded
  before this still shows its whole past: the earlier sold lots under **Acquisition lots**,
  and the earlier sales and dividends under **Realized history**, **Realized P&L**,
  **Dividends** and **Dividends received**.
- An older lot whose purchase date was never known shows an empty **Acquired date** when you
  edit it. Saving a note or cost leaves the date unknown rather than setting it to today.

## Realized and unrealized gains

- **Unrealized gain** — what an unsold holding is worth now, minus what you paid. It changes
  with the price and is worked out each time you look.
- **Realized gain** — locked in when you sell: the proceeds minus the cost basis of exactly the
  lots that sale used. Only a sale creates one.

## Options and equity compensation

Option contracts (covered calls, for example) and equity compensation (RSUs, vesting grants) are
tracked too. Both are newer, **beta** areas — check their figures before relying on them. Unvested
shares don't count toward your net worth until they vest; see
[Net worth](net-worth.md#whats-left-out-and-why) and
[Equity compensation](../features/equity-compensation.md).

## Related

- [Investments](../features/investments.md) — adding holdings, lots and sales, step by step
- [Net worth](net-worth.md)
- [Base currency and FX](base-currency-and-fx.md)

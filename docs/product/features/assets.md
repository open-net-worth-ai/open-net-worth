---
title: Assets
description: Track what you own — property, vehicles, watches, art, metals, cash-value policies — with dated values, your share, and any loan against them.
category: features
order: 45
audience: [user]
status: stable
related: [features/accounts, features/dashboard, features/investments, concepts/the-ledger]
keywords: [assets, property, vehicle, watch, collectibles, gold, valuation, value, equity, loan, mortgage, disposal, sell]
---

# Assets

A house, a car or a watch isn't a bank account and has no price feed — its value is your best
estimate as of a date. The **Assets** tab keeps these things in one place, remembers every value
you give them, and counts each in your net worth at **your share**, minus any loan secured on it.

The page also shows your **Cash**, **Investments**, **Retirement**, **Receivables** and
**Debt** as cards at the top, so the whole picture is on one screen. **Debt** is everything you
owe on loans and credit cards — the same figure as **What you owe** — split into Loans and Credit
once you have both. Those open their own tabs — you manage them as [accounts](accounts.md), not
here.

## Add an asset

1. Open **Assets** and click **Add asset** (top right). You can also click one of the kinds
   listed under **Add more**, which starts with that kind already chosen.
2. **What is it?** — pick a category: **Property**, **Vehicle**, **Watch & jewellery**,
   **Art & collectibles**, **Precious metals**, **Insurance & endowments** or **Other**. Click
   the last tile, **New category**, to make your own. Prefer to talk? Click **Describe it to Lucius** and
   say something like *"my 2019 Rolex, bought for $9,000"* — Lucius asks for anything missing
   and adds it once you confirm.
3. **Name and value** — a name, and what it's worth today. Your best estimate is fine. For a
   cash-value policy, enter the **cash value** from your latest statement, not the sum assured.
4. **Is it all yours?** — keep **All mine**, or pick **Shared** and enter **Your share** and who
   you share it with. Net worth counts only your share.
   For property, vehicles and your own categories it also asks **Any loan against it?**:
   - pick the loan account from the list, or
   - click **+ Add a loan**, enter its name and **Amount owed**, and click **Add loan** — it's
     created as a Loan account in Accounts when you save the asset.
5. **How much did you pay?** (optional) — the price and the date you bought it. This adds your
   gain, yearly return and a purchase point on the value chart. For a policy it asks
   **How much have you paid in?** — premiums paid so far and when the policy started.
6. Click **Add asset**. The asset's page opens.

Use **Continue** and **Back** to move between steps. If something can't be saved — say a date in
the future, or a loan that already secures another asset — the step says why and keeps what
you typed.

## Record a new value

Values never change on their own — no index, depreciation or reminder is applied. When you
have a new figure (a valuation, an offer, a statement):

1. Open the asset and click **Update value** at the top.
2. Enter the new value. Change **As of** if it's for an earlier date, such as a statement date.
3. Add a note if you like, and save.

The old values stay in **Value history** and on the chart.

**Entered one wrong?** In **Value history**, click the row to open **Correct this value** — fix
the amount, date or note and click **Save correction**, or click **Remove value**. An asset always keeps at least one value, and a value can't be
dated before the purchase. Clicking the purchase row (**Bought**) opens the purchase details
instead.

## Fill in the rest of the record

The asset's page shows what you've filled in. Everything still missing is a row of **+** chips
underneath — **+ A note**, **+ Custom detail**, and others that fit the category (floor area,
plate number, serial number…). Click one to add it. Each section has its own **Edit**:

- **Ownership** — your share, who with, and the linked loan. Shows what's yours (equity) and
  what's owed.
- **Purchase** — price and date (for a policy, premiums paid). The chart's purchase point follows.
- **Details** — what identifies this kind of thing (plate number, tenure, serial number,
  insurer and policy number…), whether it's insured, when a policy matures, where it's kept,
  and any **Custom detail** of your own (a label and a value, like *Bezel · Rose gold*).
- **Notes** — anything worth remembering.

To rename it or move it to another category: **⋯** menu → **Rename or change category**.

## Link a mortgage or car loan

A loan stays an **account** under Accounts, so its payments live in your ledger. Link it from
the asset — in the add steps, or later in **Ownership** → **Edit** — and the asset's page shows
equity, loan-to-value and the loan's balance. A loan can secure only one asset at a time.

If the loan account doesn't exist yet, use **+ Add a loan**, or [add a Loan
account](accounts.md#add-an-account) first.

## Sell, dispose of or delete an asset

**Sold it, or gave it away?**

1. Open the asset → **⋯** → **Sell or dispose**.
2. Enter **Sold for** (for a shared asset, **Your share of the sale** — what actually reached
   you; it's prefilled with your share of today's value), the **Sold on** date, and the cash
   account under **Proceeds into**.
3. Click **Record disposal**.

This books a real transaction into that cash account on that date, archives the asset with its
history, releases its loan link, and drops it from net worth from that day. The gain is
measured against your share of what you paid.

If you later delete that transaction, the asset stays sold but its proceeds are no longer in your
ledger. Ask Lucius to sell it again, for example "sell my road bike again for 500 into Savings",
to book them back as the asset's sale. A sold asset no longer appears on the Assets screen, so
Lucius is the way back.

**Added it by mistake?** **⋯** → **Delete asset** removes the record and its whole history.

## Photos and documents

- **Photo** — click **Add a photo** on the asset's page, or **⋯** → **Add photo**. Click the photo
  to see it full size, change it or remove it.
- **Documents** — the asset's **Documents** tab holds its paperwork: a purchase agreement, title
  deed, receipts, warranty, photos. Click **Add file** (several at once is fine) or drop files
  anywhere on the asset's page. Images, PDF, CSV, Excel and bank statement files up to 25 MB
  each are accepted.
- Click a file to preview or download it. Its **⋮** menu lets you **Rename** it, **Add a note**,
  or remove it. The same files and notes appear in **Documents** in the sidebar.
- The **Lucius can read** switch decides whether Lucius may open the file. Lucius can always
  read your note.

## How assets show up in net worth

- Each asset counts at **your share of its newest value**, minus the loan linked to it.
- Once a category has **4 or more** assets, the register stacks them into one card — how many,
  their total, their gain and the three most valuable. Click it (or its filter chip) to see them
  one by one; **All** returns to the full list.
- **Asset Composition** and **Debt Composition** on the right show how your total splits up.
- Amounts can be typed as "820,000", "860k" or "1.2m", in your own number format. A figure in a
  different currency, or one the app can't read, is refused with a message. If a number could
  mean a thousand times more or less ("17,000" when your format uses a dot for thousands), it
  asks *"Did you mean 17.000?"* rather than guessing.

## What Lucius can do here

- List your assets, open one, answer "what is the flat worth net of the loan?".
- Add an asset from a description, record a new value, correct or remove a wrong one, edit
  details, create a category.
- Record a sale or delete an asset — both ask you to confirm first.

## Troubleshooting

**My home counts twice in net worth** — you probably also have it as an account. Archive the
old account once the asset is added.

**I can't pick my loan in "Any loan against it?"** — it already secures another asset. Unlink it
there first (open that asset → **Ownership** → **Edit**).

**What belongs here?** Only policies with a cash or surrender value — term, health and car
insurance have no value to track. Gold ETFs and stocks go in an Investment account; physical
bullion is an asset.

## Related pages

- [Accounts](accounts.md)
- [Dashboard](dashboard.md)
- [Investments](investments.md)
- [The ledger](../concepts/the-ledger.md)

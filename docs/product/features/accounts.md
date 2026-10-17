---
title: Accounts
description: Add every account you own or owe — bank, cards, loans, investments, retirement — and keep each balance right with reconcile, archive and delete.
category: features
order: 20
audience: [user]
status: stable
related: [concepts/the-ledger, concepts/accounts-and-opening-balances, concepts/net-worth, concepts/base-currency-and-fx, features/transactions, features/importing, connectors/bank-brokerage]
keywords: [accounts, checking, savings, credit card, loan, mortgage, opening balance, starting balance, reconcile, archive, delete]
---

# Accounts

The **Accounts** tab lists everything you hold or owe — bank accounts, cards, loans, brokerage
and retirement accounts, money people owe you. Each one has its own register of transactions,
and your net worth is built from these balances (plus the things you track on
[Assets](assets.md)).

The sidebar shows your **Net worth** at the top, then **All accounts**, then your accounts grouped
by type. Two or more accounts at the same institution fold together under the institution's
name. The figure beside **All accounts** is only your accounts added together, debts included. It
leaves out what you own on [Assets](assets.md), so it can differ from net worth, even go
negative while net worth is positive.

On a narrow window (a laptop, or Lucius open beside the register) the register sets the **Memo**
column aside first, then squeezes the others, then **Source**. A row that needs review shows a
small dot beside its amount while Source is away. Widen the window and every column you chose
comes back. If even that is too wide, the register scrolls sideways and the **Amount** column
stays pinned at the right edge, so amounts are always in view.

## Add an account

1. Open **Accounts** and click **Add account** (top right). On a brand-new book, click
   **Add an account myself** instead — or **Add with Lucius** to do it in chat.
2. **Name** — what you call it, e.g. "Everyday account".
3. **Account type** — click the type box to change it (it starts as **Cash**). Pick from the
   list, or type in the search box: "401k", "CPF", "mortgage", "fixed deposit" all find the
   right type. See [Which type to pick](#which-type-to-pick).
4. **Currency** — the currency the bank shows this account in.
5. **Current balance** (or **Amount currently owed** for a card or loan) — what it holds or
   owes today. Type `0` if it's empty or you'd rather import its history. Enter what you owe
   as a positive number; the app knows a card or loan is a debt.
6. Click **Add account**.

The balance you enter is recorded as a **Starting Balance** transaction dated today — it's the
first line of the account's register. The account appears in the sidebar straight away.

**Type and currency can't be changed later.** If you picked the wrong one, delete the account
and add it again.

Prefer to talk? Tell Lucius *"Add my DBS savings account, SGD, about 12,400 in it"* — it fills
in the details and asks you to confirm.

## Which type to pick

The type is the only thing that classifies an account. It decides whether the account is in
your budget, whether it counts as cash you can spend, and whether it's an asset or a debt.

| Type | Use it for | In your budget? |
|---|---|---|
| **Cash** | Checking, savings, e-wallets, physical cash | Yes — this is your spendable cash |
| **Fixed deposit** | Time deposits, locked savings | No |
| **Investment** | Brokerage or crypto-exchange accounts (holdings + their cash) | No |
| **Retirement** | CPF, SRS, pension, 401(k), IRA | No |
| **Receivable** | Money someone owes you | No |
| **Credit** | Credit cards, buy-now-pay-later, lines of credit | Yes |
| **Loan** | Mortgage, car, personal, student or margin loan | No |

Every type counts toward net worth. **Cash on hand** — the figure on the **All accounts**
overview — is the total of your Cash accounts only; cash sitting in a brokerage or
retirement account isn't included.

A house, car or watch isn't an account: add it on [Assets](assets.md) and link the loan account
that financed it.

## Fill in the details (optional)

Open the account, click the **⋯** menu in its header → **Edit account**. Everything here is
optional:

- **Institution** — groups the account with others at the same bank or broker.
- **Note** — anything you or Lucius should know ("joint account with Alex").
- **Last 4 digits** — helps Lucius and imports match the right account.
- **Credit details** (Credit accounts) — **Interest rate (APR, %)**, **Credit limit**,
  **Statement closes (day)** and **Payment due (day)**. With these filled in, the card's page
  shows when the next payment is due and how much, with a **Remind me** button.
- **Loan details** (Loan accounts) — **Interest rate (APR, %)**, **Original principal**,
  **Payoff date**, **Minimum payment**.
- **Interest rate (APY, %)** (Cash and Fixed deposit) — shows a rough monthly and yearly
  interest figure.
- **Linked to** — any bank connection feeding this account, with **Unlink**.

Click **Save changes**.

## Change the starting balance or start date

The edit window shows the **Starting balance** read-only, with an **Open** link. Click **Open**
to open the Starting Balance transaction and change its amount or date there.

The date is when your tracking starts: transactions dated **before** it are left out of the
balance. If you import older history, move the Starting Balance date back (and adjust its amount)
so the earlier transactions count. More in
[Accounts & opening balances](../concepts/accounts-and-opening-balances.md).

The Starting Balance transaction can't be deleted, merged or marked uncleared on its own — it
only goes away when you delete the whole account.

## Reconcile with your bank

When the balance in the app doesn't match your bank:

1. Open the account and click **Reconcile** (on a narrow screen it's in the **⋯** menu).
2. It asks *"Is the real balance …?"* showing the **cleared** balance — the part your bank has
   already posted. Pending (uncleared) transactions aren't included, because they're not on
   your statement yet either.
3. If it matches, click **Yes, it matches**. The account is marked **Reconciled** with today's
   date.
4. If not, click **No**, type the **Real balance**, check the **Adjustment** it shows, and
   click **Add adjustment**. The app books one adjustment transaction for the difference.

## Import a statement or connect a bank

- **Import** (in the account header) reads a CSV, OFX, QIF, PDF or similar statement file — see
  [Importing](importing.md).
- On a credit card, the due-date banner offers **Import statement** so the exact statement
  figures replace the estimate.
- To sync automatically, set up a bank connection under **Connectors** — see
  [Bank & brokerage connectors](../connectors/bank-brokerage.md). Most accounts work fine
  entered by hand or from imported statements.
- To log a purchase from your phone the moment you pay, **All accounts** → **⋯** → **Phone
  shortcuts** — see [Log a spend from your phone](phone-shortcuts.md).

## Archive, restore or delete an account

**Archive** an account you've closed but want to keep the history of:

1. Open the account → **⋯** → **Archive account** → **Archive**.
2. It disappears from the sidebar; its transactions are kept.
3. To see it again, go to **All accounts** → **⋯** → **Archived accounts**, pick it, and click
   **Restore account**.

**Delete** an account that should never have been there:

1. Open the account → **⋯** → **Delete account** → **Delete account**.
2. If the account has no transactions — or only its Starting Balance — it's deleted straight
   away.
3. If it still has other transactions, a second window says how many and asks you to
   **Force delete and remove N transaction(s)**. That permanently removes the account **and**
   those transactions. If any are transfers, the other account loses its side of them too — the
   window names those accounts. This can't be undone. The transactions are erased the way
   [Delete permanently](transactions.md#delete-or-erase-a-transaction) erases one, including
   their history's contents.

Some accounts can't be deleted until you deal with what's tied to them. You'll see a message
naming the problem:

- the account is the loan on an asset (*"…is linked to the loan on …"*) — remove the loan from
  the asset's **Ownership** section first;
- it still holds open positions, lots or option contracts — close or move them first.

A [commitment](commitments.md) that points at the account doesn't block the delete: the commitment
and its funding sources simply stop pointing at it.

## What Lucius can do here

- Add, update, archive or delete accounts (it asks before deleting).
- Reconcile a balance, or mark a batch of transactions cleared.
- Answer "what's my net worth?" account by account, or show how it changed over 1M, 3M, YTD,
  1Y or all time.

## Troubleshooting

**An account's balance is wrong after importing older history** — the older transactions are
dated before the Starting Balance, so they're left out. Open the Starting Balance and move its
date back (see [Change the starting balance](#change-the-starting-balance-or-start-date)).

**"Enter a valid current balance."** — the balance box must be a plain number (use `0` if
unsure).

**I picked the wrong type or currency** — these are fixed once the account exists. Delete it and
add it again; if it already has transactions, re-import them into the new account.

**"Is the real balance …?" shows a different figure from the sidebar** — Reconcile asks about
the cleared balance only. The sidebar includes pending (uncleared) transactions too.

## Related pages

- [Transactions and the ledger](transactions.md)
- [Importing statements and receipts](importing.md)
- [Categories and payees](categories-payees.md)
- [Assets — things you own](assets.md)
- [Accounts & opening balances](../concepts/accounts-and-opening-balances.md)
- [Net worth](../concepts/net-worth.md)
- [Base currency & FX](../concepts/base-currency-and-fx.md)
- [Bank & brokerage connectors](../connectors/bank-brokerage.md)

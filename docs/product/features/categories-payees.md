---
title: Categories and payees
description: Create and tidy the categories you spend on and the payees you pay, and teach the app to name and categorize new transactions for you.
category: features
order: 50
audience: [user]
status: stable
related: [concepts/categories, features/transactions, features/budgeting]
keywords: [categories, payees, rules, renaming rules, aliases, merge, default category, hide, uncategorized]
---

# Categories and payees

**Categories** are what money is for — Groceries, Rent, Salary. **Payees** are who you paid or
who paid you. Set them up well and imported transactions sort themselves: a payee can rename
messy bank text ("GRAB* A-9BEIP3LGWFBJAV" becomes "Grab") and fill in a category automatically.

A new book starts with a set of everyday spending categories. Change them freely.

## Manage categories

Open **Manage categories** from any of these places:

- **Accounts** — the header, next to **Manage payees** (on a narrow screen or phone, in the
  **⋯** menu).
- **Plan › Budget** — the **⋯** beside the method button (on a phone, the **⋯** at the top of
  Plan).

### Add a category

1. In **Manage categories**, click **+ New category**.
2. Type the name and press **Enter** (**Esc** cancels).
3. Set its **Kind** — **Expense**, **Inflow** (income), **Transfer** or **Investment**.

Quicker, while editing a transaction: type a new name in its category field and choose
**+ New category "…"**, then pick the **Type** and save.

### Rename, recolor, hide or link a category

On the category's row:

- **Rename** — double-click the name (double-tap on a phone), or use the row's menu.
- **Color** — click the color dot.
- **Hide** — the switch in the **Hidden** column (on a phone, **Hide** in the row's menu). The
  category and its history are kept, but it's removed from pickers. Switch it back to unhide.
- **Add to envelope…** — a spending category shows **Not budgeted** until it's linked to an
  envelope. Link it here to budget for it. See [Budgeting](budgeting.md).

Use the search box and the kind chips (on a narrow screen, the filter icon) to narrow the list.

### Delete a category

1. Row menu → **Delete…**.
2. If it has transactions, pick a category under **Reassign activity to**. Its transactions
   move there, and if both are budgeted, this month's assigned money moves too.
3. Or click **Delete without reassigning** — its transactions become **Uncategorized**.
4. Click **Delete category**.

If you just want it out of the way, **Hide** it instead — deleting rewrites history.

## Manage payees

Open **Manage payees** from the **Accounts** header (or **⋯** menu), or from the bottom of any
transaction's payee picker. Pick a payee on the left to edit it. Changes save automatically.

### Fill in a category automatically

1. Under **Default category by kind**, choose a category for **Expense** (and, if this payee
   ever pays you, **Income**, **Transfer** or **Investment**). An expense default never lands on
   income, and so on.
2. Keep **Apply default categories to new transactions** ticked.
3. To update past transactions too, click **Apply defaults to existing transactions**. This
   overwrites their current category, so it asks first.

New transactions from this payee are categorized from then on.

If you never set a default for a kind, the app picks one up from the first transaction of that
kind you categorize for this payee. To stop that, set the kind to **Uncategorized**. The app
leaves it alone until you choose a category for it again.

### Clean up messy bank descriptions

**Auto-matched descriptions** lists bank text the app has already learned belongs to this payee
(from past imports). Click **×** on any that were matched by mistake.

To catch new variations, add a **renaming rule**:

1. Under **Renaming rules**, choose **contains**, **is** or **regex**.
2. Type the pattern — e.g. **contains** `grab` — and add it.
3. To fix past transactions, click **Apply rules to existing transactions**. Matching
   transactions are moved to this payee, so it asks first.

**Apply rules to history** (at the bottom of the payee list) re-runs every payee's rules across
all transactions. A payee you set by hand may be overwritten, so it asks first.

### Merge, hide or delete a payee

- **Merge** — in **Danger zone**, under **Merge into another payee**, pick the payee to keep
  (e.g. merge "AMZN Mktp" into "Amazon"). Its descriptions, rules and transactions move over and
  the merged payee is deleted. This can't be undone.
- **Hide from payee suggestions** — keeps the payee but stops offering it in the picker.
- **Delete payee** — choose where its transactions go (**Reassign transactions to…**) before
  deleting.

## What Lucius can do here

- Create, rename, recolor, hide, merge or delete categories and payees.
- Set a payee's default category, add or remove renaming rules, and apply them to past
  transactions.
- Suggest categories for uncategorized transactions based on how you've categorized similar
  ones before.

## Troubleshooting

**A transaction says "Uncategorized"** — it has no category. That's allowed; give it one when
you're ready. Uncategorized spending shows under **Outside envelopes** in the budget.

**"Unknown category" when Lucius or an import uses a name** — categories are matched by exact
name, never guessed. Create the category first, or use the existing name. If a name matches more
than one category, the message lists them all.

**A payee's default didn't change old transactions** — defaults apply to new transactions. Use
**Apply defaults to existing transactions** for the old ones.

**I can't find "Split"** — it's used only inside split transactions and can't be picked or
hidden.

## Related pages

- [Transactions and the ledger](transactions.md)
- [Budgeting](budgeting.md)
- [Importing statements and receipts](importing.md)
- [Categories](../concepts/categories.md)

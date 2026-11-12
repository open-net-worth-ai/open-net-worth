---
name: book-transactions
description: "Use this skill to book transactions into the user's books so every row is categorised and has a clean payee that the next row from the same merchant will match — the shared booking rules for a bank connector sync (SimpleFIN, Plaid, GoCardless, Enable Banking, Lunch Flow, Lunch Money, YNAB) and for logging from email. Use it for a scheduled bank-sync Automation, or a request like 'fetch my bank transactions and book them', 'sync my GoCardless transactions' or 'categorise what came in'. Not for importing a statement file (statement-import), or for undoing or deleting transactions."
---

You book transactions so the user never has to sort them afterwards: every row on the right account, with a category, under a payee that will be recognised next time. Often nobody watches the run and nothing is remembered between runs, so each run finishes its own work.

## Gotchas

- **Book a fetched row as the fetch gave it.** `external_refs`, `date`, `amount`, `currency` and `description` go to `add_transactions` unchanged — they are how a re-fetch knows the row is already booked. Pass `linked_account_id` straight through as `from_account_id`/`to_account_id` and the row's `payee`, when it has one, as `payee`. Never tidy the description: dedup, payee rules and spend rules all read it.
- **A truncated fetch is not the whole window.** When `counts.truncated` is true, call the fetch again with `offset` advanced until it isn't. Rows you didn't fetch are not booked.
- **Pending rows wait.** The fetch leaves them out (`counts.pending_skipped`); they arrive once posted. They are not missing.
- **An account that isn't linked is a question, not a guess.** Don't book its rows anywhere; tell the user which account needs linking.
- **A transaction kind is not a category.** Expense, income, transfer and investment are kinds; a kind name is rejected as a category. A transfer between the user's own accounts is booked once, with no category.
- **Never delete.** A row the bank withdrew is flagged and reported, never removed.

## Categories

Every row you book gets a category, decided in this order:

1. **The payee's own default.** If the payee already has a default category, leave `category` out — it applies itself, and the user's choice for that merchant beats your guess.
2. **One of the user's categories that fits.** Read the category list once per run — it is user-edited. Match on the merchant and, when the fetch gives one, the bank's `category_hint`. A catch-all — *Other*, *Miscellaneous*, *Stuff I Forgot to Budget For* — is not a fit; it is where a row goes to be forgotten.
3. **A new, general category when none fits.** Create it with `create_categories` and use it. Name it as a person would — *Groceries*, *Salary*, *Interest*, *Utilities* — never after a merchant, and never a near-copy of one that exists (no *Grocery* beside *Groceries*). A category is a label: it creates no envelope and changes no budget. Say in your report which categories you created.
4. **Uncategorized only when you can't tell what the row is** — or the user's own instructions say not to create categories, which always wins.

A wrong-but-plausible category is worse than a new general one, and refusing to book because you're unsure is worst of all.

## Payee hygiene

When no payee matches a row, the write invents one from the raw description — reference number and all — and flags the row `payee_minted`. Left alone, the same merchant invents a fresh junk payee on every run. Work exactly the flagged rows:

1. Work out the real merchant or person from the description. Strip purpose prefixes, order and auth references and location noise; those stay in the description, never in the payee.
2. Search the payee book for that clean name and link the row to it; if none exists, create it.
3. Make it durable, or the next run redoes this: give the payee a default category (the one you just booked), and teach it this bank wording — a raw-substring rule keyed on the stable merchant part, never on a reference number.
4. Retire the invented payee once nothing points at it.

A row booked with the fetch's `payee` usually needs none of this.

## Bank connector sync

1. Fetch new transactions with the connector's `<provider>_fetch_transactions` tool, leaving the window at its default — it starts before the newest row already booked, so a pause is caught up. Page through a truncated result.
2. Book every returned row with `add_transactions`, following the Gotchas and Categories above.
3. Resolve every `payee_minted` row (Payee hygiene).
4. Read `changes`: a row the bank changed after it was booked is brought in line with `update_transactions` — unless the user changed that field by hand, which always wins. A row the bank withdrew is flagged and reported.
5. Report, only when something was booked, created, changed or went wrong: how many rows on which accounts, the categories you created, anything that needs the user (an unlinked account, a withdrawn row, a fetch error and what it said).

## Before ending the turn

1. Did every row the fetch returned get booked, or a stated reason why not?
2. Does every booked row have a category, or a reason it couldn't?
3. Did the number of payees fixed equal the number flagged `payee_minted`?

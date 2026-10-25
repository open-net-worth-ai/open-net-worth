---
title: Importing statements and receipts
description: Import a bank, card or brokerage statement step by step, review every row before it books, and attach receipts to transactions.
category: features
order: 40
audience: [user]
status: stable
related: [concepts/the-ledger, features/accounts, features/transactions, features/categories-payees]
keywords: [import, statement, csv, pdf, xlsx, ofx, qif, mt940, camt, duplicate, receipt, reconcile, bank statement, transfer]
---

# Importing statements and receipts

Import the statement your bank, card issuer or broker gives you and Open Net Worth turns its rows
into transactions. Before anything is booked you see every row: which ones are new, which ones
you already have, and which look like duplicates. Nothing is written until you click **Confirm**.

**Supported files:** PDF (including scanned, image-only PDFs), CSV, XLSX, OFX / QFX / QBO, QIF,
MT940 (`.mt940`, `.sta`) and CAMT.053 (`.camt`, `.xml`). Up to 25 MB per file.

**Before you start:** PDF, CSV and XLSX statements are read by Lucius, so you need an AI provider
set up ([Connecting an AI provider](../getting-started/connecting-an-ai-provider.md)). A scanned
PDF also needs a model that can read images. OFX, QIF, MT940 and CAMT files are read without AI.

## Import a statement

1. Open **Accounts** and pick the account the statement belongs to.
2. Click **Import** in the account's header (on a narrow screen it's under the **⋯** menu). On a
   credit card you can also use **Import statement** on the due-date banner.
3. Drop the file onto **Drop a statement here, or browse**, or click it to choose the file. For a
   file hosted online (a published Google Sheet, say), click **or paste a URL / Google Sheet
   link**, paste the link and click **Load**.
4. Check **Into accounts**. The account you started from is filled in. If one statement covers
   several accounts (a bank statement listing your savings and current account, say), click
   **+ Add account** and add them all — each row goes to the right one.
5. Optional: under **Tell Lucius**, write anything that helps him read this bank's statements —
   *"Dates are DD/MM"*, *"Rows mentioning MOMO.0352 are transfers to my wallet, not expenses."*
   Tick **Remember for this source** to apply the same notes every time you import from it.
6. Click **Read the statement**. Lucius reads the file, finds the transaction table, checks it
   against the statement's own balances, applies your notes and compares every row with your
   ledger. A long PDF can take a minute — see [Don't wait for a long read](#dont-wait-for-a-long-read).
7. Review the rows (next section), then click **Confirm**. The rows are booked as one import batch.

The done screen sums it up — for example *"3 linked to entries you had • 12 imported new • 1 left
out"* — and says whether the statement and your ledger now agree for that period.

## Don't wait for a long read

While a statement is being read, click **Continue in background** and carry on anywhere in the
app. When the read finishes, a popup says **Ready to review** — tap **Review** to jump back in —
or **Couldn't read**, in which case re-run it from the account's **Statements** list.

**Ready to review** means the whole review is done and saved with the statement — the rows, the
matches against your ledger and the check of what your ledger has that the statement doesn't.
Review opens it straight away, even after a restart, exactly as it was built. **Confirm books
exactly what the review showed and what you decided** — nothing is re-checked or second-guessed at
that point. Only two things are never booked twice: a statement row that is already in your ledger
by its own reference (so pressing Confirm twice, or importing the same file again, is safe), and a
row you deleted before.
Choosing a different account, changing the instructions or **Re-read** builds a new review.
**Back** from a reopened statement's review shows that statement's own file, accounts and
instructions: **Back to review** returns to it exactly as you left it, **Read again** re-reads the
file with what's on screen and opens the new review when it's done.

## Review the rows

Each row gets one of three decisions. Lucius picks a default; you can change any of them.

| Decision | What it does |
|---|---|
| **Link** | The row is a transaction you already have (entered by hand, from email, or by Lucius). Nothing new is booked — your existing transaction is linked to the statement and marked cleared. The expanded row shows exactly what changes on it. |
| **Import** | Book the row as a new transaction. |
| **Leave out** | Don't book this row. |

- The filter chips at the top — **Needs you**, **Will link**, **Import**, **Left out**, **Not in
  ledger**, **Not on statement** — narrow the list. Start with **Needs you**: those are rows Lucius
  wasn't sure about, such as a possible match with a different amount or date.
- Click a row to expand it: you see the statement's version beside your ledger's, and can change
  the payee and category before it books.
- Under **Will link**, **Link all**, **Import all** and **Leave all out** apply one decision to
  every row in that list.
- **Not on statement** lists transactions in your ledger for that period that the statement
  doesn't have — often a duplicate you entered by hand. You can mark them for deletion here; they
  are only deleted when you click **Confirm**.

### Book a row as a transfer

When a statement row is really money moving between two of your own accounts — a top-up of your
e-wallet, a payment to your credit card — book it as a transfer so it doesn't count as spending:

- **In the import screen:** say so under **Tell Lucius** before you click **Read the statement**
  (*"Payments to GRAB WALLET are transfers to my GrabPay account"*). The row then shows **Books as
  transfer: Checking → GrabPay**. If Lucius got one wrong, click **Not a transfer — book as an
  expense instead** (or *… as income instead*) on that row.
- **In a chat with Lucius:** when you import through Lucius, tell him which row is a transfer and
  to which account. He books it as a transfer in the same import.

The amount on the statement stays on the statement account's side; if the other account is in a
different currency, its side is converted. Two rows of the same statement that are the two halves
of one currency conversion are recognised and folded into a single transfer automatically.

## Statements history

Every imported statement is kept in the account's **⋯** menu → **Statements**. Each one shows one
status — **Reading…** (with the stage it's on; **Open** shows its progress), **Ready to review**
(**Review**), **Imported** with the date it was booked, or **Couldn't read** / **Reading stopped**
(**Re-run**) — and the same facts on every row: the statement period, how many rows it holds
(and, once booked, how many it added and linked to entries you already had), when you uploaded it
and what the AI read cost. A booked statement also says whether its balance checks out against
the closing balance it prints (**Balance matches** or **Balance off by …**); a file that prints no
balances shows nothing there. Card statements show the amount due. Each booked row's **⋯** menu
lets you download the original file or **Delete** the record — deleting the record keeps its
transactions.

**Importing the same file twice is safe.** Rows already booked are left out and the review tells
you when you imported it before. If there's nothing new, the button reads **Close**. A *new* file
whose rows are all already in your ledger offers **File statement — nothing new**, which adds it to
the Statements history without booking anything.

**Next month is faster.** Open Net Worth remembers each source it has read — its layout, the
accounts you chose and any notes you saved — so the next statement from the same bank needs no new
setup. If a bank changes its layout and the read looks wrong, tick **Re-read from scratch** before
**Read the statement**.

## Import by asking Lucius

You can also attach a statement in the chat (the paperclip) and ask: *"Import this into my DBS
account."* A scanned, image-only PDF works here too, the same as on the Import screen. Lucius walks
you through the same new / link / leave-out decisions in the conversation.
He can also list past imports, undo (roll back) a whole import and restore it again, and check
whether a statement's printed balance still matches your ledger.

## Attach a receipt

1. Click a transaction in the register to open its detail drawer.
2. In the **Receipts** section, click **+ Attach receipt** and choose an image or PDF.
3. Click a receipt to view it.

A transaction can have several receipts, and one receipt (an invoice covering several purchases,
say) can be attached to several transactions. **Remove** (then **Confirm**) only detaches the
receipt from that transaction — it never deletes the file.

## Troubleshooting

**"Couldn't read this statement — the AI reader is unavailable."** No AI provider is set up. Add
one in **Settings → AI**, then re-run the import.

**"This statement is a scan … needs a model that can read images"** — your model can't read
images. In **Settings → AI**, choose a vision-capable default model, or set one for statement
import under **Task models**, then re-run.

**"This scan has N pages — beyond the 40-page vision limit."** Split the PDF, or download a text
PDF from your bank instead of a scan.

**"Dates in this file are ambiguous"** — the file's dates could be day/month or month/day. Say
which under **Tell Lucius** (*"Dates are DD/MM"*) and read it again.

**"file too large"** — the file is over 25 MB. Split it, or export a shorter date range.

**A row was booked as a duplicate of something you really have twice** — undo the import (ask
Lucius to roll it back), then import again and set that row to **Import**.

**The balance check fails** — open the statement under **Statements** to see which transactions
differ. A row missing from your ledger, or one in your ledger that isn't on the statement, is the
usual cause.

## Related pages

- [Accounts](accounts.md)
- [Transactions and the ledger](transactions.md)
- [Categories and payees](categories-payees.md)
- [Bank & brokerage connections](../connectors/bank-brokerage.md)
- [The ledger](../concepts/the-ledger.md)
- [An import created duplicates](../troubleshooting/import-created-duplicates.md)

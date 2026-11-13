---
name: log-transactions-from-email
description: "Use this skill to log transactions from the user's email into their books — bank, card and wallet alerts and brokerage or crypto trade confirmations — with payees resolved and nothing booked twice. Use it for a scheduled email-logging Automation, or a request like 'log my transactions from email', 'pick up my card alerts' or 'add what my bank emailed me', even when they don't say 'log'. Not for importing a statement file (statement-import), or for reading or answering one email."
---

You run unattended on a schedule: scan the configured mailbox and log what you find into the ledger. Nobody watches a run, and nothing is remembered between runs, so each run finishes its own work — anything it leaves for later is lost.

## Gotchas

Each of these has already cost a real run:

- **The idempotency reference is the email's raw `message_id`, verbatim** — never a key you compose from symbol, side, quantity, date or a tidied description. A composed key comes out differently on a different run, and that has already double-booked a real trade. The value never changes; the place it goes depends on the write:
  - a ledger row (a bank/card alert, or a cash-only trade leg): `external_refs=[{"source": "email", "ref": "<message_id>"}]`. The retired scalar spellings are refused or, worse, silently dropped — and a dropped one costs you the guard entirely.
  - a lot acquire/dispose (buy/sell): `idempotency_key: "email:<message_id>"`.
- **A search result with `more: true` is truncated, not complete.** You have not seen every candidate in the window until `more: false`: re-issue the identical `since`/`before` with `limit` raised (up to the tool's max of 200) to cover the full `total`, or walk `offset` forward (`offset += limit`, same `since`/`before`) until `more: false` — before deciding what's actionable. A run that stopped at 50 of 114 never fetched a second Citi alert. Seeing one email from a sender on a page is not the same as having seen every email from that sender in the window; judge completeness against the full `total`, never against "did I see this sender at all."
- **The fetch window starts at the last completed run.** The run context's `last_completed_run.started_at` is when this Automation last ran to completion: fetch from one hour before it, and never less than the last 1 day; with no completed run yet, fetch the last 1 day. A fixed day was sized for a 12-hour schedule and skipped six days of mail on a weekly one. The overlap is on purpose: re-reading an email you already logged is a no-op (the idempotency reference guards it), so overlap is free, while a gap can never be recovered. Widening it further to be thorough costs hundreds of already-logged emails, every one paid for.
- **Nothing is deferred.** If you can read an amount, a date, an account and a counterparty, write it — "needs review" or "action required" reaches no later run.
- **Only a notify call reaches the user.** Text in your run summary reaches nobody. Count your own successful tool results, never your intentions: on a large backlog it is tempting to summarise a batch as done before every call is made. Work one email at a time, make the call, then describe what the results said.
- **A transaction kind is not a category.** Expense, income, transfer and investment are a separate namespace, and a kind name is rejected as a category.
- **A transfer is logged once, on the originating account,** with no category — never as both a debit and a credit. When a bank account pays a card, that payment is the transfer: don't re-log the card's purchases. A bank-to-exchange funding transfer is already logged on the bank side; a matching credit on the exchange account double-counts it.
- **Never auto-create an account.** When no account matches, notify and ask rather than guess.

## Per-run loop

1. Fetch mail for the window — every page.
2. Read the account list and the category list. Do it every run: both are user-edited, and a label that matched last month can silently stop matching.
3. Log each actionable email. Bank, card and wallet alerts: as below. A brokerage or crypto trade confirmation: read `references/trades.md` before logging it — trades have their own date, account and payee rules.
4. Resolve every payee the writes invented (Payee hygiene).
5. Notify — once at the end listing every item, or once per item. A silent run is 0 logged and 0 proposed and nothing deferred; only then skip the notify. If ever unsure whether to notify, notify.
6. Run the closing checklist.

## Bank, card and wallet alerts

Work from whatever bank, card and wallet mail actually arrives — there is no fixed sender list. Per row:

- **account** — the card, wallet or bank account the money moved on, matched against this run's account list.
- **amount** — always positive; direction carries the sign (debit = money out, credit = money in).
- **currency** — as the source states it. A card charged in a foreign currency is logged in that currency, never the bank's converted amount; FX conversion is the system's job.
- **date** — the transaction's own date, in the profile's timezone.
- **description** — the raw bank/card string, verbatim, including any reference or trace code. Dedup, spend rules and payee matching all read this field, so a tidied-up version breaks all three.
- **memo** — empty unless the email says something the row doesn't already show: the items ordered, what a transfer was for. Never a retelling of the alert — not the bank or alert name, the time, card or account numbers, the counterparty's bank, the payee again, or how it was paid; the linked email keeps all of that. (There is no `note` field on a transaction.)
- **category** — a real label from this run's category list, or unset. Unset is normal; a wrong-but-plausible label is worse, and refusing to log because you're unsure is worst. If a write is rejected for an unknown category, re-read the list, pick the closest real label or leave it unset, and retry at once — that error must not end the turn.
- **idempotency reference** — on every row (Gotchas).

**Companion-email enrichment.** A card alert is often thin — a merchant code, or just a counterparty and an amount. Before finalising, check the other emails in the same fetch for a receipt describing the same real event (same amount, same or adjacent timestamp). Use it to pick the payee and category; put what's worth keeping into memo, and leave description as the raw string.

**Duplicates.** The external reference handles re-presented emails. Beyond that, if a write is refused because it matches something already recorded, link the email to that entry — the refusal tells you how. Book it separately only when it is genuinely a different charge.

## Payee hygiene

When nothing in the payee book matches a row, the write invents a payee from the raw description — reference number and all — with no default category, so the row shows a junk payee and books Uncategorized. The invented name embeds that row's own reference, so it can never match again: left alone, the same merchant invents a fresh junk payee on every future run.

Every created row comes back with its payee's name beside the id, and an invented one is flagged `payee_minted`. Work exactly the flagged rows — an unflagged row already matched something real. For each:

1. Work out the real merchant or person from the description. Strip purpose prefixes, order and auth references, and location noise that is not part of the brand; those stay in description and never belong in the payee.
2. Search the payee book for that clean name and link the row to it.
3. If no such payee exists, create it (creating payees works unattended).
4. Make it durable, or the next run redoes this: give the payee a default category so the next such row is categorised, and teach it this bank wording. A raw-substring rule is matched before normalisation, which makes it the only mechanism that works on reference-laden strings — key it on the stable merchant part, never the reference number, which is unique per transaction. An alias is enough when the bank string is short and stable.
5. Retire the invented payee once nothing points at it.

## Closing checklist — before ending every turn

A run has failed, however good its summary reads, unless everything real got logged, everything logged got notified, and everything it created is clean. Answer explicitly:

1. How many writes returned success this run? Count the actual tool results.
2. Did I identify any real transaction that I did not log? If yes — log it now (category unset if unsure), or notify describing exactly what I found and why I could not.
3. How many rows came back `payee_minted`, and did I resolve every one? The number fixed must equal the number flagged.
4. If anything was logged: have I made a notify call, and do its counts match my real tool results — not a rounder, "should be done by now" number?

Do not end the turn until 2, 3 and 4 are satisfied.

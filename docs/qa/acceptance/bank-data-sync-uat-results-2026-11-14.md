# Bank data sync — UAT results, 2026-11-14

Run on screen against the Plaid sandbox (First Platypus Bank `user_transactions_dynamic`, Tartan
Bank `user_good`) and Enable Banking's Mock ASPSP, on a test copy (ports 8096/8097, live rates
and schedules, `bank_data_sync` on). Plan: [bank-data-sync-uat.md](bank-data-sync-uat.md).
16 scenarios run, 4 not run (sandbox can't produce them).

**Status:** #1–#5 fixed in 11226ba8, 99c7ea10, a45377a9, a09081cd, 649a6efd (not yet re-run on
screen); #6 onward still open.

## What broke, worst first

| # | Severity | What the user did | What happened |
|---|---|---|---|
| 1 | Critical | Bank sign-in expired → linked the bank again (the page's own advice) → Unlink old, Link new | Next sync booked the **whole history a second time**: "73 booked", 73 duplicate pairs (a new Plaid Item has new transaction ids). Checking gap $97.50 → $658.02, card $2,644.63 → $3,463.14. |
| 2 | High | Sandbox expired the bank's sign-in → Sync now | "6 dropped by the bank": every pending booking **deleted** though nothing was read. Status "ok", card green "Synced just now". The error only sits in the run's warnings, and it's attached to the other bank's account too. |
| 3 | High | Linked the bank again, before relinking accounts | Sync "Nothing new", the bank's accounts "done · 0 rows", green — the bank silently never syncs again. |
| 4 | High | Run report → Categorize with Lucius → send | Prompt was exact. Lucius wrote `<invoke name="get_transactions">` once, then `</invoke>` ~8,400 times; 300 s timeout, red "raise ONW_CLAUDE_CLI_TIMEOUT". A loop shape the de48f22d fix misses. Nothing categorised. |
| 5 | High | First import of a Plaid account | Rows dated the account's start day count on top of the opening the bank figure already holds: checking 500 → 597.50; card owed 500 → +2,144.63. |
| 6 | Medium | Bank sign-in expired | Nothing tells you where to fix it: Connectors "Connected", Attention 0; the Plaid page doesn't show it; Reconnect is only under ⋮ → Edit configuration. |
| 7 | Medium | Card payment synced | "Payment Thank You-Mobile" +$4,569.16 booked as income on the card, not a transfer from checking. |
| 8 | Medium | Approved a row in Review with a category | The payee doesn't learn it; its next row books Uncategorized again. The no-AI sync never learns from Review. |
| 9 | Medium | Plaid sync | USD amounts booked unrounded: 97.66696, 595.10675, 145.08855 … |
| 10 | Low | Balance gap → Use the bank's balance | Works, but here the gap was duplicates; moving the Starting Balance hides them. |
| 11 | Low | Visiting the sync page | An intro guide popped over it on each fresh visit (three different ones). |
| 12 | Low | Link picker | Book accounts of another bank are offered; one mis-pick linked Plaid checking to an EB account (Unlink fixed it). |
| 13 | Low | Lucius panel open | Accounts column wraps names and pills; History outcome text cut off. |

## What worked

A1 setup + import (per-account progress, result lines, Turn on), A4 add an account later (only
the new one read), B1 nothing new, B2 new rows (booked uncleared, in Review), B3 pending settles
in place (no copy), the run report (by day, currency, original beneath), E1 one link per
connector, E3 Unlink/Link keep the dialog open, F2 a second bank as its own card, G1 card shown
as owed, G2 foreign charge (`−S$46.52`, `€32.78 · £31.20` beneath), D2 "Use the bank's balance",
H1 turn off (no next run, history kept), H2 a Lucius Automation beside the sync (no refusal).

Not run: A2 (empty account), A3 (skip import), B4 (pending dropped), B5 (bank edits a posted
row): the sandboxes can't produce them on demand.

## Overlap with the API reviews

#2 and #3 are the reviews' top finding for both GoCardless and Plaid (an unread bank is treated
as read). #1 is the Plaid review's "re-link replaces the other Item" in its worst form. #5
matches the first-import balance findings. Still unverified on screen: GoCardless account ids
after renewal, Plaid's history-not-ready truncation, and card-in-credit sign.

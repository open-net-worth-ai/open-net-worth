---
title: The YNAB migration is a one-shot server job beside the ongoing importer
status: Accepted
date: 2026-10-19
deciders: repo owner
---

## Context

The YNAB connector (`services/aggregation/ynab.py`) fetches transactions inside a Lucius turn and
deliberately maps no categories, transfers or budget. The owner tried several times to move a whole
YNAB plan through Lucius and it failed: a turn is the wrong place for thousands of writes, it
cannot be re-run deterministically, and nothing checks the result against YNAB.

## Decision

A second, separate path moves a whole plan once: `services/ynab_migration/`, driven from the YNAB
connector card (token, a plan the user must pick, preview, import, report).

- **Same identities as the importer.** Accounts carry `external_ids` `{source: "ynab", ref:
  <account id>}`; transactions carry `external_refs` `{source: "ynab", ref: <transaction id>}`. A
  later connector sync attaches to the migrated accounts and dedups against the migrated rows, and a
  second migration run books nothing twice.
- **Writes go through the choke points in-process** (`accounts.create_account`,
  `planning.apply_budget_change`, `ledger.bulk_create_transactions`), never over HTTP and never to a
  store. The token stays in `YnabConfigStore` / `connector_secrets`; reads reuse the importer's
  guarded `_safe_get`.
- **One server-owned job.** A single-worker pool runs preview / import / verify off the request; the
  UI polls `GET /api/ynab/migration`. At most one job exists (a second start is a 409), state is in
  memory, and a restart loses only progress because every write is idempotent. This is not
  `ai/turns.py` (no model is involved) and not the statement parse pool (its state lives on a
  record); it follows the latter's small-fixed-pool shape.
- **Mapping is a pure module** (`mapping.py`), so it is unit-tested without a database.
- **Verification is part of the product**: account balances and counts, and for the current month
  every envelope's assigned/available and Ready to Assign, against YNAB's own figures. A mismatch
  is reported, not hidden.
- **Import needs a confirm** when the book holds accounts that did not come from a YNAB migration.
- **It takes a backup first and fails without one.** The import runs outside a Lucius turn, so
  there is no turn checkpoint; the backup (named in the job and the UI) is the undo.
- **It only touches what the book provably does not hold.** The base currency is adopted from the
  plan only when the book has no money data (accounts, transactions, owned assets, equity grants,
  income); otherwise a mismatch is refused. On a book with no accounts it removes only the shipped
  starter categories and envelopes, still as seeded and never funded, targeted, spent against or
  linked to a commitment, plus the starter groups that leave empty; the preview lists them.
- **Four YNAB requests per run.** The plan is read as ONE full-plan export (plus the plan list,
  and verification's accounts and current month). Reading per account and per month cost ~140 of
  YNAB's 200 requests an hour on a long-lived plan, so a second run inside the hour hit HTTP 429.
- **Bounded memory.** The export is streamed and abandoned past a 96 MiB cap, and payloads are
  rendered a chunk at a time.

## Mapping decisions worth knowing

- A YNAB category becomes a category and its own envelope (YNAB's per-category available has no
  other ONW equivalent); a category group becomes an envelope group.
- Off-budget accounts land on the nearest off-budget ONW type, since on-budget is cash/credit only.
- ONW keeps one cleared flag per transaction, YNAB one per transfer side: verification adjusts the
  cleared balance by exactly that difference. Rows are booked with their flag; no per-row pass.
- **Internal is the category's own flag.** The full-plan export marks every category GROUP internal;
  trusting it skipped all of a real plan's categories and booked their spending uncategorized.
- **Labels are booked as given** (`bulk_create_transactions(labels_as_given=True)`): no payee
  default, no Automation. YNAB leaves Ready to Assign inflows uncategorized on purpose; filling them
  from a payee's learned default moved six figures from Ready to Assign into envelopes.
- **A transfer's category counts only on its single budget leg**, as in YNAB: a tracking leg keeps
  stale categories from before it was tracking, and a budget-to-budget transfer's is ignored. A
  split's transfer line keeps its category; a row in a tracking account carries none.
- **Duplicates are decided by YNAB id only** (`force=True` with the external-ref floor): the fuzzy
  matcher refused genuine same-day, same-amount twins of a transfer leg.
- **A Starting Balance split into categories funds them**: the categorized parts are booked as
  inflows on the same day and only the rest is the opening balance.
- **Budget**: each month's Assigned is written as-is, negatives included (`allow_negative`; YNAB's
  own figures); a card payment category's Assigned goes to that card's payment envelope; at the end
  `sync_card_payment_envelopes()` retires closed cards' envelopes, as boot would; a fresh import
  sets zero-based as the home method.
- **A balance YNAB does not explain with transactions** (loan interest it calculates) is booked once
  as labelled adjustments, the cleared part cleared and the rest uncleared.

## Consequences

- Budget parity is as good as the two engines' rules agree. On the owner's 7-year plan (43
  accounts, ~9.7k transactions, 86 months) a fresh import verifies with zero mismatches: every
  account's balances and count, every envelope's assigned/available, card payment, Uncategorized
  and Ready to Assign. When something does differ, verification names the first month each
  envelope drifts and the YNAB ids only one side counts.
- A first import that dies halfway leaves the starter envelopes; re-running finishes the work but
  does not prune (only a fresh book is pruned, so a user's own envelopes are never deleted).
- The migration reads no environment variables; tests point `aggregation.ynab.API_BASE` at a stub.

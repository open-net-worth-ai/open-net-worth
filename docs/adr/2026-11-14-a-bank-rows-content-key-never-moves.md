---
title: A bank row's content key never moves, and an old key still counts as booked
status: Accepted
date: 2026-11-14
deciders: Claude (side session for the Enable Banking duplicate fix) + repo owner
---

# A bank row's content key never moves, and an old key still counts as booked

## Context

A bank row with no provider id is keyed by a hash of its content
(`providers.derived_ref`: account, date, amount, currency, text). The fetch decides "already
booked" by that key alone (`connector_fetch` step 7, the first ref), and `enable_banking` is an
authoritative namespace, so `matching.py` treats two keys as two real events. A key that moves
between releases therefore books every such row a second time, by a Lucius Automation and by
the no-AI bank sync alike.

0.1.50 moved Enable Banking's key twice for rows without an `entry_reference`:

- the hashed text was the display description, which 0.1.50 improved (whitespace collapsed,
  blank remittance parts skipped, new fallbacks to the transaction-code wording and the
  reference number);
- a row with only a `transaction_id` was keyed on it in 0.1.49 and on the content hash in
  0.1.50, because `transaction_id` is a fetch handle Enable Banking says may change between
  listings.

GoCardless, SimpleFIN, Lunch Money and Plaid hash their display description the same way. Their
rules have not changed since 0.1.49, but the next description fix would move their keys too.

## Decision

We will key an id-less row on a **frozen ref text** that nothing displays: each connector's
`_ref_text`, a copy of the rules as they stood when its rows were last keyed (0.1.50 for Enable
Banking; 0.1.49 and since for the other four). `_txn_description` is free to change; `_ref_text`
is not. A test pins each connector's key from literal text.

A provider row may carry **`ref_aliases`**: the refs an earlier release keyed it on (Enable
Banking: its 0.1.49 ref, when it differs). Step 7 checks them after the row's own ref, so a
row booked under one comes back `already_booked`, with `booked_txn_id` set under
`include_booked`, through `fetch` and `fetch_all` (the bank sync). Aliases are numbered for
identical rows the way that release numbered them, count as seen for the vanished check, and
never leave `connector_fetch`.

An alias that is a content hash is the row itself. An alias that is a provider handle (the old
`transaction_id`) counts only when the booked transaction has the same date and amount: a
handle may be given to a different row later, and a stale one must never hide a new
transaction. When the check fails, the row is offered, as it was before this fix.

`ref_aliases` is not `supersedes_ref`, which the bank sync reads as pending → posted.

## Consequences

### Positive
- Upgrading from 0.1.49 no longer books Enable Banking's id-less rows again; rows booked since
  0.1.50 keep their keys.
- Improving any of the five connectors' descriptions re-keys nothing.

### Negative
- The ref text and the description are two copies of similar rules that will drift apart by
  design. A reader has to know which one keys.
- Aliases stay forever: the fetch never writes the new ref onto the old transaction (a fetch
  is read-only), so the 0.1.49 alias is always checked.
- A handle alias whose booked row the user re-dated or re-amounted is not recognised; that row
  is booked again (the behaviour before this fix).

### Follow-ups
- YNAB also hashes its description (`payee_name`), but nearly every YNAB row has an id; it was
  left as is.

## Alternatives considered
- **Revert the description to the 0.1.49 rules** — rejected: rows booked under 0.1.50 would
  then be re-keyed, and the description fix (2026-11-13) is wanted.
- **Reuse `supersedes_ref`** — rejected: the bank sync books a row that supersedes a booked one
  as a pending → posted link or a review item, not as already booked.
- **Rewrite the old refs in the ledger with a migration** — rejected: the old key depends on
  the bank's raw row, which only a fetch has; a migration cannot compute it.
- **Accept a handle alias with no check** — rejected: Enable Banking documents the handle as
  unstable, so a match could silently drop a new transaction.

## Agent provenance

- **Decided by:** Claude, a side session started by the owner's main session.
- **Lane / branch:** trunk (`main`), worktree `lucid-elgamal-4314a7`.
- **Alternatives weighed:** revert the description, reuse `supersedes_ref`, a ledger migration,
  an unchecked handle alias.
- **Driving artifacts:** PR #123 (0.1.50, the description fallbacks), commit 8d253bfa (0.1.50,
  `transaction_id` dropped), PR #127; `tests/test_bank_ref_compat.py`.
- **Why this way (one line):** the key must be the same in every release, and the only place
  an old key can still be computed is the fetch that reads the bank's raw row.

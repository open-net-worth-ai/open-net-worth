---
title: A statement row routed by Lucius or the review can cross currencies
status: Accepted
date: 2026-11-07
deciders: Claude (implementing agent) + owner
---

## Context

A statement row can become a transfer several ways, and they disagreed about currency. An explicit
decision (`edits.to_account_id`, importing through a chat with Lucius) converted the other leg
through the ledger choke. A Lucius-pass `route_transfer` op, which is also what the review's Type
picker writes, refused any other currency in `route_transfer_draft`, on the reasoning that one
statement row cannot invent the other leg's converted amount.

The refusal was silent. "Payments to HARBOR are transfers to my USD account" left the row a plain
expense, with nothing on the row and only a log line to say why, so it counted as spending. The user
docs also promised conversion for both paths.

A statement prints only its own side. Whatever we book on the other account is an estimate, and
every reader of a routed draft's legs has to treat it as one.

## Decision

We will let `route_transfer_draft` route across currencies, in the same draft shape the explicit path
uses: both legs stated in the statement's currency, with the choke converting the other account's leg
into its own currency at write time.

Four rules come with it:

1. **The review shows the figure first.** `routed.other_leg` comes from `resolve_transaction_row`, the
   same resolution the write runs, and the row says it is converted at the latest rate.
2. **No known rate means no route, and the row says so.** The ledger's conversion raises on an unknown
   pair, which would fail the whole review for one row, so `lacks_route_rate` stops the route before
   reaching it. The row stays plain and
   carries a `transfer_no_rate` flag, and the review's verdict panel warns instead of reading "Looks
   good". It is deliberately not `needs_eyes`: those rows auto-decide on load and show no badge, so a
   real-app run with that flag set showed nothing at all.
3. **The other leg never decides a match.** `_routed_booked_transfers` judges a cross-currency routed
   row on the statement's leg alone, because a transfer booked earlier holds the bank's real rate and
   would never match our estimate.
4. **A Link never writes the other leg.** `statement_leg_only` drops that figure before
   `apply_import_merge`, whose loop otherwise re-amounts the first leg on a matching account. On an
   inbound row, that is the other account's real figure.

`unconverted_leg` is the one test for "this leg is still in the statement's currency". An FX pair has
native amounts on both legs and is never affected.

Automation routing stays same-currency, with its own guard in `_apply_routing`. It runs unattended,
with no review step to show the estimate to.

## Consequences

**Positive.** A transfer the owner asked for books as one, on every review path, and the two paths
that convert now share one shape. The last silent refusal on those paths is now visible on the row.

**Negative.**
- The other account's balance can be off by the difference between the latest rate and the bank's,
  until the owner corrects that leg. The review says so, but nothing chases it.
- Matching on one leg is looser: a different transfer between the same two accounts, with the same
  statement figure, within 2 days, is flagged as already booked. It is only a flag, and the owner can
  still book the row.

**Follow-ups.**
- Automation routing could convert once it has a way to disclose the estimate.
- A one-cent row can round to the same number on both legs and trip the equal-amount guard at the
  choke. The explicit path has the same edge.

## Alternatives considered

- **Keep same-currency and only explain the refusal.** Smaller, and never books an estimate, but it
  leaves the owner routing these rows through chat, which books the same estimate anyway.
- **Store the converted figure on the draft.** The preview's amount and currency read a routed
  draft's legs, so the row would show the converted figure in place of the statement's. It would also
  fork from the explicit path's shape.
- **Ask for the other amount in the review.** The most accurate, but it adds a required field to a
  bulk flow for a figure the statement doesn't print and the owner often doesn't have to hand. It
  could still be added later.
- **Keep comparing both legs, with a wider tolerance.** Any tolerance wide enough for real bank
  spreads is a guess, and rates drift between a transfer's date and the import.

## Agent provenance

- **Decided by:** Claude (implementing agent) on the owner's choice to add support rather than only
  flag refusals. Branch `worktree-import-cross-currency-transfers`.
- **Alternatives weighed:** see above. The owner chose between "add support" and "only flag refusals";
  rules 3 and 4 came from reading the readers of a routed draft, and each has a test confirmed to fail
  with its guard removed (`tests/test_import_route_transfer_fx.py`).
- **Why this way:** the owner's own words about a transfer should be honoured, and an estimate that is
  disclosed and correctable is better than a transfer silently booked as spending.

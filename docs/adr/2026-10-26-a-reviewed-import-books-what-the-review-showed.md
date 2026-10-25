---
title: A reviewed import books what the review showed — no second matching pass, no fuzzy guard
status: Accepted
date: 2026-10-26
deciders: orchestrating Claude agent + repo owner
amends: 2026-10-25-ready-means-the-review-is-persisted
---

# A reviewed import books what the review showed

## Context

Confirm on the import review called `decision_commit`, which re-derived the whole match pass
from the cached parse (`rederive_match_pass`) and then booked new rows through the ledger's
near-duplicate guard. On the owner's book this took 109 s for 58 rows, and the guard refused three
real Vietcombank → MoMo top-ups (₫500,000 each, a few days from another of the same amount and
route) as "already in your ledger" — rows the review had shown as **Import**, which the owner had
reviewed and confirmed. Reproduced on synthetic transfers: the guard treats a recurring top-up as
a copy of one booked days later. The owner: "after i spent minutes reviewing and making decisions,
it should follow my decision … it's not necessary to have another duplicate check there."

## Decision

- The review modal's Confirm sends `reviewed: true`; `decision_commit(reviewed=True)` books from
  the STORED review's per-row verdicts (`statement_previews`, a private `_commit_pass` key: every
  row's `RowResult` right after the matcher and the Lucius ops — exactly where
  `rederive_match_pass` ends — and the account it was built for). No second matching pass. A merge
  is still accepted only if that stored review proposed it (server-side, never the client's say).
- A reviewed commit books new rows with `allow_duplicate=True` — no fuzzy guard. Two identity
  floors remain: a row whose own statement ref is already booked (`dedup_by_external_ref`: a
  double Confirm, a re-commit) and a row the owner deleted before (`rejected_identities`, on every
  path).
- An unattended commit (Lucius's import tool, the one-shot REST import, `reviewed` absent) keeps
  re-deriving and the guard: nobody looked at what it books.
- No stored review for the account (a statement read before stored reviews) → re-derive, as before.
- Supersedes the 2026-10-25 ADR's commit hardening ("refuse an explicit-create row that became an
  unflagged duplicate since the review") — that was the same second check the owner rejected.
- A statement whose every import batch was rolled back is reviewable again: `already_imported`
  and the Statements list's `filed` count only batches that still stand.

## Consequences

- Booking follows the screen exactly, and stops paying for a full matching pass.
- A transaction that lands in the ledger between review and Confirm by another path (an email
  automation, Lucius) is not caught at booking; the review is where duplicates are shown, and the
  owner can undo the import from the Booked screen.

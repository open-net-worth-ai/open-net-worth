---
title: Ready means the review is persisted — a finished preview is stored and reopened as-is
status: Accepted
date: 2026-10-25
deciders: orchestrating Claude agent + repo owner
---

# Ready means the review is persisted

## Context

`statements_meta.status = ready` meant only that the PARSE was cached. Clicking **Review** on a
background-read statement then called `decision_preview` again: the parse was reused, but routing,
the matcher, anomaly detection, enrichment and the pre-commit ledger check re-ran over the whole
ledger in pure Python (measured on a synthetic 4,000-transaction book: 54 s first, 177-187 s per
reopen, zero LLM calls), holding the GIL and starving every other request. The preview the
background read had just built was thrown away. Two effects in the modal also fired the reopen
twice for one statement.

## Decision

- `ready` means every step is done. `decision_preview` persists its final response in
  `statement_previews` (one row per statement, `ON DELETE CASCADE`, checkpoint-excluded, not
  portable) before it returns; the record flips to `ready` only afterwards, for an async job and a
  sync request whose client left alike.
- A reopen returns the stored preview as-is. No ledger fingerprint, no staleness stamp, no
  recompute on open. Only an explicit ask builds a new one: `force_reparse` / `force_relearn`, a
  different account, changed instructions, a mapping. A statement read before this has no row and
  recomputes once.
- Safety against a ledger that moved on lives at COMMIT, which already re-derives the match pass
  against the live ledger. One hole is closed: an explicit-create row skips the near-duplicate
  guard (`allow_duplicate=True`), so a row the stored review did NOT flag but that is now an exact
  duplicate of a booked entry is refused and reported in `refused_duplicates`, never booked.
- The row is dropped on a successful commit and when Lucius ops are undone.
- The bounded retries that ran on every identical re-preview (category hints, metadata heal, match
  policy extraction) no longer run on an open; they run on a recompute. A degraded first attempt is
  therefore kept until the owner re-reads.

## Consequences

Review opens instantly and survives a restart. The first open of a statement read before this
change still recomputes. The client reopens a given statement at most once at a time.
Not addressed: the matcher itself is O(drafts x ledger) per recompute.

---
title: The YNAB two-way sync reconciles against per-entity baselines
status: Accepted
date: 2026-10-20
deciders: repo owner
---

## Context

The migration (ADR `2026-10-19-ynab-migration-is-a-one-shot-job-beside-the-importer`) moves a plan
across once. The owner keeps using both apps and wants them kept in step on a schedule:
transactions and each month's Assigned, both directions, with no dry-run mode and with conflicts
shown rather than guessed. YNAB's API has no per-transaction modified time, ignores a split's date
and amount on update, takes no future dates, and allows 200 requests an hour per token.

## Decision

`services/ynab_sync/` reconciles the two sides against what both last agreed on.

- **Scheduled reconcile with per-entity baselines.** For each ledger transaction (and each month +
  category Assigned) the sync stores the last agreed state. A run compares three states by plain
  equality (`units.decide`): unchanged / only the ledger changed (push) / only YNAB changed (pull) /
  both changed to the same thing (adopt) / both changed differently (conflict). Both sides are
  reduced to one canonical form (`canon.py`), YNAB's by the migration's own
  `mapping.plan_transactions` + `render_piece`, so a field is never compared across representations
  (cents vs milliunits, a transfer's one cleared flag vs YNAB's two). A transfer's description is
  YNAB's derived payee and is not compared.
- **YNAB side: delta + mirror.** One `GET /plans/{id}?last_knowledge_of_server=N` per run returns
  only what changed; it is folded into a stored mirror of YNAB's transactions, categories, accounts
  and payees. After a push the mirror is set to what YNAB answered, so the next read of our own
  write is an "adopt", not a change (echo suppression, YNAB -> ledger).
- **Ledger side: audit-cursor candidates.** Candidates are the transactions with `txn_audit` events
  after a stored `seq` cursor whose surface is not `ynab_sync`; every write the sync makes runs
  under that audit surface, so it never sees its own writes as edits (echo suppression, ledger ->
  YNAB). The candidate is then compared with its baseline, so a no-op edit sends nothing. `seq`
  only grows, so the cursor survives the audit table's cap.
- **First run.** A full export is the YNAB baseline. A ledger row's baseline is its state when the
  migration booked it, rebuilt by undoing its audited `update` events field by field (earliest
  `before` value wins). The per-row cut-off is its own `create` event — no timestamp is compared.
  The migration's one follow-up write (`mark_cleared`) looks like an edit but is harmless, because
  states are compared by value. Assigned baselines are seeded with YNAB's figures, but on the first
  run nothing is pushed: YNAB may have changed since the migration, so every month + category where
  the ledger differs goes to the review list; the three-way rule applies from the second run.
- **Conflicts go to a review list, not to a rule.** Same transaction (or month + category) changed
  on both sides and different: nothing is applied; both versions are shown with Keep Open Net
  Worth's / Keep YNAB's, applied to both sides. What the API cannot express (split date/amount/
  lines, future dates, unmapped categories, foreign currency) is filed with its reason, never
  dropped or approximated; a YNAB rejection is filed with YNAB's message. An item blocks its unit
  until resolved, or until the two sides come to agree.
- **YNAB-side trail.** Every transaction the sync creates or changes gets `flag_color: red`; every
  pushed Assigned appends `ONW YYYY-MM: assigned X (was Y)` to the category note (last ten of ours,
  the rest untouched, 500 characters). Nothing in the ledger changes to say "synced".
- **Rate cap.** At most 40 requests a run, counted before each is sent; creates and updates are
  batched (100 a request), a 400 halves the batch until the bad row stands alone. What does not fit
  stays owed (`pending`, and Assigned is re-derived from its baseline), never dropped. 429 is
  retried by the shared `source.call`.
- **Writes through the chokes.** Pulls use `bulk_create_transactions(labels_as_given=True)`,
  `update_transaction`, `bulk_delete_transactions` and `apply_budget_change(assign, allow_negative)`.
  Pushes use one guarded request path (`aggregation.ynab._safe_send`, which `_safe_get` now
  delegates to): fixed `API_BASE`, the stored Bearer token, a response cap, sanitized errors
  (status and YNAB's validation message only), ids checked before they reach a URL.
- **Failure safety.** Refs and baselines are recorded per confirmed batch, not at the end of the run.
  A ref-less ledger row identical to an unowned YNAB row claims it (a create whose answer was lost)
  instead of being sent again; `import_id` is not used because the spec makes such rows eligible
  for matching with user-entered YNAB transactions. Ledger changes nobody audited (a rewind, a
  batch rollback) are found every run by comparing each baselined row with its baseline. A pull
  re-reads the row before writing and files a conflict if it moved. A 429 is never slept on: it
  defers the rest to the next run, and every actual send is charged to the budget.
- **Safety nets.** A read that would delete more than a quarter of the synced ledger (and 25 rows)
  is refused, and deletions to send to YNAB above the same threshold go to the review list. Starting Balance rows, adjustments, and accounts YNAB does not know are never sent.
- **State** lives in four `ynab_sync_*` tables (state + meta, mirror, baselines, review list),
  book-scoped, checkpoint-excluded (a rolled-back turn must not rewind a cursor and replay writes),
  wiped by a data reset, holding no credential. The schedule is a once-a-minute tick
  (`two_way_budget_sync` job, not scheduled in a QA run) reading `next_run_at`, so changing the
  interval needs no re-registration. Enabling it makes the plain importer refuse that plan.

## Consequences

- A first run's Assigned differences are settled by hand in the review list, not pushed.
- A ledger row removed outright by a rewind (not soft-deleted) leaves an orphan baseline and is not
  re-created.
- Ledger edits older than the audit log's 20,000-event cap cannot be reconstructed on the first run;
  such a row is treated as untouched and YNAB's version wins.
- YNAB renaming a payee, or a split whose lines change shape, is seen only through the transactions
  YNAB reports as changed.
- A crash between creating a transaction in YNAB and linking its id in the ledger can duplicate it
  once; the window is one local write.

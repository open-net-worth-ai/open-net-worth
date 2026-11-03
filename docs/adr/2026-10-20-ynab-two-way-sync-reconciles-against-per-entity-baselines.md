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

## Amendment 2026-11-02: a direction

The owner asked for the sync to be one data sync with YNAB that runs either way or both, not a
two-way-only feature. The state gains `direction` (`both` — the default and the behaviour above —
`from_ynab`, `to_ynab`), set with the schedule.

- **One way only gates the action** `units.decide` already returns: `from_ynab` skips `push`,
  `to_ynab` skips `pull`, for transactions and for each month's Assigned. `conflict` is unchanged:
  a change on BOTH sides goes to the review list in every direction (owner decision — not "the
  source wins").
- **Resolving never writes the protected side.** Keeping the side a direction protects (this book's
  under `from_ynab`, YNAB's under `to_ynab`) records the other side's current state as the agreed
  one and writes nothing (`resolve._accept`), so a later change on both sides is asked about again.
- **Nothing held back is lost.** A held ledger edit to a baselined row is re-found every run by the
  existing baseline comparison; the rest is kept in the state — `held` (ledger ids never sent),
  `held_ynab` (YNAB ids never pulled; YNAB's delta reports a change only once) and `held_assigned`
  (YNAB's Assigned figures never pulled) — and fed back into each run, so switching back to `both`
  sends or pulls it on the next run.

## Amendment 2026-11-03: the import turns the sync on

The setup is two steps, not three: the plan is reviewed in one window (its accounts, every
category under its group, the newest 500 transactions as they would be booked — read from the
mapped plan, `ynab_migration.preview`, never a second reading of YNAB) and imported from there with
a direction. `POST /api/ynab/migration/start` takes an optional `sync_direction`; the import job
itself turns the sync on with it (`runner.update_settings`) once the import succeeds, so closing
the page mid-import loses nothing. A sync that cannot turn on never fails the import: the run's log
says why and the Data sync page offers **Turn on**. No direction (`Don't sync`) leaves it off, as
before.

## Amendment 2026-11-03: a YNAB account can be linked to an account already here

The owner asked whether someone who already has accounts can choose which YNAB account goes into
which of them. The review's Accounts tab now offers, per YNAB account, **New account** (the
default) or an existing account; `POST /api/ynab/migration/{preview,start}` take
`account_links` (YNAB account id -> account id). `ynab_migration.links` owns it.

- **A link is the account's YNAB id, nothing else.** The import appends `{source: "ynab", ref}`
  to the existing account's `external_ids` (`accounts.update_account`) and creates no account —
  exactly what an account the import created carries, so `book_state`, a re-run and the sync's
  `ctx.ynab_acct` pair with it unchanged. The account keeps its own opening; YNAB's Starting
  Balance is not booked there. Rows dated before that opening are booked as history, and the
  sync's existing `before_start` item explains them (`ynab_sync.starts`).
- **Which accounts fit.** Open, in the plan's currency, not linked to another YNAB account, and of
  a type the budget treats the same way: a budget account only an account of its own type (`cash`
  or `credit` — anything else would change Ready to Assign or card coverage), a tracking loan a
  `loan`, a tracking asset any off-budget asset (`fixed_deposit`, `investment`, `retirement`,
  `receivable`). A closed YNAB account is never linked. The same rules validate the request
  server-side (`links.resolve`); a link that breaks one fails the run, writing nothing.
- **Matching uses the ledger's engine, never a new one.** Each YNAB row on a linked account not
  yet in the ledger is rendered as the row the import would book and run through
  `match_preflight.check_proposed_rows` (the backend of `check_duplicates`: `match_batch` with its
  1:1 assignment, L0 included), in chunks of its 500-row cap, one account's rows together in date
  order. Only a `DUPLICATE_CLASSES` candidate that is a ledger row, not a Starting Balance and not
  already holding a YNAB ref counts, and each existing row is claimed once. A strong-band
  candidate on the same accounts is a **match**: that row takes the YNAB ids
  (`ledger.attach_external_refs`) and nothing is booked; the merge choke's adopt-if-blank rule
  (`match_review.adopt_if_blank`) then fills only what the row leaves blank — the person's
  payee, category and memo are never replaced. A review-band candidate, or a strong one on other
  accounts (a single-leg row here vs YNAB's transfer), is a **possible match**: listed in the
  review beside the row here, and booked as a new row unless the person ticked it (`same_as`:
  YNAB transaction id -> ledger id, honoured only for exactly that pair). Categories play no part
  in a match, so the preview — which has no categories yet — decides what the import does.
- **Why a possible match is asked, not queued.** Booking it and filing a match review was the
  obvious reuse, but by our reading of `txn_sync` and `units.build` a review accepted after the
  sync has baselined the booked row merges that YNAB-linked row INTO the existing one; the
  tombstone keeps the baseline, so the next run would read it as deleted here and send the delete
  to YNAB. Deciding in the review, before anything is written or baselined, avoids that; the
  merge interaction stays a known limit of the sync (not exercised by a test).
- **The review re-checks on each change.** Choosing an account starts a new preview with the
  links and `recheck_of` (the preview it refines); that re-check reuses its preview's YNAB read
  for ten minutes (`job._read_cache`, one entry) instead of reading YNAB again. Every other run
  reads it afresh.
- **Verification** keeps comparing the balance with YNAB's. A linked account that also holds rows
  YNAB does not has its count check read as "YNAB transactions" (rows carrying a YNAB ref vs
  YNAB's), with the account's own rows named in the note.
- **The sync.** Matched rows carry YNAB ids, so the first run pairs them like any migrated row
  (its `first_run_bases` reconstruction included: an edit the person made before the import reads
  as a local change). The account's own rows YNAB does not hold are unlinked creatable rows in a
  YNAB-known account, so a sync that sends (`both`, `to_ynab`) creates them in YNAB — the review
  says how many.

## Amendment 2026-11-03: categories and groups are paired by id and synced both ways

The sync used to re-resolve YNAB categories **by name** on every run, so a rename in YNAB broke the
pairing, a category new in YNAB held its transactions ("no counterpart in this book"), and
categories and groups were never synced. The import also matched by exact label ("🛒 Groceries"
never met "Groceries") and moved a matched envelope into the YNAB group. The owner's model: a YNAB
category both **labels a transaction and holds the budget**; here a **category** labels and an
**envelope** (holding 0..n categories) holds the budget, and envelope groups group envelopes.

- **Pairing.** A YNAB category pairs with one of OUR CATEGORIES, by id, for good; a YNAB category
  group with one of our envelope groups. The documents live in the sync's own `ynab_sync_baselines`
  table (`c:<ynab id>`, `g:<ynab id>`; no migration), written by the import and read and kept by the
  sync through `services/ynab_pairs.py`, a leaf both packages import. `state.reset(keep_plan=...)`
  carries a plan's pairing across `runner._adopt_plan`, so turning the sync on after the import
  finds it. Each pair holds a baseline PER SIDE (name, hidden, group): a matched pair legitimately
  differs ("🛒 Groceries" there, "Groceries" here), so "did it change?" is asked of each side
  against its own last-seen state, and a field changed on both goes to review when the results
  differ (`category_sync`, same `decide` shape as transactions, per field).
- **Matching** (`ynab_migration.category_match`, shared by the import review and the sync), names
  compared without emoji/symbols/case/extra spaces, first hit wins: (1) one of our categories not
  yet paired: pair with it; (2) an unpaired envelope of that name: empty, the category is created
  inside it; holding categories, only offered — not pre-selected at import, and a review item in
  the sync (it would make the envelope shared); (3) nothing: a new category with an envelope of
  its own in the group paired with its YNAB group. Two candidates that read the same are never guessed between by the sync
  (it files a `choose` item; the review settles them by spelling, below), nothing existing is renamed or
  moved, nothing is paired twice. The import's review alone then SUGGESTS a close name for a row
  left new (`category_links._suggest_close`, `category_match.closeness` >= `CLOSE`: same words up
  to plurals and "and", one name starting the other and most of it, or one side of the other's
  slash): the closest pair first, after every exact match, never one category for two rows,
  pre-selected and marked only by a warning beside it that says why. Of several that read the
  same, the review takes the one spelled most alike (`category_links._settle_ties`,
  `category_match.spelled_alike`: same text, then same up to capitals, then letters in common,
  emoji dropped); only an exact tie still asks. The sync
  never pairs on a close name — nobody is there to check it. The import's `category_links` (YNAB category id -> `cat:<id>` |
  `env:<id>` | `""`) mirrors `account_links`.
- **The budget follows the envelope.** A category's Assigned syncs only while its envelope holds
  that category alone (`pair.env`, re-derived each run). Leaving that state is reported once;
  while paused YNAB's figures are noted, and on resuming the baselines are cleared so any
  difference goes to review. A category in no envelope has Assigned 0 (our held-outside-envelopes
  rule matches YNAB's zero-assigned behaviour): it lives in a YNAB group "Not budgeted" the sync
  creates once, and money YNAB later assigns to it gives it an envelope here.
- **Deletes hide, and parking replaces hiding in YNAB.** YNAB's API can neither delete NOR hide a
  category (SaveCategory takes a name, a note, a group and goals; SaveCategoryGroup a name), and
  the sync never deletes ours: hidden or deleted in YNAB hides ours (the real "hide" is the
  category flag); hidden, deleted or merged here MOVES the YNAB category into a YNAB group named
  "Hidden in Open Net Worth", made once like "Not budgeted" and never paired with a book group.
  The pair is kept while it sits there (`pair.parked`, `pair.home` = the group it came from);
  showing it again moves it back to `home`, or to the group paired with its envelope's if that
  moved meanwhile. A category in either parking group, or in YNAB's own "Hidden Categories", is
  never a move the book follows; a category hidden in YNAB's app reports its
  `original_category_group_id`, which both the import and the sync use as its group. Money is
  never released or moved.
- **A decision is remembered.** "Leave it out" on a category either side would not take writes a
  `x:y:<ynab id>` / `x:o:<category id>` baseline document that both `pull_new` and `push_new`
  honour. Rows in a category that will not be in YNAB (hidden unpaired, or left out) go to YNAB
  uncategorized with one report line per category, and this book's category stands
  (`units._income_view`).
- **A refusal is never recorded as agreed.** A pushed change is recorded only when YNAB's answer
  shows it (`Base.confirmed`); a refused or ignored one is a review item in plain words. A refused
  group or category create no longer stops the run.
- **Transactions** map through the pairing in both directions, so a category inside a shared
  envelope round-trips without being relabelled. A transaction whose category is not in YNAB yet
  waits for a later run (`payloads.NotYet`) instead of being filed as unsupported. Income-kind
  categories are never sent; their rows go as Ready to Assign inflows and the ledger's own
  category stands (`units._income_view`).
- **Allowance and direction.** Category and group writes take at most 20 of a run's 40 requests;
  the rest is found again next run (state is re-derived, nothing is queued). One way skips the other
  way's writes exactly as for transactions, and keeping the protected side accepts instead of
  writing (`category_resolve`).
- **Not done:** a YNAB category hidden BY YNAB's own app moves to its "Hidden Categories" group;
  the sync reads `original_category_group_id` so that is not read as a move, but it has only been
  exercised against the stub.
- **Money rule: no Assigned moves without an agreed baseline or the person's choice.** Whenever a
  category's envelope changes (none to one, one to another) or it is paired with a YNAB category
  that already existed, `pair.resume` / the envelope change makes the category *fresh*: its
  baselines are dropped, YNAB's figures are read in full (this run's read when it was a full one,
  else one more request: `Base.ynab_figures`), and `assigned.evaluate(..., fresh=)` turns any
  push or pull for it into a review item. A category with money in YNAB and no envelope gets one
  automatically only if it never had one (`pair.had_env`); otherwise the review asks (`env:`
  item; "Give it one?" takes YNAB's figures, "Leave it without one" is remembered as `x:e:`).
  Only a category made just now from the other side's has nothing to lose and is not fresh.
- **Parking.** A move INTO a parking group is not a YNAB-side change; every push carrying a group
  records the confirmed group; showing a parked category returns it to `pair.home` only while its
  envelope has not moved since (`pair.home_for`), else to the group paired with the envelope's.
  The import never makes a book group from a parking group (`ynab_pairs.PARKING`). Pairs and
  remembered decisions of every plan survive a reset.

---
title: write_log row-level checkpoints replace whole-file blob snapshots for DB-backed stores; hybrid restore reverts both worlds in one call, DB half first
status: Accepted
date: 2026-08-07
deciders: Claude (implementer agent, storage plan 2 task 2 — mechanism + task 2 review fix round 1 — transaction-contract and single-PK-guard hardening); this ADR authored in task 8, ratifying spec 2026-08-05's D7 and closing the spec's Definition-of-Done requirement for "the checkpoint replacement" ADR
---

# Write_log row-level checkpoints replace whole-file blob snapshots for DB-backed stores

## Context

`ai/checkpoint.py` is the mechanism behind `/rewind`: every AI turn that mutates data captures
enough state to undo itself. Before this plan, the ONLY mechanism was file-shaped —
`JsonStore._save_raw()` calls `ctx.snapshot_before_write(path)`, which gzips and
content-addresses the WHOLE pre-write file into a shared blob pool; restore is temp-file +
`os.replace` per file, done in a per-session-guarded, best-effort sequence. That mechanism is
correct but scales with **file size**, not with how much actually changed — a one-row append to
an 86,000-row `evidence.json` still cost a fresh gzip of the entire file, every time.

Storage Plan 1 moved `evidence` off JSON onto a real table (`evidence` — checkpoint-EXCLUDED, so
this didn't yet require a DB-side checkpoint mechanism). Plan 2 moves `price_history` (excluded)
and, critically, `instruments` — which **is** checkpoint-INCLUDED (it is not in
`ai/checkpoint.py`'s `_EXCLUDED_PATHS`) — off JSON too. Once `instruments.json` no longer exists,
there is no file left to snapshot for it, but `/rewind` must keep working: an AI turn that edits
an instrument must remain undoable. Spec D7
(`docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`) names the
replacement and the reasons for it directly:

- **O(rows changed), not O(file size).** A one-row append stops costing a full-file gzip.
- **Identical on both dialects** — no reliance on SQLite's session extension (not built into
  CPython's stdlib `sqlite3`) or any whole-database snapshot feature, which would give SQLite and
  Postgres two structurally different restore mechanisms.
- **Converges conceptually with `txn_audit`**, which already records most of this shape for
  transactions specifically.
- **Restore becomes transactional** rather than a best-effort sequence of independent file
  replaces.

The Global Constraints for this plan make the ordering explicit: **"write_log lands before any
checkpoint-INCLUDED store cuts over"** — `instruments`' cutover (task 5) depends on this task (2).
The cutover also runs over **several releases**, so for a stretch some stores are JSON files and
some are DB rows, and a *single* AI turn can write both kinds — restore must revert both or
neither, never one without the other.

**The concrete scale this replaces**, measured twice against real production data cloned to
`/tmp` (never the live tree):

- Plan 1's dress rehearsal (evidence only, recorded in `docs/DATA_MODEL.md`): 86,145 evidence
  rows / ~111.4 MB source JSON migrated in 3.4s, landing a ~164.7 MB `onw.db`.
- Plan 2 task 8's dress rehearsal (this task): the **full** `0001`→`0005` chain absorbing all
  three files in one `ensure_migrated()` call — 86,849 evidence rows (~112 MB), 34,424
  `price_points` rows across 196 series (~3.2 MB), 58 instruments (~40 KB) — in **3.697s** wall
  time, landing a 161.26 MB `onw.db`. A single-row `/rewind` proof against a real absorbed
  instrument (id `e40e64f8-3567-4eab-81a3-55fa95eefa3f`, symbol `ONWAI`) wrote exactly **one**
  `write_log` row, not a re-snapshot of the 58-row table it belongs to.

## Decision

We will add a `write_log` table plus a `core.io` seam plus checkpoint-side capture/restore/GC
logic, so DB-store writes get the same undo guarantee JSON-file writes already had, at O(rows)
cost instead of O(file size).

**Table shape** (`backend/db/tables.py`, Alembic revision `0003`, `down_revision="0002"`):

```
write_log(checkpoint_id TEXT, seq INTEGER, table_name TEXT, pk TEXT,
          before_payload JSON NULL, created_at TEXT,
          PRIMARY KEY (checkpoint_id, seq), INDEX ix_write_log_checkpoint(checkpoint_id))
```

`before_payload = None` means the row did not exist before this turn (replay = DELETE); a
non-null value is the FULL row dict (`{seq, id, payload}` for a `PayloadTableStore` row — not
just the bare payload, so replay's `table.insert().values(**before_payload)` restores the row at
its **original** seq/position, not wherever `seq` happens to be next at restore time).

**The seam** (`backend/core/io.py`) mirrors the existing file seam exactly:
`fire_db_write_hook(table_name, rows, conn=None)` / `register_db_write_hook(fn)`, called
unconditionally by every DB store write — the checkpoint layer, not the seam, decides
captured-vs-excluded (mirrors the file half's split). `core/` stays a stdlib leaf: `conn` is an
opaque `object`, never typed as a DB-driver type here, passed through untouched to the registered
hook.

**`CheckpointContext.record_db_before(table_name, rows, conn=None)`** is the DB mirror of
`snapshot_before_write`/`_capture`:

- **First-touch-per-(table, pk) dedup**, via a per-context `self._db_seen: set[tuple[str,str]]`.
  Only the FIRST before-image of a row is ever kept for a given turn — a row touched three times
  in one turn still yields exactly one `write_log` entry, holding the state from BEFORE the
  turn's first write. This is what lets replay be "delete then re-insert one recorded row" with
  no extra logic to pick the right one among several.
- **Immediate, durable insert** — not deferred or batched. Crash-durability parity with file
  blobs (which are gzipped into the blob pool on first touch, not at `finalise()`): a before-image
  exists in `write_log` even if the turn crashes before it ever calls `finalise()`.
- **The transaction contract** (review-hardened — see the incident it closes, below): if `conn`
  is given, the insert runs THROUGH the caller's own open write transaction, so the image commits
  atomically with the write it precedes. If `conn` is `None`, `record_db_before` opens its own
  short transaction immediately — safe ONLY when the caller holds no write transaction of its
  own.

**The single-PK guard** (`_single_pk_col`): every replay/preview/pre-restore site resolves a
table's primary-key column through this helper, which RAISES `RuntimeError` — *"write_log
checkpoints support single-column primary keys only; table `<name>` has N — extend write_log
before checkpoint-including this table (composite pks would corrupt unrelated rows on
replay)"* — instead of silently taking `list(table.primary_key.columns)[0]`. `price_points` has a
COMPOSITE primary key (`price_source_id`, `date`); it sits in `_EXCLUDED_TABLES` for cache-parity
reasons (below), but the guard means it structurally **could not** be flipped to
checkpoint-included later without first extending `write_log`'s schema, independent of whether
anyone remembers the parity rationale.

**Excluded tables** (`ai/checkpoint.py::_EXCLUDED_TABLES`, the DB mirror of `_EXCLUDED_PATHS`):

| Table | Why excluded |
|---|---|
| `evidence`, `evidence_subjects` | Accumulated news/web evidence — a derived, rebuildable cache; a turn rollback must not delete/resurrect news (same rationale as the pre-existing `EVIDENCE_JSON` file exclusion, Plan-1 precedent). The subject index goes with its parent row. |
| `price_points` | Append-only price cache — parity with the JSON store it replaced, whose `_save` never fired the file write-hook either (not per-turn AI state). Structurally reinforced by the single-PK guard: its PK is composite, so it could not be safely included even if the parity rationale were revisited without first extending `write_log`. |
| `write_log` | The machinery itself — self-inclusion would recurse. |
| `json_cutovers` | The permanent migration-absorption ledger, not per-turn AI state — same rationale as its exemption from the tier-2 test-clearing sweep. |

**Hybrid restore — binding phase order**, `ai/checkpoint.py::restore(mode="data"|"both")`:

1. `_capture_pre_restore_snapshot(cp)` captures the CURRENT state of **both worlds** the target
   checkpoint touched — files via the existing `snapshot_before_write`, and DB rows via
   `_capture_db_pre_restore` (reads the current row for every `(table, pk)` the checkpoint's
   `write_log` names, feeds it through `record_db_before` so the restore itself becomes
   undoable — the same guarantee files already had).
2. `_replay_write_log(checkpoint_id)` — the DB half, **first**: ONE `unit_of_work()` transaction,
   rows in reverse `seq` order, `before_payload is None` → `DELETE ... WHERE pk`, else →
   delete-then-insert (a dialect-portable upsert). This call is **not** wrapped in a
   try/except at its call site in `restore()` — an exception propagates out before a single file
   is touched, so a DB replay failure leaves nothing partially reverted across the two worlds.
3. Files replace **last**, exactly as before (temp + `os.replace` per file).

A DB replay failure therefore aborts the WHOLE restore before any file write; a file-write
failure can still leave files partially reverted (unchanged pre-existing behavior for the
file-only case) but never happens after a DB failure, since DB always goes first.

**GC**: `_gc_write_log` deletes every `write_log` row for a checkpoint no longer in the surviving
manifest, wired into both `delete_checkpoint` and `_prune_old_checkpoints` (same best-effort
try/except posture as `_gc_blobs`) — `write_log` is bounded by the same `MAX_CHECKPOINTS = 50`
retention the blob pool already enforces, not an unbounded append log.

**Deferred, not decided here: the checkpoint index stays a file.** `CHECKPOINTS_INDEX` (the
manifest listing every checkpoint, its file-blob-hash map, and now its `db_tables` name list) is
UNCHANGED — still a JSON file, read/written by `_read_index`/`_write_index`/`_append_index` under
`_index_lock`. Per spec D7 and the module's own docstring, this is explicitly this plan's scope
boundary: `write_log` is the row-CONTENT half of the eventual full checkpoint cutover; the
manifest half (a real `checkpoints`/`checkpoint_files` schema) is Plan 4's, alongside the
remaining ~40-store rewrite.

## Consequences

### Positive

- **O(rows changed), not O(file size)**, proven at real scale twice — Plan 1's evidence-only
  rehearsal (86,145 rows/~111.4 MB in 3.4s) and this task's full-chain rehearsal (121,331 rows
  across three stores, ~115 MB of source JSON, in 3.697s). A single-instrument `/rewind` touch in
  this task's dress rehearsal wrote exactly one `write_log` row, not a re-snapshot of anything.
- **Restore is transactional for the DB half** — a partial revert across many rows touched in one
  turn is now structurally impossible (one `unit_of_work()`), which is strictly stronger than the
  file mechanism it sits beside: file restore was already atomic PER FILE, but never atomic
  ACROSS files (a multi-file restore is a sequence of independent `os.replace` calls).
- **Dialect-portable by construction** — no SQLite session extension, no whole-database snapshot
  feature; Postgres gets the identical restore guarantee for free, verified by `tests/db` running
  on both dialects in CI.
- **The first DB store proven end-to-end in the `/rewind` contract, on real data, not just
  fixtures** — this task's Phase 3: `begin_turn` → `InstrumentStore().touch()` on a live absorbed
  instrument → `end_turn` → `restore` → the touched field reverted to its exact original value.
- **Free for future stores**: any store built on `PayloadTableStore` gets checkpoint inclusion
  by default (`checkpointed=True`) with zero additional per-store wiring — unlike the old
  per-JsonStore hook call, which had to be present at every mutating method by hand.

### Negative

- **Composite-PK tables can never be checkpoint-included as-is.** `write_log.pk` is a single
  opaque `TEXT` column; `_single_pk_col` raises rather than silently corrupting, but that means a
  future store with a genuinely composite natural key needs a `write_log` schema change (e.g. a
  composite-safe key encoding, or a second key column) before it can set `checkpointed=True` —
  not just a subclass of `PayloadTableStore`.
- **No cross-checkpoint dedup.** The old blob pool content-addresses by SHA256, so an unchanged
  file costs nothing across many checkpoints. `write_log` has no equivalent: every turn's first
  touch of a row writes a fresh `write_log` row even if an earlier, still-live checkpoint already
  holds an identical before-image. Not a measured problem at today's scale (a single-row touch is
  instant), but a real structural difference from the mechanism it replaces.
- **`conn=None` is a footgun by design, not eliminated.** A future caller that opens its own write
  transaction and then fires the hook WITHOUT passing `conn` reproduces exactly the failure mode
  task 2's review found (see Alternatives): the independent transaction stalls on SQLite's
  single-writer slot until `busy_timeout`, and because the hook is best-effort (must never block
  or fail a real data write), that failure is swallowed — no checkpoint minted for a real change.
  The contract is enforced by docstring discipline (`core.io.fire_db_write_hook`,
  `register_db_write_hook`, `record_db_before` all carry the identical warning) and by the one
  production caller (`PayloadTableStore._fire_hook`, which always passes `conn`) — not by a type
  system or a runtime assertion, since an assertion that could itself raise would violate the
  "never blocks a write" guarantee it exists to protect.
- **The hybrid isn't finished end-to-end.** `find_checkpoint`, `_mark_orphaned_after`, and the
  index bookkeeping around a restore are still file-shaped even for a checkpoint that is 100% DB
  writes — this ADR covers the row-content half only (see Follow-ups).

### Follow-ups

- Plan 4 ports `CHECKPOINTS_INDEX` itself off JSON onto a real schema (per spec D7 and the
  module's own docstring, which states this scope boundary explicitly) — this ADR's `write_log`
  is the content half of that eventual full cutover, not the manifest half.
- If a future store needs composite-PK checkpoint inclusion, extend `write_log`'s key shape
  deliberately (e.g. a deterministic composite-key encoding) rather than working around
  `_single_pk_col` — its raise is the intended trip-wire for that design conversation to happen
  on purpose.
- Cross-checkpoint `write_log` dedup (mirroring the blob pool's content-address dedup) is a
  plausible future optimization if row-level checkpoint volume ever becomes a measured cost — not
  needed at today's scale.

## Alternatives considered

- **SQLite's session extension** — rejected per spec D7: not built into CPython's stdlib
  `sqlite3` module, and inherently dialect-specific, which would give SQLite and Postgres two
  structurally different restore mechanisms instead of the "identical on both dialects" property
  the whole storage program is built on.
- **Whole-table snapshot per write** (copy the entire table into a shadow table before each
  mutation) — rejected: this is O(table size) per write, reintroducing exactly the O(file size)
  problem `write_log` exists to solve, merely moved from "whole JSON file" to "whole SQL table."
- **Recording every touch, not just the first-per-turn** — rejected: without first-touch dedup, a
  row touched three times in one turn would write three `write_log` rows, and replay would need
  extra logic to select the OLDEST one rather than the current "exactly one row for this
  `(checkpoint_id, pk)`, by construction." Deduping at capture time is simpler and cheaper than
  resolving ambiguity at replay time.
- **Deferred/batched `write_log` inserts** (buffer in memory, flush at `finalise()`) — rejected:
  breaks crash-durability parity with file blobs, which land in the blob pool on first touch, not
  in a batch at the end. A turn that crashes before `finalise()` would lose every DB before-image
  while its file before-images survived — an asymmetric, confusing failure mode.
  `record_db_before` inserts immediately instead.
- **Making `conn` mandatory, no `conn=None` path** — rejected: `_capture_db_pre_restore`
  legitimately holds no open write transaction of its own (only a `.connect()` read) when it
  feeds rows through `record_db_before`; forcing it to fabricate a write transaction it doesn't
  otherwise need would be pure ceremony. The two-mode contract (pass yours if you have one,
  otherwise a short independent one opens) is simpler than requiring every caller to hold a
  transaction it doesn't need. Handing the read connection through would have been actively
  wrong: an uncommitted `engine.connect()` context rolls back on exit in SQLAlchemy 2.0, silently
  discarding every pre-restore DB image — checked explicitly before ruling this out.
- **Silently taking `list(table.primary_key.columns)[0]` for any table** — this WAS the task 2
  original implementation, then reverted after the task 2 review reproduced real corruption: two
  `book_members` rows sharing a primary-key fragment, replayed against a composite-PK table,
  would delete/reinsert the wrong row. Replaced with the loud `_single_pk_col` guard.
- **A conn-less hook call as the only path (the task 2 original implementation)** — reverted
  after the review empirically reproduced the failure it causes: a throwaway repro script opened
  a `unit_of_work()`, wrote through it, then opened a SECOND independent transaction the way the
  original `record_db_before` always did — `OperationalError: database is locked` after 5.20s
  (SQLite's `busy_timeout=5000`), which the best-effort `except Exception` swallowed silently. The
  transaction-contract fix (pass `conn` when you hold one) closes this without giving up the
  best-effort posture that keeps a hook failure from ever blocking a real write.

## Agent provenance

- **Decided by:** Claude (implementer agent), storage plan 2 — mechanism designed and built in
  task 2 (commit `7d637c71`, `feat(db): write_log row-level checkpoints + hybrid restore (plan 2,
  task 2 — spec D7)`); the transaction-contract and single-PK-guard hardening landed in task 2's
  review fix round 1 (commit `3200b3a2`, `fix(db): write_log seam carries caller's conn; loud
  single-PK guard; preview tests (plan 2, task 2, fix round 1)`); the `instruments` cutover proved
  the mechanism checkpoint-included in task 5 (commit `53550f12`). This ADR itself is authored in
  task 8 — belatedly, by design: the spec's own Definition of Done names four required ADRs ("the
  engine ruling, the `book_id` partition, the checkpoint replacement, and the fail-closed
  migration posture"); this is the third, written once the mechanism had shipped, been reviewed,
  and been proven against real production-scale data (dress rehearsal 2), rather than
  speculatively ahead of any of that. Lane/branch: `lane/storage-plan1` (worktree
  `storage-plan1`).
- **Alternatives weighed:** the dialect-portability question (write_log vs. the SQLite session
  extension) was settled by spec D7 before task 2 began; first-touch-dedup vs. record-every-touch,
  immediate-durable-insert vs. deferred/batched, and conn-optional vs. conn-mandatory were settled
  during task 2's implementation; the conn-passthrough transaction contract and the single-PK
  guard were REVERSED from task 2's first implementation by its own review, both against
  empirically reproduced failures (see Alternatives above), not just reasoned-about risk.
- **Driving artifacts:**
  `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md` (D7 — the
  replacement's full rationale, the hybrid-period framing, the O(rows)-vs-O(file) argument, and
  the Definition of Done's four-ADR requirement this document fulfils the third of);
  `docs/superpowers/plans/2026-08-07-storage-plan2-reference-data-and-write-log.md` (Task 2's
  Produces block — the binding table shape, seam signature, and restore phase order);
  `.superpowers/sdd/2026-08-07-storage-plan2-reference-data-and-write-log/task-2-report.md` (the
  TDD evidence and fix round 1's two empirically-reproduced findings: the conn-less stall and the
  composite-PK corruption); `task-8-report.md` (dress rehearsal 2 — the first real-data, real-scale
  proof of the DB half of `/rewind`, including the 3.697s full-chain migration and a live
  instrument touch-and-revert).
- **Why this way (one line):** replace a checkpoint mechanism that scales with FILE SIZE with one
  that scales with ROWS CHANGED, without regressing any of the three guarantees the file
  mechanism already had — atomic restore, the restore is itself undoable, and identical behavior
  on both dialects.

## Amendment (2026-08-07, Plan-2 final-review wave)

Three corrections to the mechanism described above — not a new decision, so recorded here rather
than as a fresh ADR (`docs/DATA_MODEL.md`'s `write_log` entry carries the current, detailed
truth; this note is the append-only trail for why it changed):

1. **`_replay_write_log` is now TWO-PHASE, not the single-phase reverse-seq delete-then-insert
   described above.** A turn whose write PERMUTED row order (e.g.
   `PayloadTableStore.replace_all([b, a])` on a table that had `[a, b]`, which reassigns both
   rows' `seq`) could not be restored: reinserting a row at its original `seq` collided with the
   sibling row still occupying it, since it hadn't been processed yet — `IntegrityError`, restore
   fail-closed but broken for that whole turn class. Phase 1 now deletes every touched pk first
   (including the `before_payload = None` ones); phase 2 re-inserts every before-image once the
   table holds nothing this checkpoint touched, making row order irrelevant.
2. **`record_db_before`'s `conn`-given insert now runs inside a SAVEPOINT
   (`conn.begin_nested()`).** This is the first correction this ADR's own "Consequences /
   Negative" section didn't anticipate: the first genuine Postgres run of `tests/db` (C1 of this
   same review wave — see the `tests/db` entry in DATA_MODEL.md) found that the existing
   best-effort `except Exception: log and continue` around a bare `conn.execute` was silently
   SQLite-only. SQLite tolerates a failed statement inside an open transaction; Postgres aborts
   the WHOLE transaction on any statement failure, so the caller's own next statement on that same
   connection (e.g. `PayloadTableStore.insert`'s real data write) died too, with an
   unrelated-looking `InFailedSqlTransaction` — silently breaking the "a failing hook must never
   block a real write" guarantee, Postgres-only. The SAVEPOINT confines the failure to just the
   image insert, identically on both dialects.
3. **Checkpoint GC now respects a pinned-ids set** (`ai.checkpoint._pinned_ids`) that
   `_prune_old_checkpoints`/`_gc_write_log`/`_gc_blobs` all honor. Two reproduced silent-failure
   shapes without it: restoring the OLDEST of `MAX_CHECKPOINTS` active checkpoints could
   self-destruct (capturing the pre_restore snapshot pushed the restore TARGET past the cap
   mid-restore, pruning it and GC'ing its write_log rows before the replay call ever read them —
   `restore()` returned success having reverted nothing), and a different session's `end_turn`
   hitting the cap could GC an in-flight turn's already-durable write_log rows before that turn's
   own `end_turn` ever added it to the index.

All three were reproduced against pre-fix code (RED) before the fix (GREEN) — see
`.superpowers/sdd/2026-08-07-storage-plan2-reference-data-and-write-log/final-fix-report.md`.

## Amendment (2026-08-07, Plan 3 task 1 — restore hardening)

Storage Plan 2's final review named the remaining gap directly (spec D7, "Restore failure
semantics as implemented"): the DB replay commits before the file-replace loop, so a crash mid-loop
left DB-reverted + files-partial — recoverable via the pre_restore checkpoint, but a real window
nonetheless, and one this ADR's own "Consequences / Negative" section had not separately called
out for the DB-capture half. Plan 3 (the money core) is the reason it could not stay best-effort
any longer; three corrections close it, again not new decisions, so recorded here:

1. **`_capture_db_pre_restore`'s write_log image write is now FAIL-CLOSED, not best-effort.**
   Its own SELECT reads (and the single-PK-column check) were already unwrapped and propagated on
   failure; the gap was the write_log INSERT itself, made through `record_db_before`, whose
   `except Exception: log and continue` is best-effort BY DEFAULT for good reason (a real in-turn
   write must never be blocked by a failing checkpoint hook). Applied to the PRE-RESTORE capture,
   that same default meant a pre-restore image that failed to record was silently swallowed — the
   safety snapshot would finalise "successfully" missing a row it claimed to protect, and
   `restore()` would still go on to mutate live data behind an undo that could not actually undo
   it. `record_db_before` gained a `fail_closed: bool = False` keyword (default unchanged — every
   in-turn caller, via the registered hook, keeps the best-effort swallow); `_capture_db_pre_restore`
   is the one caller that passes `fail_closed=True`. A failing image now raises OUT of
   `_capture_pre_restore_snapshot` and out of `restore()` itself, BEFORE `_replay_write_log` or any
   file write runs — nothing mutated, restore simply refuses.
2. **File writes now STAGE before the DB replay commits, and only RENAME after.** Previously the
   whole read-blob → decompress → write-file sequence ran AFTER `_replay_write_log` committed, so
   the split-world window (DB reverted, files not yet reverted) lasted as long as that sequence
   took. `_stage_restore_files` now writes each restored file's bytes, durably, to a sibling temp
   path (`<dest>.restore-tmp-<checkpoint_id[:8]>`) BEFORE the replay call; `_commit_staged_restore_files`
   — called only after the replay succeeds — does nothing but a `durable_replace` (or an unlink for
   a file that was new that turn) per file, shrinking the window to that fast rename loop. A replay
   failure after staging unlinks the temps (`_cleanup_staged_restore_files`) so an aborted restore
   leaves no trace; a crash INSIDE the rename loop leaves the untouched destination beside its
   fully-staged temp, and calling `restore()` again for the same checkpoint finishes the job
   (`_replay_write_log` is idempotent against an already-reverted DB; re-staging overwrites the
   stale temp with the identical bytes).
3. **`_pinned_ids` is now a `collections.Counter`, not a `set`.** The prior amendment's pinned-ids
   set already closed two self-destruct shapes, but a set's `discard` lets the FIRST unpin release
   an id a SECOND concurrent holder is still relying on — e.g. two overlapping `restore()` calls
   against the same checkpoint id, each pinning it for its own duration. The refcount means "still
   pinned" survives until every holder has unpinned; `_gc_blobs`'s defer path also now logs the
   blocking id(s) at WARNING, so a pin that never resolves (a leak) is discoverable instead of a
   silent, permanent GC stall indistinguishable from routine deferral.

All three were reproduced against pre-fix code (RED) before the fix (GREEN) — the fail-closed
capture via the same write_log primary-key collision technique the previous amendment's item 2
introduced (a real `IntegrityError`, not a mocked one), the staged-restore recovery path via an
injected failure in the commit-phase rename call. See
`.superpowers/sdd/2026-08-07-storage-plan3-money-core/task-1-report.md`.

## Amendment (2026-11-01 — an edited row is restored IN PLACE)

The two-phase replay (I2: delete every touched pk, then re-insert every before-image) deleted rows
the turn had only EDITED. A delete fires every foreign key pointing at that row from a row the turn
never touched: `transactions.from/to_account_id` → `accounts` is RESTRICT, so restoring a turn that
renamed an account with any transaction failed the whole restore (`FOREIGN KEY constraint failed`);
`transaction_splits`/`transaction_external_refs` → `transactions`, `signal_timeline_entries` →
`intel_stories` and `session_messages` → `sessions` are CASCADE, so a parent edit captured without
its children silently took them with it — the parent's payload still listed the external refs while
the child table that dedup reads no longer held them. Deferring FK checks was rejected: Postgres
FKs are not `DEFERRABLE`, and a deferred CASCADE still cascades.

`ai/checkpoint/_replay.apply_images` replaces the two phases with four steps in the same locked
transaction: **park** one non-FK column of each unique index (`seq`, `idx`, `normalized_name`, …)
on every touched row that exists now; **insert** rows the turn deleted, parents first; **update**
rows the turn edited in place, parents first; **delete** rows the turn created, children first. An
edited row is never deleted, so no untouched row's FK fires. Parking keeps I2's guarantee (a
permuted `seq` order restores without a unique collision); a parked integer is `-(2**30 + n)`
(inside Postgres `INTEGER`), a parked text `~restore-park:n`. The replay stays idempotent — on a
second run every row is either present (re-parked, updated to the same image) or already gone.
Regression: `tests/db/test_restore_updates_in_place.py`.

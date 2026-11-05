---
title: Actual Budget is a second app behind the YNAB engine
status: Accepted
date: 2026-11-05
deciders: orchestrating agent (Claude) + repo owner
---

# Actual Budget is a second app behind the YNAB engine

## Context

People who budget in Actual should be able to bring a budget here, and either leave or keep both in
step, as YNAB's users can. Actual has no HTTP API. Its client speaks a CRDT sync protocol, and the one
Python implementation is `actualpy`. Actual's database holds no computed figures, has no delta read
that doesn't repeat rows, no compare-and-set, no transaction flag, and only an on-budget or off-budget
mark on an account. Its server limits one address to 500 requests a minute.

The binding constraint was a shared engine. A second copy of the sync would mean every later fix made
twice, and nothing a YNAB user sees or gets could change.

## Decision

- **One engine.** `ynab_migration` and `ynab_sync` work for `budget_app.current()`, a `ContextVar`
  defaulting to YNAB. `BudgetApp` holds what differs: names, stores, reader, client opener, lock, pool,
  and a `Capabilities` table. The YNAB JSON shape is the engine's internal format; `actual_sync/shape.py`
  translates to it and `ActualClient` takes the same request bodies. The packages keep their names, and
  `from_ynab`, `to_ynab`, `keep: "ynab"`, `held_ynab` and a pair's `"ynab"` key mean "the budget app's
  side" for both apps. YNAB's test files pass unedited, and a snapshot holds its user-visible strings.
- **A fresh copy each run.** Each run downloads to an owner-only directory under
  `CACHE_DIR/actual_sync/`, reads it all, and deletes it in `finally`. A saved copy would be faster, but
  a 5,000-row read took 1.2 to 1.5s here, which doesn't justify keeping a decrypted budget on disk. The
  whole read is compared with the stored mirror, so `delta.apply_read`, echo suppression and baselines
  work unchanged. `actualpy`'s `sync()` change list is never used: the design's first test saw it repeat
  rows, ours included. The smoke's two forms (a fresh copy, and after a commit) returned none, so what it
  returns depends on the clock it asks from, which is reason enough not to depend on it.
- **A batch is sync, re-check, apply, commit.** A row that moved since this run read it is filed as a
  conflict and nothing is written. Any error from `commit()` ends the run, and the next one works it out
  from a fresh copy. A lost answer is claimed on the next run by `units._claim_unowned`.
- **Gate 0, older budgets.** Every open checks, before migrations run, which ones a snapshot still
  lacks. `add_messages_pending`, `multiple_dashboards`, `create_dashboard_table`,
  `reports_move_selected_categories` and `prefs` are known safe. `remove_cache`, or any JavaScript
  migration not on the list, refuses the budget with words asking for a Reset sync. The budget mode is
  read as the preference, else `metadata.json`, else envelope, because `prefs` isn't carried over by
  `actualpy` and a July 2024 tracking budget otherwise reads as envelope.
- **A chosen server is trusted with a budget, not with the book.** Everything in a download is the
  server's to choose, over plain http anyone on the way can too, so none of it may reach past the copy.
  Server migrations run on a connection that can't attach another database and can't run a `PRAGMA`
  (`actual_sync/sql_guard.py`, in place of `actualpy`'s own loop), and the attach limit also stops the
  `VACUUM` forms, which attach internally (read in SQLite's `attachFunc` and `sqlite3RunVacuum`). A file
  name of any shape but Actual's is refused before it is fetched. A download holding more than
  `db.sqlite` and `metadata.json` is refused before it is unpacked, because a journal file beside the
  database is replayed on open and can name a file for SQLite to delete. No migration in 6.8.2, 25.12.0,
  26.9.0 or 26.11.0 uses a `PRAGMA`; the one `VACUUM` is in `remove_cache`, already refused. A later
  genuine one that needs either is refused in plain words until it's reviewed, and then allowed as that
  exact case. What else runs on a copy can't leave it either: a trigger or view can't hold an `ATTACH`,
  and `actualpy`'s engine runs only its own statements, naming tables it reflected.
- **Income is real.** An Actual income category is an income-kind category here with no envelope, so
  salary raises Ready to Assign. Starting Balances is internal, and an opening stays an opening.
- **One app at a time.** Turning one app's sync on, or importing from it, is refused while the other's
  is on. Off behind the gateway, because `actualpy` opens its own connection and a check beforehand can't
  pin an address against DNS rebinding.
- **No compare with To Budget.** Actual has no card payment categories, and carryover and held money
  work differently, so Ready to Assign is not compared. Verify compares balances, Assigned and available.

### What the real server changed

`make actual-smoke` found three things the stubbed tests couldn't.

- **Reads ask from a little before the copy began, not from the clock.** `commit()` stamps our messages
  and moves `actualpy`'s clock past them, and `sync()` asks the server only for what is stamped later.
  An edit stamped before our commit and arriving after that batch's pull never reached the copy, and a
  later batch in the same run wrote over it with nothing filed. Measured with the official client as the
  person: the later batch did not raise `Moved`. `Checked.sync` now asks from `CLOCK_MARGIN` (10 minutes)
  before the first read and keeps the clock where `commit` put it. What comes back twice is replayed in
  timestamp order, so each cell ends where the server's does. This costs replays: 2,000 new rows in 20
  batches took 8 to 12s.
- **The request cap binds after all.** The design said a cap would never bind. The server refuses a
  501st request in a minute with HTTP 429, whose limit is why a real `make actual-smoke` needs a front
  that varies the address. A batch is two requests, so `request_cap` is now 200 batches and
  `category_request_cap` 100, using the engine's deferred path. 280 Assigned figures, at two batches
  each, ran as 160 sent and 120 waiting. A second run in the same minute got the 429, stopped in plain
  words, and a later run finished with nothing lost or doubled.
- **The official client has a blind spot.** On a fresh download, `getBudgetMonth` answers 0 for the
  first month of a budget's sheets (three months before its first row), whoever wrote the figure, and
  the months after it carry without it. Its own `zero_budgets` table holds the right figure, and so does
  this app's read. Pinned by `test_the_official_clients_blind_spot_is_its_own`. The web app was not
  checked, so what a person sees there is unknown. Verify reads only the current month, so this bites
  only a budget started this month or within the last three.

### The measured limits

- **The same field inside its own batch's gap.** Nothing can close this: the server has no
  compare-and-set. If a person's edit reaches the server between a batch's pull and its commit, on a field
  in that batch, the later timestamp wins, which is ours, and nothing is filed. The gap was a median of 3
  to 13ms and a worst of 57ms over 20 one-row batches on loopback, across runs. A real network adds the
  round trips. A hundred-row batch takes longer than a one-row batch, and that wasn't measured.
- **A device clock more than 10 minutes behind.** An edit stamped earlier than `CLOCK_MARGIN` before the
  newest message the copy read stays out of the copy until the next run, and if the same field is written
  later in the run, ours carries the later stamp. Measured at 90 seconds behind, with a window left open
  since before our commit: seen, and the later batch refused. Beyond 10 minutes is by reasoning only.
- **Another field on the same row.** The server merges by field, so both changes stand, and the next run
  brings the other field here. Measured.
- **A person's identical row is claimed.** `_claim_unowned` can't tell a lost answer from a person typing
  the very same row (date, amount, payee, memo, category, account) in Actual before our create goes. It
  pairs them: one row each side, and the person's row now belongs to the pairing. Accepted, since the
  alternatives are a duplicate on every lost answer or a marker Actual has no place for. Measured.
- **A row edited inside a lost answer's window** can duplicate once, as the YNAB ADR already accepts.
  Reasoned, not measured.

### Not verified

Windows until CI runs it, the Actual web app itself, OpenID-only servers, TLS to a real server, a
pre-September-2021 snapshot (that client wouldn't install), real people's budgets, a remote server's
latency, and any Actual version but the pinned 26.11.0 (older snapshots from 6.8.2, 25.12.0 and 26.9.0
open on it).

## Consequences

**Positive**

- A fix to the engine reaches both apps. Actual's differences are a table anyone can read.
- `make actual-smoke` is a real regression: pinned server and official client, made-up budgets, opt-in,
  never in `make check`.
- A decrypted budget is never left on disk by a run that ended normally, and leftovers are swept at boot.

**Negative**

- YNAB's words live on inside the engine (`from_ynab`, package names). A rename would churn every import
  and test, and can happen later as its own change.
- Every run downloads the whole budget, and a pull replays this run's messages.
- A very large run takes several runs to finish, and a rushed second run can get a 429.
- `actualpy` is a third-party client of a protocol its authors can change. It is pinned exactly, and a new
  JavaScript migration stops only the budgets that need it until it's reviewed.

**Follow-ups**

- Two bugs YNAB may share, left unchanged because the owner was promised no YNAB difference: a refilled
  cleared category, and rows stuck after a crash. Each is a capability switch away.
- YNAB's plan switch also resets the schedule and shares the plan-swap delete path, by reading.
- Address pinning, to open this behind the gateway.

## Alternatives considered

- **A Node helper running Actual's own client.** Most faithful, but it adds a second runtime to every
  install, including the wheel and the desktop app. Kept as the fallback had Gate 0 failed.
- **An abstract budget model both apps translate to.** Cleaner names, at the price of moving every engine
  path and the YNAB proof with it.
- **A second engine for Actual.** Simplest on day one, and every later fix twice.
- **A saved copy between runs.** 0.14s against 1.4s for 5,000 rows. Rejected for the decrypted file it
  leaves on disk and the recovery after a failed commit.
- **Replaying only from the last read instead of the start of the copy.** Cheaper on a huge push, but an
  edit from a slow client arriving later than one pull would be lost again.

## Agent provenance

- **Decided by:** orchestrating agent with worker agents, across tasks 0-8 of the plan; four owner
  decisions noted in the design (one app at a time, very old budgets refused, no transaction marker, off
  behind the gateway).
- **Lane/branch:** `feat/actual-budget-sync`.
- **Alternatives weighed:** above.
- **Driving artifacts:** the design and plan of 2026-11-05 under `docs/superpowers/` (kept local), the
  throwaway Gate 0 test, `tests/actual_live/`, and `make qa-e2e SPECS=ACTUAL-01`.
- **Why this way:** one engine is the only shape that keeps YNAB's proof intact and every later fix single.

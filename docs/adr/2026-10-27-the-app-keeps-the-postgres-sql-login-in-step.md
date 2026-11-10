---
title: The app creates and re-syncs Lucius's Postgres SQL login itself, and verifies it from that login before any query runs
status: Accepted
date: 2026-10-27
deciders: implementing Claude agent + repo owner
---

# The app creates and re-syncs Lucius's Postgres SQL login itself, and verifies it from that login before any query runs

## Context

`run_sql_query` must never read a credential table (`SECRET_TABLES`, plus `connectors`, plus a
public demo's wider set — `db.sql_console.denied_tables()`). On SQLite an authorizer enforces
that inside the app. Postgres has no authorizer, and a textual scan is not a boundary there
(`query_to_xml('select * from secret_config', …)`, `U&"…"` escapes). So since commit `9dec6e858`
(2026-10-04) SQL ran on Postgres only as a separate login named by `ONW_SQL_CONSOLE_DATABASE_URL`,
which the OPERATOR created and REVOKE-d by hand from a guide.

That made every change to the readable set an operator chore nobody is told about. The
2026-10-27 change making `sessions` readable (it held whole transcripts until ADR
`2026-08-24-a-transcript-is-a-table-of-messages`; its SQL block was never revisited) reached a
Postgres install only if someone read a release note and ran a GRANT. Every table added by an
upgrade had the same gap. Worse, Settings showed the SQL switch ON while every query failed with
"disabled on Postgres". SQL access is an important feature, not an optional extra; it must work
on a supported database without a guide.

Two facts make automation possible: the app's own login OWNS every table (its migrations create
them), so it may GRANT and REVOKE on them; and creating a login needs only `CREATEROLE`, which
the account an app connects with nearly always has (self-hosted superuser, the RDS master user,
Supabase/Neon defaults).

## Decision

We will make the app own the console login's permissions on Postgres
(`backend/db/sql_console_role.py`):

1. **Managed login by default.** The app creates `onw_sql_<database>` (`LOGIN NOINHERIT`, no
   other attributes) with a random password stored in `secret_config` (namespace `sql_console`,
   itself denied to SQL). A forgotten password (e.g. secrets stripped by a restore) is reset,
   not refused. Provisioning is serialized across processes by an advisory lock.
2. **Re-sync at every boot, after migrations.** SELECT on every table outside the denied set,
   `REVOKE ALL` on every denied table, USAGE on the schema — so an upgrade's new tables and a
   changed denied set take effect on the next restart with no operator step. An
   operator-supplied `ONW_SQL_CONSOLE_DATABASE_URL` login is synced the same way.
3. **Check every session, from the session.** At preparation AND on each query's own
   connection, one catalog query asks the database: did this session sign in as the role it
   runs as (`session_user = current_user`, `role` unset — a login that STARTS as a restricted
   role via `options=-c role=…` could switch back mid-statement); does that login hold no
   `SUPERUSER`/`REPLICATION`/`BYPASSRLS`/`CREATEROLE`/`CREATEDB`; is it a member of no other
   role; and is `has_any_column_privilege` false on every denied table. A failed check switches
   SQL access off with the reason — the database answers, never our bookkeeping. The console
   uses no connection pool, so nothing a query leaves on its session (a session advisory lock,
   a setting) outlives it.
4. **Never `SET ROLE` on the app's connection, never the app's own login.** The session user
   could switch back mid-statement (`set_config('role', …)` then a nested `query_to_xml`), so the
   boundary must be a separate LOGIN; a URL naming the app's own login is refused (revoking
   would lock the app out of its own secrets).
5. **Say why, in Settings.** `GET /api/config/sql-query` returns `available`, `reason` and, when
   only an operator can fix it (no `CREATEROLE`), the one command: `ALTER ROLE "<app>"
   CREATEROLE;` on Postgres 16+, and on older versions — where `CREATEROLE` can grant itself
   nearly anything — a `CREATE ROLE` for an admin to run, used via
   `ONW_SQL_CONSOLE_DATABASE_URL`. An unavailable status is re-checked at most every 30s, so a
   fix works without a restart. Any unexpected error while preparing fails closed.

## Consequences

### Positive
- SQL access works on Postgres out of the box, and stays in step with every upgrade.
- Access someone grants by hand to a credential table is taken back at the next boot, and
  anything the sync cannot remove switches SQL off instead of leaking.
- Settings tells the truth: an unavailable login shows its reason and its fix.

### Negative
- The app now issues `CREATE ROLE` / `GRANT` / `REVOKE` — DDL against a cluster-wide object. Two
  instances on one cluster get distinct logins by database name; names that sanitize to the
  same value would share one.
- An app login without `CREATEROLE` still needs one admin command, once.
- `pg_hba.conf` that refuses password sign-ins for a new login makes SQL unavailable; the
  reason says so and `ONW_SQL_CONSOLE_DATABASE_URL` remains the escape hatch.
- A fresh session per query costs a sign-in each time (milliseconds locally, more over TLS to
  a remote server) — accepted for a tool that runs occasionally.
- A login supplied through `ONW_SQL_CONSOLE_DATABASE_URL` gets its table permissions from the
  app: a narrower REVOKE an operator added (the old guide offered one for transcripts) is
  undone at the next boot.
- With `log_statement = ddl` or `all`, the server log records the managed login's password from
  `CREATE/ALTER ROLE`. A SCRAM verifier instead of the plaintext would close that; not done.

### Follow-ups
- `docs/product/operations/sql-console-postgres.md` rewritten from a setup guide to "it just
  works, and what to do in the rare case it can't".

## Alternatives considered
- **Keep the operator-made login and document the upgrade step** — rejected: nobody is told,
  and Settings showed the switch ON while SQL failed.
- **Turn SQL off on Postgres** — rejected by the owner: SQL access is an important feature and
  Postgres is a supported database (ADR `2026-08-07-storage-engine-sqlite-shipped-postgres-first-class`).
- **`SET ROLE` to a NOLOGIN role on the app's connection** — rejected: the session user can
  switch back inside the same statement, so it is not a boundary.
- **Row-level security keyed on a session variable** — rejected: `set_config` is callable from a
  SELECT, so the query could flip the variable.
- **Encrypt credentials at rest so nothing needs blocking** — worth doing for its own sake, but
  `connectors` and the demo's set are not credentials and would still need a boundary.

## Agent provenance

- **Decided by:** implementing Claude agent, with the repo owner directing ("the code needs to
  be able to update that … we can't expect them to do manually by running command").
- **Lane / branch:** `fix/sql-console-postgres-role-sync` (worktree `.claude/worktrees/sql-role-sync`).
- **Alternatives weighed:** document the manual step; disable on Postgres; `SET ROLE`; RLS;
  encryption at rest.
- **Driving artifacts:** a security review of this change (2026-10-28) reproduced a
  `options=-c role=…` bypass of `current_user`-based checks and a pooled-session advisory-lock
  freeze; both are fixed above and covered by tests. Commit `db5ed2f` (`sessions` made readable); ADR
  `2026-08-23-lucius-reads-the-database-directly`; commit `9dec6e858` (dedicated REVOKE-d role).
- **Why this way (one line):** the app owns the tables, so it can keep the boundary correct
  itself — and verifying from the restricted login keeps the database, not the app, the judge.

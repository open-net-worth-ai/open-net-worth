---
title: Lucius SQL access on Postgres
description: On PostgreSQL, Lucius's read-only SQL runs as a restricted login the app creates and keeps up to date itself. What it does, and the rare fixes.
category: operations
order: 57
audience: [operator]
status: stable
related: [operations/security-and-privacy, operations/exposing-beyond-localhost]
keywords: [Postgres, PostgreSQL, run_sql_query, SQL console, read-only role, CREATEROLE, ONW_SQL_CONSOLE_DATABASE_URL, ONW_DATABASE_URL]
---

# Lucius SQL access on Postgres

**Only for installs that keep the database in PostgreSQL** (`ONW_DATABASE_URL` set to a
`postgresql+psycopg://…` address). On the standard SQLite setup none of this applies.

Lucius can answer unusual questions by reading your data directly with a read-only SQL query.
It must never read the tables that hold your credentials. SQLite lets the app enforce that
itself; PostgreSQL doesn't, so on PostgreSQL Lucius's queries run as a **separate database
login that can't read those tables** — the database itself refuses.

## There's nothing to set up

The app handles that login for you:

- **It creates it** the first time it starts on PostgreSQL, named `onw_sql_<your database>`,
  with a random password it keeps with its other secrets.
- **It updates it at every start**, right after any database update, so tables added by a new
  version are readable and credential tables never are. You never run a `GRANT`.
- **It checks it on every query**: the login must not be able to read a credential table,
  switch to another database user, or hold powers like superuser. If anything is off, SQL
  access switches off and **Settings › Developer › SQL access** says why.

To check it works, ask Lucius *"Use SQL to count my transactions by year."* He should answer
with a table.

## When the app can't do it alone

Settings › Developer › SQL access shows **Not working on this database** with the reason, and
a command to copy when an admin can fix it. Once the fix is in, SQL access starts working within
a minute — no restart needed.

**"…isn't allowed to create the restricted login"** — the database login the app uses lacks the
`CREATEROLE` permission (some managed databases). A database admin runs the command shown, for
example:

```sql
ALTER ROLE "onw" CREATEROLE;
```

On PostgreSQL 15 and older, Settings suggests a different command instead: there, that
permission lets a login grant itself almost anything, so an admin creates the restricted login
directly and you point the app at it (below).

**"…the database refused a sign-in as onw_sql_…"** — the server doesn't accept password
sign-ins for new logins (`pg_hba.conf`). Allow it, or use your own login (below).

## Using a login you made yourself

If your database rules require it, create a login and point the app at it, then restart:

```bash
ONW_SQL_CONSOLE_DATABASE_URL=postgresql+psycopg://<login>:<password>@<host>/<database>
```

The app keeps that login's table permissions up to date at every start, exactly as it does
for its own — so a table you revoke from it by hand is readable again after the next start.
It refuses the app's own login (it must be a different one), a login that starts as another
role, one with powers like superuser or creating databases, and one that's a member of
another role.

## Limits

Every query runs as a single read-only statement, inside a read-only transaction, with a
10-second time limit. Results are capped at 200 rows by default (Lucius can ask for up to 1,000).
Your conversations (`sessions`, `session_messages`) are readable, so Lucius can find an earlier
chat; a public demo blocks them.

## Troubleshooting

**"Query exceeded the 10s limit"** — the query was too broad; ask Lucius to narrow it (a date
range, one account).

**Lucius says SQL access is unavailable** — open Settings › Developer › SQL access for the
reason, and see [When the app can't do it alone](#when-the-app-cant-do-it-alone).

## Related pages

- [Security and privacy](security-and-privacy.md)
- [Upgrading](upgrading.md#using-postgresql-instead-of-sqlite) — backups before database updates
  on PostgreSQL

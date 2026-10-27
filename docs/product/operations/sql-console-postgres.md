---
title: Lucius SQL access on Postgres
description: Lucius reads your data with a read-only SQL tool; on a PostgreSQL database it needs a dedicated, restricted database role. How to create it, step by step.
category: operations
order: 57
audience: [operator]
status: stable
related: [operations/security-and-privacy, operations/exposing-beyond-localhost]
keywords: [Postgres, PostgreSQL, run_sql_query, SQL console, read-only role, REVOKE, ONW_SQL_CONSOLE_DATABASE_URL, ONW_DATABASE_URL]
---

# Lucius SQL access on Postgres

**Only for installs that keep the database in PostgreSQL** (`ONW_DATABASE_URL` set to a
`postgresql+psycopg://…` address). On the standard SQLite setup there's nothing to do — this
works out of the box.

Lucius can answer unusual questions by reading your data directly with a read-only SQL query.
On SQLite the database itself stops him from reading the tables that hold credentials. PostgreSQL
has no equivalent, and checking the query text isn't reliable, so on PostgreSQL this tool stays
**off** until you give it its own database login that physically can't read those tables.

## Set it up

1. **Create a restricted role.** Connect to PostgreSQL as an admin and run (replace the database
   name `onw` and the password):

   ```sql
   CREATE ROLE onw_sql_console LOGIN PASSWORD 'choose-a-long-random-password';
   GRANT CONNECT ON DATABASE onw TO onw_sql_console;
   GRANT USAGE ON SCHEMA public TO onw_sql_console;
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO onw_sql_console;
   -- credential tables and connector settings: never readable through this role
   REVOKE ALL ON secret_config, connector_secrets, connectors FROM onw_sql_console;
   -- optional: also keep your conversations (titles and messages) out of Lucius's queries
   REVOKE ALL ON sessions, session_messages FROM onw_sql_console;
   ```

2. **Point the app at it** with a second connection address, then restart:

   ```bash
   ONW_SQL_CONSOLE_DATABASE_URL=postgresql+psycopg://onw_sql_console:<password>@<host>/onw
   ```

3. **Check it works:** ask Lucius *"Use SQL to count my transactions by year."* He should answer
   with a table instead of saying SQL access is disabled.

## Limits

Every query runs as a single read-only statement, inside a read-only transaction, with a
10-second time limit. Results are capped at 200 rows by default (Lucius can ask for up to 1,000).

## After an upgrade

`GRANT SELECT ON ALL TABLES` only covers tables that exist when you run it. If a new version adds
tables, re-run the `GRANT SELECT …` line so Lucius can read them — and if release notes mention a
new credential table, add it to the `REVOKE` line. The authoritative list of tables the role must
not read is `DENIED_TABLES` in `backend/db/sql_console.py`.

## Troubleshooting

**Lucius says "SQL access is disabled on Postgres: it needs a dedicated read-only database
role."** — `ONW_SQL_CONSOLE_DATABASE_URL` isn't set, or the app wasn't restarted after setting it.

**"Query exceeded the 10s limit"** — the query was too broad; ask Lucius to narrow it (a date
range, one account).

**Permission denied on a table** — a table was added after you ran the `GRANT`; re-run it.

## Related pages

- [Security and privacy](security-and-privacy.md)
- [Upgrading](upgrading.md#using-postgresql-instead-of-sqlite) — backups before database updates
  on PostgreSQL

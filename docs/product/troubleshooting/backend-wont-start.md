---
title: The app won't start
description: Fix the common reasons Open Net Worth won't start — port in use, book already open, a newer database, a failed upgrade — with the exact messages and steps.
category: troubleshooting
order: 10
audience: [user, operator]
status: stable
related: [getting-started/installation, reference/environment-variables, reference/ports-and-urls, reference/file-layout, troubleshooting/reporting-a-problem]
keywords: [backend, wont start, startup, migration, port in use, address already in use, already in use, newer version, desktop, logs]
---

# The app won't start

Find the message you see in the table, then follow that section. The message appears in the
terminal where you ran `onw start` / `make start`, in `docker compose logs`, or — in the
desktop app — on a page titled **Open Net Worth could not start** or **Book already open**.

| Message contains | Section |
|---|---|
| `address already in use` | [The port is already in use](#the-port-is-already-in-use) |
| `is already in use by pid …` / **Book already open** / `another process holds` | [Another copy is already using the folder](#another-copy-is-already-using-the-folder) |
| `migrated by NEWER code than this build` / "last opened by a newer version" | [The book is from a newer version](#the-book-is-from-a-newer-version) |
| `schema migration failed` | [An upgrade step failed](#an-upgrade-step-failed) |
| `has no alembic_version stamp` | [The database isn't one the app recognizes](#the-database-isnt-one-the-app-recognizes) |
| `ONW_GATEWAY_TOKEN must be at least 32 characters` | [A setting is invalid](#a-setting-is-invalid) |
| Anything else | [Find the real error in the log](#find-the-real-error-in-the-log) |

## The port is already in use

**Cause:** something else — often an earlier copy of Open Net Worth — is already listening on
port 8080.

**Fix:**

1. If an earlier copy is still running (another terminal, or a boot service from
   `onw autostart on`), use that one, or stop it first: `onw autostart off` for the boot
   service, <kbd>Ctrl</kbd>+<kbd>C</kbd> in its terminal otherwise.
2. Or start on another port: `onw start --port 8081` (from a checkout:
   `PORT=8081 make start`; Docker: `PORT=8081 docker compose up -d`), then open
   `http://localhost:8081`.

See [Ports and URLs](../reference/ports-and-urls.md#changing-the-port).

## Another copy is already using the folder

**Message:** `… is already in use by pid 12345 on port 8080. Two instances must not share a
working folder. Start this one with ONW_ROOT pointing somewhere else, or stop the other
first.` In the desktop app: **Book already open** — "This book is already open in another Open
Net Worth server on port 8080."

**Cause:** two copies of the app can't open the same book at once — it would corrupt your data.
Usually the other copy is a server you started earlier, a boot service, or the desktop app.

**Fix:**

1. Find and stop the other copy (the message names its port — open `http://localhost:<port>`
   to see it). Close the desktop app if it has this book open; run `onw autostart off` if you
   installed the boot service.
2. Start again. In the desktop app, click **Retry startup**.
3. If you meant to run a **second, separate** book, give it its own folder:
   `ONW_ROOT=~/onw-second onw start --port 8081`.

If the message says `another process holds … refusing to migrate`, another copy is open
*and* this one wanted to upgrade the database. If that copy is in the middle of upgrading, let
it finish — never kill an upgrade in progress. Otherwise stop it and retry.

## The book is from a newer version

**Message:** `database is at revision '…', which this checkout's Alembic scripts do not
recognize — it was migrated by NEWER code than this build. Refusing to touch it`. In the
desktop app: "This book was last opened by a newer version of Open Net Worth. Update the app
to open it."

**Cause:** a newer version of Open Net Worth already opened this book, and you're now running an
older one. The app refuses to touch it so nothing is lost — it hasn't changed anything.

**Fix:** update to the newest version, then start again.

- Desktop app: click **Check for updates**.
- `uv tool` install: `onw upgrade`.
- Docker: `docker compose pull && docker compose up -d` (or set `ONW_VERSION` to the newer
  release).
- Source checkout: `git pull`, then `make setup`.

## An upgrade step failed

**Message:** `schema migration failed (…). Database restored from the pre-migrate backup.
Refusing to boot.`

**Cause:** when a new version starts for the first time, it upgrades your database. It takes a
safety copy first; if the upgrade fails, it puts the safety copy back and stops. **Your data is
as it was before the upgrade.**

**Fix:**

1. Try starting once more — a one-off problem (a full disk, a file another program had locked)
   may have cleared. Check you have free disk space.
2. If it fails again, go back to the version you were using before — your book still works
   with it — and [report the problem](reporting-a-problem.md), including the message.

**If it runs as a boot service with `--auto-upgrade`**, it keeps restarting into this same error,
and it can't upgrade itself while it does. Run `onw autostart off`. Once a fixed version is out,
run `onw upgrade`, then `onw start --autostart --auto-upgrade`.

**If the message also says `the automatic restore ALSO failed`**, it names the safety copy to
use. Restore it by hand:

1. Stop the app.
2. In your working folder (`~/.open-net-worth` by default), open `data/backups/db/`. The
   safety copies are named `pre-migrate-<date>T<time>Z.db`; the newest five are kept.
3. Copy the one the message names (or the newest) over `data/onw.db`. Delete `data/onw.db-wal`
   and `data/onw.db-shm` if they exist.
4. Start the version you were using before.

## The database isn't one the app recognizes

**Message:** `existing database has no alembic_version stamp — refusing to touch it. No backup,
no upgrade, no restore.`

**Cause:** the `onw.db` file (or the Postgres database in `ONW_DATABASE_URL`) has tables in it,
but wasn't created by Open Net Worth — or was created by a very old build. The app won't guess.

**Fix:** check you're pointing at the right folder or database (`ONW_ROOT`, `DATA_DIR`,
`ONW_DATABASE_URL`). If you are, restore a backup from `backups/` instead (see
[Backup and restore](../operations/backup-and-restore.md)), or [report the problem](reporting-a-problem.md).

## A setting is invalid

The app refuses to start rather than run with a setting that would weaken your security. The
message names the variable — for example `ONW_GATEWAY_TOKEN must be at least 32 characters (got
12); refusing to start.` Fix or remove it, then start again. See
[Environment variables](../reference/environment-variables.md).

## Find the real error in the log

The terminal shows only part of the story. The full log is `logs/app.log` in your working
folder (`~/.open-net-worth/logs/app.log` by default; Docker: `docker compose logs app`). In the
desktop app, use **Help → Troubleshooting → Open Logs Folder**, or **Open logs folder** on the
error page.

Look at the last lines for the first `ERROR`. Then [report the problem](reporting-a-problem.md) —
the desktop app's **Report this problem** button, and the report window everywhere else, can
attach recent log lines for you with amounts and names masked.

## On Windows

Windows 10 and 11 are fully supported — the desktop app, `onw start`, and Docker all work. The
same messages and fixes above apply. Your working folder is `C:\Users\<you>\.open-net-worth`.

## Related

- [Where your files live](../reference/file-layout.md)
- [Ports and URLs](../reference/ports-and-urls.md)
- [Environment variables](../reference/environment-variables.md)
- [Reporting a problem](reporting-a-problem.md)

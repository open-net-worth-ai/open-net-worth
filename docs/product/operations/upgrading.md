---
title: Upgrading
description: How to update Open Net Worth for each way of installing it, and how the app protects your data while it updates its database.
category: operations
order: 30
audience: [user, operator]
status: stable
related: [operations/backup-and-restore, operations/desktop, operations/changelog, getting-started/installation]
keywords: [upgrade, update, onw upgrade, uv tool upgrade, docker pull, ONW_VERSION, git pull, make setup, migration, schema, downgrade]
---

# Upgrading

Updating never touches your book's folder except to bring its database up to the new version,
and it always takes a backup first. How you update depends on how you installed it.

## Update for your install

### Desktop app

It updates itself. A new version downloads in the background; when it's ready, the menu shows
**Restart to Update** and a bar at the bottom of the window offers **Restart** — choose it, or let
it install the next time you quit. After the update, the app shows once what's new in that version. To check right away: **Open Net Worth → Check for Updates…** on a Mac,
**Help → Check for Updates…** on Windows and Linux. Details in
[Desktop app § Updates](desktop.md#updates).

You can also re-run the installer from [Installing Open Net Worth](../getting-started/installation.md)
— it upgrades in place and never touches your book.

### Server installed with the one-line installer (`onw`)

1. Stop the app (press <kbd>Ctrl</kbd>+<kbd>C</kbd> where `onw start` is running, or
   `onw autostart stop` if it runs as a boot service).
2. Run:

   ```bash
   onw upgrade
   ```

   This is the same as `uv tool upgrade open-net-worth`. Re-running the install script works
   too.
3. Start it again: `onw start` (or `onw autostart install`).
4. Check: `onw version` prints the new version.

### Docker

The `docker-compose.yml` you downloaded is pinned to the version it came with, so
`docker compose pull` on its own does **not** move you to a newer release. Either:

- **Download the new compose file** (recommended — it always matches its release):

  ```bash
  curl -fsSLO https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest/download/docker-compose.yml
  docker compose pull
  docker compose up -d
  ```

- **Or name the version** you want, keeping your current file:

  ```bash
  ONW_VERSION=x.y.z docker compose up -d
  ```

  Put `ONW_VERSION=x.y.z` in the `.env` file next to `docker-compose.yml` to make it stick.

Your data is in the `open-net-worth-data` volume and survives both. Keep `AUTH_PASSWORD` set
(in `.env` or on the command line) as you did the first time. Check the new version with
`curl http://localhost:8080/api/health`.

### From a source checkout

Stop the running app first, then:

```bash
git pull
make setup
make start
```

`make setup` refreshes the Python and frontend dependencies, rebuilds the web app and updates the
database. (Running `onw upgrade` in a checkout tells you to do exactly this.)

## What the app does when the database needs to change

Most updates need no database change. When one does, the app handles it the first time it
starts, before it shows you anything:

1. **It backs up your database** (on the standard SQLite setup) to
   `backups/db/pre-migrate-<date>.db` inside your data folder, and checks that the backup opens.
   If the backup can't be verified, nothing changes.
2. **It applies the change.**
3. **If the change fails partway, it puts the backup back**, so you're left with the database you
   started with, never a half-updated one.
4. **If anything went wrong, the app refuses to start** rather than run on data it can't vouch for.
   The message says what failed and, if the automatic restore also couldn't finish, names the
   backup file to restore by hand.

The five most recent of these backups are kept; older ones are deleted automatically.

**If the app won't start after an update**, read the error first — it almost always says what to
do. See [Backend won't start](../troubleshooting/backend-wont-start.md).

## Going back to an older version

You can't. Once a newer version has updated your database, an older version refuses to open it
("it was migrated by NEWER code than this build") and leaves it untouched. To truly go back, you
would restore a backup taken before the update — see [Backup and restore](backup-and-restore.md).
In practice, the fix for a problem in a new version is the next update.

## Using PostgreSQL instead of SQLite

The automatic pre-update backup only exists for SQLite. On PostgreSQL, the app **refuses** to
apply a database change until you confirm you've taken your own backup:

1. Back up the database yourself (for example `pg_dump`).
2. Start the new version with `ONW_MIGRATE_ASSUME_BACKUP=1` set.

## Before a big update

The automatic backup covers the database change itself. Before a large version jump it's still
worth taking your own backup — **Settings → Data → Back up now** — so you have a restore point
you chose.

## Related pages

- [What's new](changelog.md) — changes that can make a figure read differently after an update
- [Backup and restore](backup-and-restore.md)
- [Desktop app](desktop.md#updates) — how the desktop app updates itself
- [Installing Open Net Worth](../getting-started/installation.md)

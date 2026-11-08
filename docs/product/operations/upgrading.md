---
title: Upgrading
description: How to update Open Net Worth for each way of installing it, and how the app protects your data while it updates its database.
category: operations
order: 30
audience: [user, operator]
status: stable
related: [operations/backup-and-restore, operations/desktop, operations/changelog, getting-started/installation]
keywords: [upgrade, update, auto-upgrade, automatic updates, onw upgrade, uv tool upgrade, docker pull, docker latest tag, ONW_VERSION, git pull, make setup, migration, schema, downgrade]
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

**To keep it up to date automatically**, start it with `--auto-upgrade`:

```bash
onw start --autostart --auto-upgrade
```

It checks for a new version 30 seconds after it starts and every 4 hours after that. When one is
out, it downloads it, checks that it starts in a temporary copy, waits until Lucius, your
Automations and any restore or import have finished, installs it and restarts itself. It's off unless you ask for it: without the flag, `onw start` never checks.

- **Only a signed release installs.** The release's signature and the download's checksum are
  checked first, and an older release is never installed. If a check fails, nothing changes and
  it tries again at the next check.
- **If the new version won't run,** it puts the old one back and keeps going on it. Once the new
  version has started and updated your book, there's no going back automatically — see
  [Going back to an older version](#going-back-to-an-older-version).
- **A restart takes a few seconds or more,** and an open browser tab reconnects by itself. A voice
  call in progress isn't noticed as busy: a restart can end it.
- **To turn it off,** run `onw start --autostart` without the flag. `onw autostart` shows
  whether it's on. `onw autostart on` also leaves it off.
- **In a terminal,** `onw start --auto-upgrade` without `--autostart` works too: it restarts
  itself in the same terminal (on Windows, in a new console window).
- **On Windows,** a separate helper does the install once the app has closed, because Windows
  can't replace a running program. Its log is `upgrade.log` in the folder
  `%APPDATA%\uv\open-net-worth-upgrades`.

To check by hand, `onw upgrade --check` says whether a newer version is out and changes nothing.

**To update by hand,** run this. You don't need to stop the app first:

```bash
onw upgrade
```

It gets the newest signed release, with the same checks as auto-upgrade. Use it rather than
`uv tool upgrade open-net-worth`: after an upgrade `uv` says there's nothing to upgrade, even when
there is.

- **If the app is running,** the app does the update itself. It waits until Lucius, your Automations
  and any restore or import have finished, installs the new version and restarts, in the same
  terminal if you started it in one (on Windows, in a new console window), or as the boot service.
  The command shows each step and tells you when the new version is running. If the app is busy for
  longer than a few minutes the command stops waiting, and the update carries on without it:
  `onw status` shows how it's going, and why it failed if it did.
- **If the new version doesn't come back,** the command says so, names the log and exits with an
  error code. `onw status` also shows where the log is.
- **If the app isn't running,** it installs the new version, which is used the next time the app
  starts.
- **If the running server is older than the install,** say after an update that didn't restart it,
  run `onw upgrade` again: it restarts that server even when the install is already up to date.
- **A server on a version from before this feature can't be asked.** The command says so and names
  its address. Restart that server yourself. On Windows, stop it, run `onw upgrade` again, then
  start it.
- **To check,** `onw status` shows the version the server is running, and `onw version` the version
  that's installed.

Re-running the install script also updates the app, but it doesn't restart a running server.

### Docker

The `docker-compose.yml` you downloaded is pinned to the version it came with, so
`docker compose pull` on its own does **not** move you to a newer release. Pick one:

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

- **Or follow a moving tag**, if you write your own Compose file:

  ```yaml
  image: ghcr.io/open-net-worth-ai/open-net-worth:latest
  ```

  `latest` moves to each new release, and `0.1` moves to each new `0.1.x` release. Run
  `docker compose pull && docker compose up -d` to update. New releases can come out as often as
  every night, and [you can't simply go back](#going-back-to-an-older-version) after an update, so
  only let a tool update these tags automatically if you're happy to take every release.

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

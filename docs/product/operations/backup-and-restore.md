---
title: Backup and restore
description: Turn on daily backups, take one on demand, download a credential-free copy, and roll this book back to an earlier point — step by step.
category: operations
order: 10
audience: [user, operator]
status: stable
related: [operations/export-and-portability, operations/security-and-privacy, operations/upgrading]
keywords: [backup, restore, back up now, daily backup, retention, download, sanitised, disaster recovery, credentials, make backup]
---

# Backup and restore

A backup is a complete copy of your book at one moment — every account, transaction, holding,
setting, memory and conversation. If something goes wrong (a mistake, a bad import, a bug), you
can put the whole book back the way it was.

Backups are for rolling **this** book back. To move your book to another computer or install,
use an [export](export-and-portability.md) instead.

## Set up backups

Daily backups are **on by default**. To check or change them:

1. Open **Settings → Data**.
2. Under **Automatic backups**:
   - **Back up daily** — leave it on. The backup runs once a day at about 3:30 in the morning
     (the clock of the computer running Open Net Worth).
   - **Keep for (days)** — how long to keep backups (default 30, anything from 1 to 365). Older
     ones are deleted automatically the next time a backup runs. A backup is a full copy, so it
     still holds anything you erased with **Delete permanently** after it was made, until it
     expires.
3. Click **Back up now** to take one immediately. The line underneath shows **Last backup** and
   how many are kept, and the new backup appears in the **Restore from a backup** list.

**Only runs while the app is running.** If Open Net Worth isn't running at 3:30 am (or within an
hour after), that day's backup is skipped. On a laptop that sleeps overnight, either keep it
awake, or click **Back up now** now and then — especially before a big import or cleanup.

You can also take a backup from a terminal in a source checkout with `make backup` (it shows up
labelled **Automatic**).

## Where backups are stored

In a `backups` folder next to your data, on the same computer:

| Install | Folder |
|---|---|
| Desktop app | `backups` inside your book folder (by default `~/.open-net-worth/backups`) |
| Server (`onw`, source checkout) | `~/.open-net-worth/backups` |
| Docker | `/home/onw/.open-net-worth/backups` inside the `open-net-worth-data` volume |

Each one is a `.zip` named by the time it was taken (UTC), for example `20261010T033000Z.zip`;
the ones you take by hand end in `-manual.zip`.

These backups live on the **same disk** as your book. To protect against losing the computer,
also keep a copy somewhere else — see the next section.

## Keep a copy somewhere else

In **Settings → Data → Restore from a backup**, click **Download** next to a backup. The
downloaded file has **every credential removed** — your login, API token, connector passwords and
keys, and AI provider key are deleted from it before it leaves the server — so it's safe to keep
in cloud storage or on a USB drive.

For moving to a **new** computer, an [export](export-and-portability.md) (**Export backup
(.zip)** on the same screen) is the file you need — a downloaded backup can't be loaded through
the app (see below).

## Restore a backup

1. Open **Settings → Data** and find the backup in **Restore from a backup** (each row shows its
   date, **Manual** or **Automatic**, and its size).
2. Click **Restore…**.
3. Read the confirmation — "Restore the backup from …?" — then click **Restore this backup**.
4. The app reloads with your book as it was at that moment. Anything added since then is gone.

Before restoring, the app **takes a fresh backup of your current book** (it appears in the list
labelled **Manual**), so if you picked the wrong one you can restore that and be back where you
started.

### What a restore does and doesn't change

Your book goes back: accounts, transactions, holdings, budget, settings, memory, chat history and
Automation run history all match the backup.

These stay **exactly as they are now**, whichever backup you restore:

- your login password and API token,
- every connector's credentials and your AI provider key,
- your tool permissions and approval decisions,
- the audit trail of changes, and the list of apps connected to your book.

So an old backup can't lock you out, bring back a permission you revoked, or erase the record of
how your data got here — and you never re-enter credentials after a restore.

Each book also carries an invisible identity that changes whenever the data is reset or
replaced (a backup restore, an import). A browser tab that was open on the old book notices the
change the next time it talks to the app, forgets its saved conversation list and cached screens,
and reloads, so you never see the previous book's conversations on the new one. Preferences such
as theme and panel sizes are kept.

## Things that can trip you up

- **"Restore from a file instead…" is not a backup restore.** That button (and **Choose import
  file…**) loads an **export** `.zip`, replacing your book with it. A downloaded backup is a
  different kind of file and will be refused there (typically "missing manifest.json").
- **A downloaded backup can't be uploaded back.** Restore only works with backups already in this
  install's `backups` folder.
- **PostgreSQL installs:** the backup covers the files in your data folder but **not the database
  itself**. Back up the database with your own tools (for example `pg_dump`).
- **Other automatic copies.** Before updating its database the app keeps a separate copy in
  `data/backups/db/` — see [Upgrading](upgrading.md). These, and the copy taken before a
  **Reset instance**, don't appear in the restore list.

## Related pages

- [Export and portability](export-and-portability.md) — moving your book to another install
- [Upgrading](upgrading.md) — the automatic backup before a database update
- [Security and privacy](security-and-privacy.md) — where credentials are kept

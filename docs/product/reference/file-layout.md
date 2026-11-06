---
title: Where your files live
description: The working folder on disk — one database, your uploaded files, daily backups, caches and logs — and what's safe to delete.
category: reference
order: 30
audience: [operator]
status: stable
related: [concepts/how-your-data-is-stored, reference/environment-variables, operations/backup-and-restore, troubleshooting/backend-wont-start]
keywords: [file layout, data directory, working folder, onw_root, sqlite, onw.db, backup, cache, logs]
---

# Where your files live

Everything the app keeps is in one **working folder**. By default that's `.open-net-worth` in
your home folder — the same for the server, `onw start` and the desktop app:

| System | Default working folder |
|---|---|
| macOS | `/Users/<you>/.open-net-worth` |
| Linux | `/home/<you>/.open-net-worth` |
| Windows | `C:\Users\<you>\.open-net-worth` |
| Docker | `/home/onw/.open-net-worth` inside the container, kept in the `onw-data` volume. Claude Code, Codex and OpenCode sign-ins are kept apart from your data, in the `onw-claude`, `onw-codex` and `onw-opencode` volumes |

It's a hidden folder (the name starts with a dot). On macOS press <kbd>⌘</kbd> <kbd>Shift</kbd>
<kbd>.</kbd> in Finder to show hidden folders; on Windows turn on **View → Hidden items** in
File Explorer. In the desktop app, **Help → Troubleshooting → Reveal Book Folder** opens it for
you.

To keep it somewhere else — another drive, a NAS — set `ONW_ROOT` (see
[Environment variables](environment-variables.md#where-things-live)); the desktop app lets you
pick the folder instead.

## What's inside

```
~/.open-net-worth/
├── data/
│   ├── onw.db              ← everything: accounts, transactions, budgets, holdings,
│   │                          settings, connector setup, Lucius's memory and chats
│   ├── files/
│   │   ├── attachments/     ← files you've added in chat or Documents
│   │   ├── receipts/        ← receipts attached to transactions
│   │   ├── statements/      ← imported statement files and what was read from them
│   │   └── artifacts/       ← documents and charts Lucius made for you
│   ├── skills/              ← Lucius skills you've created or installed
│   ├── checkpoints/         ← the data behind undoing a Lucius change
│   └── backups/db/          ← automatic safety copies taken just before an upgrade
├── backups/                  ← daily backups, one .zip each
├── cache/                    ← prices, exchange rates, icons — rebuilt on demand
└── logs/
    └── app.log               ← the application log
```

You may also see `onw.db-wal` and `onw.db-shm` next to `onw.db` while the app is running —
they're part of the database. Never delete or copy them separately.

A hidden `.instance.lock` file at the top marks the folder as in use, so a second copy of the
app can't open the same book at the same time.

## What matters, and what's safe to delete

| Folder | If you lose it |
|---|---|
| `data/` | **This is your book.** Back it up. |
| `backups/` | Your daily safety copies. Keep them — ideally also somewhere off this machine. |
| `cache/` | Nothing. Delete it any time; prices and rates re-download on next use. |
| `logs/` | Nothing but troubleshooting history. |

## Backups

- **Daily backups** (`backups/`): a full copy of `data/` as a `.zip`, made automatically every night
  and kept for 30 days by default. Turn them on or off, change how long they're kept, or make
  one now with **Back up now** in **Settings → Data → Automatic backups**. Restore one from
  **Settings → Data → Restore from a backup**. See [Backup and restore](../operations/backup-and-restore.md).
- **Upgrade safety copies** (`data/backups/db/pre-migrate-<time>.db`): taken automatically
  right before the app upgrades its database. The newest five are kept. You normally never
  touch them — [The backend won't start](../troubleshooting/backend-wont-start.md) explains
  when to use one.

To move to a new machine, stop the app and copy the whole working folder, or use
**Settings → Data → Export backup (.zip)** and import it on the other side with **Choose
import file…**. An exported backup leaves out your password, API token and connector
credentials, so you re-enter those after importing.

## Related

- [How your data is stored](../concepts/how-your-data-is-stored.md)
- [Environment variables](environment-variables.md)
- [Backup and restore](../operations/backup-and-restore.md)

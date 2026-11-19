---
title: How your data is stored
description: What self-hosted means here — one database in a folder on your machine, daily backups, and exactly which features connect to the internet.
category: concepts
order: 80
audience: [user, operator]
status: stable
related: [reference/file-layout, operations/backup-and-restore, operations/security-and-privacy, reference/environment-variables]
keywords: [self-hosted, sqlite, database, privacy, backup, data directory, working folder, telemetry, outbound]
---

# How your data is stored

Open Net Worth doesn't keep your finances on a company server. There's no sign-up and no cloud
sync. Everything you enter — accounts, transactions, budgets, holdings, documents, even Lucius's
memory and conversations — lives in **one database file**, inside a folder on the machine that
runs the app.

## Where it lives

By default, in a folder called `.open-net-worth` in your home folder. It holds the database,
the files you've added (statements, receipts, chat uploads), daily backups, and logs.

To keep it somewhere else — another drive, a NAS — set `ONW_ROOT` when you start the server, or
choose a different folder in the desktop app. See [Where your files live](../reference/file-layout.md)
for the full layout.

Copying that one folder (with the app stopped) copies everything — that's all it takes to move
to a new machine.

## Backups

The app makes a full backup of your data **every night**, automatically, and keeps 30 days of
them. You can change that, back up on demand, or restore one in **Settings → Data**:

- **Automatic backups** — **Back up daily** on or off, **Keep for (days)**, and **Back up now**.
- **Restore from a backup** — pick any saved backup to go back to it.
- **Export backup (.zip)** — download a full copy to keep somewhere else. It leaves out your
  password, API token and connector credentials.

Backups are stored next to your data on the same machine, so also keep a copy elsewhere. See
[Backup and restore](../operations/backup-and-restore.md).

Separately, before Lucius changes anything, the app takes a snapshot so the change can be
undone.

## What connects to the internet

Open Net Worth has no analytics, no usage tracking and no automatic crash reporting. Your
balances, transactions and net worth are never sent to the Open Net Worth project.

Some features do connect out, and each sends only what it needs:

**On by default:**

- **Prices and exchange rates** — from Yahoo Finance and CoinGecko. Only ticker symbols and
  coin names are sent, never what you hold or how much.
- **News for Intel** — news and filings about the companies you hold or watch, from public feeds
  (such as Google News and SEC filings), plus those sites' icons. Turn it off with
  **Settings → Intelligence → Monitoring → Monitoring active**; that also pauses background
  price checks.
- **Desktop app update checks** — the desktop app asks `opennetworth.ai` for updates every few
  hours, sending its version and platform. Servers and Docker never check.

**Only when you set them up:**

- **Your AI provider** — when you use Lucius, your messages and the data he looks up to answer
  go to the provider you chose (Anthropic, OpenAI, a local model…), with your own key or
  subscription. Lucius also uses it in the background for things you turn on, such as
  Automations and briefs on your watchlist. Nothing goes through a server run by this project.
  With no provider connected, nothing is sent and the rest of the app still works.
- **Connectors** — email, Telegram, a bank or broker link, web search, an external MCP server.
  Each one talks only to its own service, and none is on until you add it.
- **Problem reports** — only when you open **Report a problem**, read it, and click **Send
  report**. See [Reporting a problem](../troubleshooting/reporting-a-problem.md).

The full list, with how to turn each off, is in
[Security and privacy](../operations/security-and-privacy.md#what-leaves-your-computer-and-when).

## Private Cloud

If you choose to run your book on a Private Cloud server instead of your own machine, the same
single database lives on that server, and you sign in with your Private Cloud account. See
[Using it from more than one device](../operations/multi-device.md#on-a-private-cloud-server).

## Related

- [Where your files live](../reference/file-layout.md)
- [Backup and restore](../operations/backup-and-restore.md)
- [Security and privacy](../operations/security-and-privacy.md)

## Guides and comparisons

Longer write-ups on the Open Net Worth blog:

- [Private AI financial assistant: where your data goes](https://opennetworth.ai/blog/where-your-financial-data-goes/): where your data goes with cloud apps, AI chats and a self-hosted agent

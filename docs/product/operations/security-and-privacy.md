---
title: Security and privacy
description: Where your data lives, how sign-in is protected, what leaves your computer and when, and the settings that control each.
category: operations
order: 40
audience: [user, operator]
status: stable
related: [operations/exposing-beyond-localhost, operations/backup-and-restore, lucius/permissions]
keywords: [security, privacy, auth, password, API token, PBKDF2, data, local, self-hosted, telemetry, outbound, hide amounts, problem report]
---

# Security and privacy

Open Net Worth runs on your own computer or server. Everything it knows about your money lives
there, protected by one password, and nothing about how you use it is collected or reported.

## Where your data lives

In one folder, by default `~/.open-net-worth` in your home folder (the desktop app lets you choose
another; Docker keeps it in the `open-net-worth-data` volume):

- `data/onw.db` — one database file with your ledger, accounts, budget, holdings, settings, chat
  history, and every saved credential (connector passwords and keys, your AI provider key).
- `data/files/` — receipt images, uploaded statements and other attachments.
- `backups/` and `logs/` — see [Backup and restore](backup-and-restore.md) and [Logs](logs.md).

The database and files are readable only by the account that runs the app. They aren't encrypted
on disk, so anyone who can sign in to that computer as you can read them — use your operating
system's disk encryption (FileVault, BitLocker, LUKS) to protect a lost or stolen machine.
Advanced installs can keep the database in PostgreSQL instead (`ONW_DATABASE_URL`).

## Login

One password, set in **Settings → Security** — there are no separate user accounts.

- **The password is never stored**, only a strong one-way hash of it (PBKDF2-SHA256, 600,000
  rounds, a fresh random salt each time it's set).
- **Lockout:** five wrong passwords from one address lock that address out for 15 minutes ("too
  many attempts, try again later"). The count resets when the app restarts.
- **Staying signed in:** 30 days by default; change it under **Settings → Security → Auth
  behaviour → Stay signed in for** (8 hours, 7 days or 30 days).
- **Changing your password signs out every other device** at once; the device you changed it on
  stays signed in. You always need the current password to change it.
- **Password-free on this computer.** By default, the computer running Open Net Worth doesn't
  have to sign in — so local tools and the browser on that machine just open. Turn off **Don't
  ask for the password on this computer** (in **Settings → Security → Auth behaviour**) on a
  shared computer, and always before putting a reverse proxy in front of it (see
  [Exposing this beyond localhost](exposing-beyond-localhost.md)).
- **Before a password is set**, the app opens only on its own computer and refuses every other
  device, so nobody on your network can claim it first.
- **Removing the password** (**Remove password** in Settings → Security, which asks for the
  current one) returns to "no password yet": this computer opens as before and every other device
  is refused. It isn't offered while the password comes from `AUTH_PASSWORD` /
  `AUTH_PASSWORD_HASH`, or while **Don't ask for the password on this computer** is off.
- **Disable auth entirely** (same screen) accepts every request without a password. Only use it
  behind a private network you fully trust.
- **Forgot the password?** Reset it on the machine itself — in the desktop app, or with
  `onw reset-password` on a server. See [I lost my password](../troubleshooting/lost-password.md).

### The API token

Other AI apps, the `onw mcp` bridge and phone shortcuts sign in with an **API token** instead of
the password. It's in **Settings → Security → API token** (**Reveal**, **Copy**, **Rotate**).

- It's separate from your password — changing the password doesn't affect it.
- **Rotate** invalidates the old token immediately, for every app and Shortcut using it, without
  signing out any browser. There's no confirmation, so be sure first.
- Rotating also closes any open phone setup page (Settings → Phone shortcuts), since that page
  shows the current token as part of the setup code.
- Setting a password for the **first** time also replaces the token; copy it again into any app
  you'd already connected.

## What leaves your computer, and when

No analytics, no usage tracking, no crash reporting, and no licence check.

These connections happen **without you asking**, as part of normal running:

| What | Sends | Turn it off |
|---|---|---|
| **Prices** (Yahoo Finance, CoinGecko) | A ticker or coin id for anything you hold or watch — never amounts or account names. | Prices are always on; pause the background checks under **Settings → Intelligence → Monitoring active**. See [Price sources](../connectors/price-sources.md). |
| **News for Intel** | Requests to news feeds (Google News, Bing, Yahoo Finance, SEC and other publishers) about your holdings and watchlist names, plus fetching those sites' icons. | **Settings → Intelligence → Monitoring active**, or turn sources off in Intel. |
| **Desktop app update check** | Your app version and platform, to `opennetworth.ai` (Linux: GitHub). Every 4 hours. | Start the app with `ONW_DESKTOP_UPDATES=0`. Servers and Docker never check. |

These happen **only because you set them up**:

| What | Sends |
|---|---|
| Your AI provider | Your conversation with Lucius and the data he looks up for it, using your own key or subscription — see [Models and cost](../lucius/models-and-cost.md). Nothing goes through a server run by this project. |
| Connectors | [Email](../connectors/email-ingestion.md) (read-only), [Telegram](../connectors/telegram.md), a [web search provider](../connectors/web-search.md), a [bank or brokerage sync](../connectors/bank-brokerage.md), [external MCP servers](../connectors/external-mcp.md). Each page says what it sends. None is on by default. |
| A problem report | Only when you open **Report a problem** and press **Send report** — see below. |

### Problem reports

The only thing the app ever sends about itself is a problem report, and only when you press
**Send report**. You see the whole report first and can edit or cancel it. It holds what you
wrote, an optional **Email (optional)** contact, the app version, your operating system, the
screen you were on and the error — and, only if you tick **Include recent logs**, up to 200 recent
log lines with amounts, names and keys masked. It never contains your book, accounts or
attachments, and nothing is sent automatically, queued or retried. See
[Reporting a problem](../troubleshooting/reporting-a-problem.md).

### Addresses you type into Settings

A model endpoint, a webhook or an HTTP MCP server is an address you chose, so on a self-hosted
install it may point at this computer or your home network (a local Ollama, Home Assistant, a
Tailscale host). Cloud-metadata and link-local addresses (`169.254.x.x`) are always refused, and
redirects are re-checked at every hop. Set `ONW_ALLOW_PRIVATE_ENDPOINTS=0` to refuse local and
home-network addresses too; on a hosted (gateway) instance they're always refused. Addresses that
come from a model or a web page (search results, page fetches) may only ever be public sites.

## Deleting something for good

**Delete** on a transaction keeps a record of it, so Lucius's undo still works and the same bank
email isn't booked twice. **Delete permanently** erases its contents everywhere the app keeps
them, except backups made before (until they expire) and files you uploaded. Details:
[Delete or erase a transaction](../features/transactions.md#delete-or-erase-a-transaction).
Deleting a conversation or something Lucius remembers erases it straight away.

## Untrusted content

Anything from outside the app — an email, a web page, a news article, a price feed, another MCP
server's output — is treated as information for Lucius to read, never as instructions to follow.
No AI model ignores planted instructions every time, so the app doesn't rely on that alone. The
ways out of the app are locked down too:

- **Opening a web address** happens without asking only for a link you typed or a web search
  returned, or, in an Automation, a website on that Automation's own list. Any other address shows
  a card first, because a planted instruction could hide your data in it. See
  [Permissions](../lucius/permissions.md).
- **Reports and charts Lucius makes** can't load or open anything on the internet.
- **Sending, posting and changing things** follow your [Permissions](../lucius/permissions.md).

## Hiding amounts on screen

Press <kbd>H</kbd>, or use **Hide amounts** in the sidebar (on a phone, in the **More** menu), to
replace your figures with `***` across the app, charts included — handy when sharing your screen.
Market prices and daily % changes in the ticker strip stay visible. Press <kbd>H</kbd> again to
show them.

## Related pages

- [Exposing this beyond localhost](exposing-beyond-localhost.md) — reaching it safely from
  elsewhere
- [Backup and restore](backup-and-restore.md) — how downloaded backups strip credentials
- [Permissions](../lucius/permissions.md) — what Lucius may do
- [Use your book from other AI apps](connect-ai-apps.md) — what an app with your API token can do

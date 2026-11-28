---
title: FAQ
description: Quick answers — cost, privacy, phones, sharing with a partner, what works without AI, and where to get help.
category: troubleshooting
order: 80
audience: [user]
status: stable
related: [getting-started/what-is-open-net-worth, getting-started/connecting-an-ai-provider, concepts/how-your-data-is-stored, troubleshooting/reporting-a-problem]
keywords: [faq, questions, cost, free, privacy, multi-user, mobile, phone, windows, license]
---

# FAQ

## Is it free?

Yes. Open Net Worth is open source (AGPL-3.0) with no subscription. The only cost is the AI
provider you connect for Lucius, which you pay directly — or nothing, if you use a Claude Code
or Codex subscription you already have, or skip AI. See
[Connecting an AI provider](../getting-started/connecting-an-ai-provider.md#what-it-costs).

## Does my data go anywhere?

Not on its own. It lives in one database in a folder on your machine. Only features you turn on
talk to the internet: price updates, the AI provider you connect, and connectors you add. See
[How your data is stored](../concepts/how-your-data-is-stored.md).

## Which computers does it run on?

macOS, Windows 10/11 and Linux — the desktop app on all three, or a server via `onw` or Docker.
See [Installing Open Net Worth](../getting-started/installation.md).

## Can I use it on my phone?

Yes. Open the app's address in your phone's browser — every screen works at phone size, with the
less-used screens under **More**. Your phone needs to reach the machine running the app; see
[Reaching it from another device](../reference/ports-and-urls.md#reaching-it-from-another-device)
and [Using it from more than one device](../operations/multi-device.md).

**Settings → Phone shortcuts** sets up logging a purchase the moment you pay: a shortcut on every
Apple Pay tap on an iPhone, or a Tasker/MacroDroid automation on Android. See
[Log a spend from your phone](../features/phone-shortcuts.md). You can also chat with Lucius from
your phone through [Telegram](../connectors/telegram.md).

## Can my partner and I share it?

Yes, as one shared book: there's one password per instance, not separate accounts. Anyone you
give the password to sees and can change everything. To keep finances separate, run two
instances, each with its own working folder.

## What happens if I don't connect an AI provider?

Accounts, transactions, budgeting, investments, net worth, and importing CSV, OFX and QIF files
all work without one. You need a provider for chatting with Lucius, reading PDF statements and
receipts, categorizing with AI, Automations, and Lucius's memory.

## Which browsers are supported?

Any current version of Chrome, Edge, Firefox or Safari.

## Are any parts still experimental?

Yes — Intel (market news and signals), equity compensation (RSUs and vesting), retirement and
FIRE projections, and the bank and brokerage connectors are marked **beta**. They work, but
double-check their numbers before relying on them. Accounts, budgeting and investment tracking
are the most thoroughly tested parts.

## How do I back up my data?

It's automatic: a full copy every day, kept for 30 days. Check or change it in
**Settings → Data → Automatic backups**. See [Backup and restore](../operations/backup-and-restore.md).

## I forgot my password

See [I lost my password](lost-password.md).

## I have a question this didn't answer

Ask Lucius — he can read this whole help center. If something's broken, use
[Send feedback](reporting-a-problem.md).

## Related

- [What Open Net Worth is](../getting-started/what-is-open-net-worth.md)
- [How your data is stored](../concepts/how-your-data-is-stored.md)
- [Reporting a problem](reporting-a-problem.md)

---
title: Settings
description: Every Settings section and what it is for — profile, AI, currency and dates, appearance, security, backups, Intel and more.
category: features
order: 160
audience: [user, operator]
status: stable
related: [concepts/base-currency-and-fx, lucius/permissions, lucius/models-and-cost, operations/backup-and-restore, operations/security-and-privacy]
keywords: [settings, profile, currency, time zone, date format, appearance, theme, security, password, api token, backup, developer, duplicate detection, mcp, phone shortcuts, iphone shortcuts]
---

# Settings

Settings is where you tell Open Net Worth about yourself (profile, income, retirement
assumptions) and choose how this copy of the app behaves (AI provider, currency, dates, theme,
security, backups). Open **Settings** from the sidebar, then type in the **Search** box at the top
of the section list to jump to a setting by name — "password", "backup", "time zone".

On a phone the sections are a grouped list — tap one to open it, and use the back arrow to return.

## Common tasks

**Change your base currency**
1. Open **Settings → Currency & format**.
2. Pick a new **Base currency**.
3. Read the confirmation: accounts held in another currency convert at current rates, but an
   amount you typed by hand in the old base currency is re-read as the new one. Confirm only if
   that is what you want.

**Set your time zone and date format**
1. Open **Settings → Currency & format**.
2. Under **Time zone**, pick your zone. It decides what "today" is, the time of day Lucius sees,
   and the default date on new entries. The browser's zone is detected the first time; a zone
   you pick yourself is never overwritten.
3. Under **Number & date format**, choose a **Date format** (31/12/2026, 12/31/2026 or
   2026-12-31) and a **Number format**. Left on **Browser default**, they follow your browser.

**Switch to dark mode**
1. Open **Settings → Appearance**.
2. Under **Theme**, set **Color scheme**. It applies to this device only, straight away.

**Set or change your password**
1. Open **Settings → Security**.
2. Enter your **Current password** (if one is set), then the **New password** (at least 8
   characters) and **Confirm password**.
3. Click **Set password** (or **Change password** / **Reset password**).
   Forgot it? See [Lost password](../troubleshooting/lost-password.md).

**Back up your data now**
1. Open **Settings → Data**.
2. Under **Backup & restore**, click **Export backup (.zip)**. Automatic daily backups are
   configured in the same section.

## The sections

| Section | What it is for |
|---|---|
| **Profile** | **About you** (name, what Lucius should call you, date of birth, marital status, risk tolerance, nationalities, countries of residence, tax residencies, employment), **Income & expenses**, **FIRE settings** (retirement spending, withdrawal rate, expected return, volatility, target retirement age, life expectancy, monthly saving — these drive [Retirement and FIRE outlook](retirement-planning.md)), and **Notes for Lucius**. Everything here is context Lucius reads; an empty field is just a gap in what he knows. |
| **AI** | Which AI provider and model run Lucius: **AI providers** (add an API key or connect a Claude/ChatGPT subscription), **Preferred models**, **Task models**, and **Stop a turn after** (how long one reply or Automation run may take). See [Connecting an AI provider](../getting-started/connecting-an-ai-provider.md) and [Models & cost](../lucius/models-and-cost.md). |
| **Chat** | How the chat behaves: follow-up suggestions, cost per turn, multi-turn chat, grouping reasoning into one block. |
| **Memory** | What Lucius remembers about you: **Generate memory from chats** turns it on or off, and below it everything he remembers, by section and topic — open a topic to edit or forget what's in it. See [Memory](../lucius/memory.md). |
| **Permissions** | Which tools Lucius (and Automations) may use without asking first. See [Permissions](../lucius/permissions.md). |
| **Duplicate Detection** | How imported transactions are matched against what is already in your ledger: **Auto-merge policy**, the **AI judge** and its **Judgment model**, **Max AI checks per run**, and **Remembered decisions** — pairs you already ruled on, which are never re-flagged unless you forget the decision. |
| **Intelligence** | Every Intel setting: monitoring cadence, where each kind of alert is sent, Lucius's explained alerts and reads, how much the briefs say, and crash thresholds. See [Intel settings](intel.md#settings). |
| **Appearance** | **Theme** — the color scheme and the scrolling price ticker in the status bar, per device. Market prices (gold, coins, FX, large stocks) join the ticker once you hold a market position or follow a watchlist; **Always show market prices** shows them regardless. **Appearance** — the app name, accent color and logo for this whole instance (everyone using it sees them). Changes save as you make them. |
| **Desktop** | Only in the desktop app: keep running when the window closes, start at login, desktop notifications, and **Statement folders** — folders the app watches so new statements appear for review. See [Desktop app](../operations/desktop.md). |
| **Phone shortcuts** | Log a spend from your phone the moment you pay. **iPhone**: show a QR to open the setup page on your phone, or open it on the iPhone itself to get the steps and an **Install shortcut** button; the **Setup code** field (**Reveal**, **Copy**) holds the address + API token it asks for. **Android**: the capture address and your API token for Tasker or MacroDroid. See [Log a spend from your phone](phone-shortcuts.md). |
| **MCP** | Copy-paste configurations that let Claude Desktop, Cursor, VS Code, ChatGPT and other AI apps use your book. See [Use your book from other AI apps](../operations/connect-ai-apps.md). |
| **Currency & format** | **Base currency**, **Number & date format**, and **Time zone** (see the tasks above). Date fields in forms use your date format too. |
| **Security** | Your **Password**, the **API token** that other apps (Claude Desktop, the CLI, phone shortcuts) use to sign in, and **Auth behaviour** — how long you stay signed in, and whether this computer may skip the password. See [Security & privacy](../operations/security-and-privacy.md). |
| **Data** | **Backup & restore** (export a full .zip), **Automatic backups** (daily, and how many days to keep), **Restore from a backup**, **Uploaded files** from chat, and resetting the instance. See [Backup & restore](../operations/backup-and-restore.md). |
| **Terminal** | A shell on the machine the app runs on. Shown only where it is allowed; off by default when you host the app yourself. See [Terminal](../operations/terminal.md). |
| **Help** | The **Help center** (these pages, available offline), **Ask Lucius**, and **Send feedback**. |
| **Developer** | **Application logs**, **Code execution** (let Lucius run short Python on this server), **Extensions**, **Bundled script skills**, and **SQL access** (Lucius's read-only queries and their log). See [Logs](../operations/logs.md). |
| **Cost & Performance** | **AI spend** over a period you choose, search and page-fetch costs, and model speed and reliability. |

## Things to know

- **Theme is per device; branding is per instance.** The color scheme follows the device you set
  it on. The app name, accent color and logo apply to everyone using this instance.
- **Changing base currency doesn't rewrite hand-typed amounts.** Read the confirmation dialog
  before you accept.
- **Profile fields aren't private from Lucius.** They exist so he can give advice that fits you.
  Anything you'd rather he not know, leave blank.
- **Some sections only appear where they apply** — Desktop in the desktop app, Terminal where it
  is enabled.

## Related pages

- [Base currency & FX](../concepts/base-currency-and-fx.md)
- [Connecting an AI provider](../getting-started/connecting-an-ai-provider.md)
- [Permissions](../lucius/permissions.md)
- [Models & cost](../lucius/models-and-cost.md)
- [Backup & restore](../operations/backup-and-restore.md)
- [Security & privacy](../operations/security-and-privacy.md)
- [Use your book from other AI apps](../operations/connect-ai-apps.md)

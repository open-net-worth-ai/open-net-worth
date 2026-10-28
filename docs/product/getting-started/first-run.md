---
title: Your first run
description: The three setup screens — currency, AI model, optional password — then Lucius sets up your accounts with you in a short chat.
category: getting-started
order: 30
audience: [user]
status: stable
related: [getting-started/installation, getting-started/connecting-an-ai-provider, getting-started/your-first-account-and-transactions, troubleshooting/lost-password]
keywords: [first run, onboarding, welcome, setup, lucius, currency, password, restore, docker, screen intro]
---

# Your first run

Setting up takes two parts:

1. **Three quick setup screens** get the app ready: the currency you think in, an AI model for
   Lucius, and an optional password. About two minutes.
2. **A short chat with Lucius** sets up your book: he asks where your money is and adds your
   accounts with you. Screenshots from your banking app are the fastest way.

Have one thing ready before you start: a way for Lucius to think — a Claude or ChatGPT
subscription, an API key, or a local model. [Connecting an AI provider](connecting-an-ai-provider.md)
shows how to get each one.

## Before the steps: Welcome

The first screen says what Open Net Worth is and what happens next. Click **Get started**.

**In the desktop app** you then see **Where should we keep your book?**

- **Continue** keeps the suggested folder (`~/.open-net-worth`), or click **Change** next to
  **Book folder** to pick another. A folder that already holds a book opens as it is — you skip
  setup entirely.
- **On your own server** connects to an Open Net Worth server you already run instead. Enter its
  address (for example `http://192.168.1.20:8080` or `https://onw.example.com`), click
  **Connect**, and sign in with that server's password. Your book, AI model and automations stay
  on the server. See [Desktop app § Connect to a server](../operations/desktop.md#connect-to-a-server).

**In a browser** (a server install) the folder is already decided — the next screen shows it as
**Working folder**, and says so if it already holds a book.

## Step 1: Your currency

Pick **the currency you think in** — your base currency. Every total and report is shown in it.
Accounts in other currencies still work; they convert into this one automatically.

1. Check the **App currency** row. It's guessed from your browser's language settings.
2. To change it, click **Change** and search by code or name (for example `SGD` or `euro`).
3. Click **Continue**.

You can change it later in **Settings → Currency & format**, but it's a bigger decision than it
looks — [Base currency and FX](../concepts/base-currency-and-fx.md) explains why.

### Restoring a backup instead

Moving from another computer? On this screen, click **I have a backup to restore**:

1. Click **Choose a backup file** and pick an Open Net Worth export `.zip` (made in
   **Settings → Data** on the old machine).
2. Check the preview — currency and record counts. Nothing is written yet.
3. Click **Restore this backup**. Setup then continues at Step 2.

An export never contains AI provider keys or your password, so you set those again here.

## Step 2: Connect Lucius

Lucius needs an AI model. This step has no skip — every option below works, and one of them is
free.

**Use a subscription you already pay for (no API key).** Claude Code and Codex are listed even
if you haven't installed them yet:

1. Under **Claude Code** (Claude subscription) or **Codex** (ChatGPT subscription), expand
   **Install & sign in**.
2. Copy the install command and run it in a terminal **on the computer running Open Net Worth**.
3. Open a new terminal and run the sign-in command it shows (`claude auth login` or
   `codex login`), and choose your subscription when asked.
4. Back in the app, the row changes to **Found … on this machine** on its own within a few
   seconds. Click **Use this**.

**Or use an API key or a local model.** Below **or use a provider**:

1. Pick a **Provider** — OpenRouter, Anthropic, OpenAI, Google Gemini, Ollama (local) and more.
2. Paste its **API key** (or, for a local model, check the **Endpoint**).
3. Click **Continue**. The app checks the key with the provider and fetches its model list.

Then choose the **Model** (a sensible default is picked; click **Change** to search the list)
and click **Continue**.

Step-by-step instructions for each provider, including where to get a key and what it costs, are
in [Connecting an AI provider](connecting-an-ai-provider.md).

## Step 3: Secure the vault (optional password)

A password only matters if you'll open the app from **another device** — your phone, another
computer, a reverse proxy. On the machine running it, you're trusted without one.

- To set one: type it in **Password** (at least 8 characters) and again in **Confirm**, then click
  **Set & finish**. It's kept exactly as typed, spaces included.
- To skip: click **Skip**. You can add one any time in **Settings → Security**.

If the book already has a password (a Docker install, or after a reset), this screen just says
so — click **Finish**.

**Desktop app:** after setup, a **Ready when you need it** screen offers **Keep running in the
background** and **Start at login**. Both are off unless you turn them on; change them later in
**Settings → Desktop**.

## Then: Lucius sets up your book

Setup drops you into a chat with Lucius. With an empty book, he leads, one question at a time,
and gets your money in before asking anything personal:

1. Asks your name and what to call you.
2. Mentions you can add a few details about yourself in **Settings → Profile** whenever you like,
   for advice that fits you more closely. No questions.
3. Asks what you'd like to do first: see where your money goes, see everything in one place, set
   up a budget, check whether your money will last, or keep an eye on your investments. The five
   choices appear as buttons under his message straight away; tap one or answer in your own
   words. The rest follows your answer.
4. Gets your money in, starting from **today's balances** — no history needed. Tell him each
   account's bank and balance, or drop a screenshot of your banking app's account list (he lights
   up the paperclip as he asks) and he confirms before saving. Your accounts start from today and
   new transactions arrive from there. Past transactions are optional: on the spending and budget
   paths he offers once to bring in last month's statement, so you can see where it went. You can
   also connect a bank in **Connectors**, or send a statement file (CSV, OFX, QIF, XLSX, PDF and
   more) any time; he categorizes what he imports.
5. Shows you what's now live (your net worth, on the **Dashboard**) and offers to keep the book
   current from your bank, card and broker emails. You connect your email yourself in
   **Connectors → Email** — he never asks for a password in chat — then he sets up the
   **Auto-log transactions from email** automation. See [Email](../connectors/email-ingestion.md).
6. If you hold investments (you can skip this): reads them from a screenshot, a statement or a
   list, and confirms each one before saving.
7. Closes with two or three useful next steps — for example a daily briefing or a budget — and
   offers a 20-second tour of his own controls.

Personal details (age, where you live, tax residency, income and spending) come up later, only when
something you ask needs them, and he says why. On the retirement path he takes your date of birth
(only the one you give; he asks before saving the 1st for a month and year), what you save and
what you'd spend in retirement from what you tell him, enters them for you and runs the
[Outlook](../features/retirement-planning.md) — you don't fill the screen in. He never saves a
figure you didn't state.

On the budget path he starts from the envelopes your book already has: your word for one ("Food")
renames the matching starter envelope (Groceries) instead of adding a second, he creates only what's
missing, and he offers once to clear the starters you never mentioned. He then proposes amounts from
the numbers you gave him, from the cash you have today rather than pay that hasn't arrived, and
assigns them once you agree — he only calls the budget set up when money is assigned.

It's a conversation, not a form: ask him something else at any point and he answers that first.
Anything Lucius would change in your book shows as a card you approve first — see
[Confirm cards](../lucius/confirm-cards.md).

Prefer to do it yourself? Close the chat and follow
[Adding your first account and transactions](your-first-account-and-transactions.md). Both paths
write to the same book.

## Every screen introduces itself

The first time you open a main screen — Accounts, Assets, Investments, Plan (Budget and
Outlook), Intel, Commitments, Automations, Skills, Connectors — it shows a short introduction
with two buttons. The Dashboard and Plan's Cash Flow have none: they already show what they are
for.

- **On an empty screen:** **Set up myself** points at the control that adds the first thing,
  unless it already leads the screen;
  **Set up with Lucius** starts a chat that sets it up with you.
- **On a screen that already has data** (for example, accounts Lucius added during setup):
  **Got it**, or **Walk me through it**, where Lucius explains what you're looking at.

Each introduction shows once per person, on any device, and never over a window you already
have open, such as a statement import: it waits until you close it. On Skills the buttons are **Write one
myself** and **Make one with Lucius**. Plan's Outlook asks for your birth date, monthly saving and retirement spending first if it
doesn't have them yet; answering them is its introduction.

Lucius has a short guide of his own — mentions (@), skills (/) and Documents. It runs the first time you start a new chat with something already in the book, or
when you say yes at the end of setup.

## Docker

Under Docker you **must** set a password before the first start — Compose refuses to start
without `AUTH_PASSWORD`:

```bash
AUTH_PASSWORD='pick a password' docker compose up -d
```

Why: on a normal install, the app trusts requests from its own machine. Docker's networking makes
every request — even your own browser on the same machine — look like it comes from another
device. Without a password, nothing would ever count as you: the app would look fine, then every
action would fail.

`AUTH_PASSWORD` sets the password only while none is stored yet. So the first screen you see is a
normal sign-in; sign in with that password and setup continues as above (Step 3 just says a
password is already set). To make it stick across restarts, put `AUTH_PASSWORD=...` in a `.env`
file next to `docker-compose.yml`. A plain `docker run` has no such check — pass
`-e AUTH_PASSWORD=...` yourself.

**Already running without a password** (an older Compose file, or `docker run` without `-e`)?
You're not locked out. Set one from inside the container:

```bash
docker compose exec app python3 -m backend.cli reset-password
```

Type the new password twice (at least 8 characters), reload the app, and sign in.

The published port is bound to your own machine only (`127.0.0.1:8080:8080` in
`docker-compose.yml`), so none of this is reachable from your network unless you widen that
binding on purpose — the comment next to it explains the risk.

## Starting over

**Settings → Data → Reset instance…** erases the book and reopens first run from the
beginning. A reset keeps your password and sign-in settings, so Step 3 just shows **Finish**.
Back up first — see [Backup and restore](../operations/backup-and-restore.md).

## Troubleshooting

- **"Could not load the list of model providers."** — the page couldn't reach the app's own
  server. Click **Try again**; if it keeps failing, check the server is running.
- **"That key was rejected by …"** — the provider refused the key. Copy it again from the
  provider's site, with no spaces at either end. See
  [Connecting an AI provider § Troubleshooting](connecting-an-ai-provider.md#troubleshooting).
- **Claude Code or Codex stays "Not installed"** — it must be installed on the computer running
  Open Net Worth, not the one showing the browser. Open a new terminal after installing so it's
  on your `PATH`.
- **"Those two passwords do not match."** — retype the **Confirm** field.
- **Opening it from another device shows "Set a password first"** — no password is set yet. Set
  one in **Settings → Security** on the computer running the app, then click
  **I've set it, sign in**.

## Related

- [Installing Open Net Worth](installation.md)
- [Connecting an AI provider](connecting-an-ai-provider.md)
- [Adding your first account and transactions](your-first-account-and-transactions.md)
- [I lost my password](../troubleshooting/lost-password.md)

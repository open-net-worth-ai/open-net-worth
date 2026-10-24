---
title: Email
description: Connect your mailbox so Lucius can log bank, card and trade emails — step-by-step setup for Gmail, Outlook, iCloud, Fastmail or any IMAP server.
category: connectors
order: 20
audience: [user]
status: stable
related: [lucius/automations, lucius/permissions, connectors/telegram, operations/security-and-privacy]
keywords: [email, imap, smtp, gmail, outlook, icloud, fastmail, app password, receipts, statements, auto-log, setup]
---

# Email

Connect your mailbox and Lucius can read the emails your bank, card issuer and broker already send
you — purchase alerts, transfer notices, trade confirmations — and turn them into transactions.
It can also send email from the same address when you ask it to.

Setup takes about five minutes. You need two things: your email address and an **app password**
from your email provider.

## 1. Get an app password

An app password is a separate password your provider generates for one app. Use it instead of
your normal password — most providers refuse a normal password here once two-step verification
is on, and you can revoke an app password at any time without changing your real one.

| Provider | Where to create one |
|---|---|
| **Gmail** | Turn on 2-Step Verification in your Google Account, then go to **Google Account → Security → App passwords** (or search "App passwords" in your Google Account). Name it "Open Net Worth" and copy the 16-character code. |
| **iCloud** | [appleid.apple.com](https://appleid.apple.com) → **Sign-In and Security → App-Specific Passwords** → generate one. |
| **Fastmail** | **Settings → Privacy & Security → Manage app passwords** → create one with access to *Mail (IMAP/POP/SMTP)*. |
| **Outlook / Hotmail** | **Microsoft account → Security → Advanced security options → App passwords** (only shown once two-step verification is on). See the note under [Troubleshooting](#troubleshooting). |
| **Other** | Look for "app password", "app-specific password" or "IMAP access" in your provider's security settings. |

Copy it somewhere safe for a minute — most providers only show it once.

## 2. Add the Email connector

1. Open **Connectors** in the sidebar, click **Browse & add**, choose **Data sources**, click
   **Email**, then **Add connector**.
2. Under **Provider**, click your provider's chip — **Gmail**, **Outlook**, **iCloud** or
   **Fastmail**. That fills in all the server settings for you.
   Using another provider or your own mail server? Click **Custom** and enter its IMAP host and
   port (usually `993`) and SMTP host and port (usually `587`).
3. **Email / username** — your full email address.
4. **App password** — paste the password from step 1.
5. **Mail folder** — leave it as `INBOX` to let Lucius see all your mail, or see
   [Limit what Lucius can see](#limit-what-lucius-can-see) below.
6. Click **Save**.

The same address and password are used for both reading (IMAP) and sending (SMTP) — you never
enter them twice.

## 3. Check that it works

Ask Lucius something like:

> *Search my email from the last 7 days for card or bank notifications.*

If it lists messages, you're connected. If it reports an error, the same error appears as
**Last error** in the Email connector's settings (**⋯ → Edit configuration**) — see [Troubleshooting](#troubleshooting).

## 4. Log transactions automatically (optional)

On the Email connector's page, click **Automate** (or **Automate with a template** inside its
settings). That opens the **Auto-log transactions from email** Automation as a draft — click
**Create automation** to turn it on. Once on: every 12 hours, Lucius reads new bank, card and
trade confirmation emails, logs them with the payee resolved, and messages you a summary of what
it logged. Each run reads the mail since the last run that finished, so changing the schedule
never skips any.

Running it twice over the same email never books it twice — each transaction remembers which
email it came from. Details, and how to change the schedule or approval mode, are in
[Automations](../lucius/automations.md#logging-transactions-from-your-email).

You don't need the Automation to use email — you can always just ask: *"log the purchases from
my card emails this week."*

## Limit what Lucius can see

With `INBOX`, Lucius can search all of your incoming mail (it only reads the messages it decides
are relevant, but it can see any of them). If you'd rather it see only financial mail:

1. In your email app, create a folder or label — for example `Finance`.
2. Add a filter that moves (or, in Gmail, labels) emails from your bank, card issuer and broker
   into it.
3. Set **Mail folder** on the Email connector to that name, exactly as spelled — `Finance`.

Lucius then reads only that folder.

## What Lucius can and can't do with your mailbox

- **Reading never changes anything.** The mailbox is opened read-only: nothing is marked read,
  moved, flagged or deleted.
- **It looks before it downloads.** A search fetches only sender, subject and date; Lucius then
  opens just the messages that matter.
- **Attachments are never opened**, only the text of the email, and a very long email is cut
  short before it reaches the AI model.
- **Nothing runs in the background** unless you set up an Automation. Without one, your mail is
  only read when you ask.
- **Sending only happens when you ask for it — but check your approval setting.** In a
  conversation set to **Manually approve**, Lucius shows you the recipient and message and waits
  for your approval. In **Automatically approve** (what a new conversation starts in) it sends
  without asking. To be asked every time, wherever you are, open the Email connector's **Tool
  permissions** and set the send tool to **Ask** (it starts there) and keep conversations on **Manually approve** — see
  [Permissions](../lucius/permissions.md).
- **Email content is treated as untrusted.** An email can't instruct Lucius to do anything; it's
  read as data only.

## Where your password is stored

On this machine only. It's never sent to Lucius or the AI model and never included in an export,
and it's left out of a sanitised backup like every other connector credential (see [Backup and
restore](../operations/backup-and-restore.md)). The field hides it by default; you can reveal it
to check what's saved, and that reveal is logged.

To stop using email, **Pause** or **Remove** the connector from its page — and revoke the app
password at your provider if you no longer need it.

## Troubleshooting

**"Authentication failed" / "Invalid credentials"**
- You used your normal password — create an app password (step 1) and paste that instead.
- The app password was pasted with a space or line break at the end; paste it again.
- **Gmail:** if your Gmail settings still show **Forwarding and POP/IMAP → IMAP access**, make sure
  it's enabled.
- **Outlook / Hotmail:** Microsoft is phasing out password sign-in for third-party mail apps. If
  an app password is refused, your account may only allow Microsoft's own sign-in, which this
  connector doesn't support yet. Work and school Microsoft 365 accounts often have IMAP turned
  off by the administrator.

**"Folder not found"** — the name must match exactly, including capitals. Gmail labels use the
label's name (`Finance`); nested labels and folders use a `/` (`Banking/Cards`).

**Lucius finds nothing** — check the folder is right (a filtered folder only has mail from the
day the filter was created onwards), and give it a date range: *"from the last 30 days."*

**Custom server won't connect** — IMAP needs a secure connection (normally port `993`); SMTP uses
`587` (STARTTLS) or `465`. Servers that only accept unencrypted connections aren't supported.

## Related pages

- [Automations](../lucius/automations.md) — the auto-log template and scheduling
- [Permissions](../lucius/permissions.md) — when Lucius asks before sending
- [Telegram](telegram.md) — get Lucius's summaries on your phone
- [Security and privacy](../operations/security-and-privacy.md) — where every credential lives

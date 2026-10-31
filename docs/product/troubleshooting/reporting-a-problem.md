---
title: Reporting a problem
description: Send us a problem report in a few clicks — where to find it, what it contains, how logs are masked, and what to do if it can't be sent.
category: troubleshooting
order: 90
audience: [user, operator]
status: stable
related: [operations/security-and-privacy, operations/logs, operations/desktop, troubleshooting/backend-wont-start]
keywords: [report a problem, bug report, feedback, support, logs, privacy, redaction, error, send feedback]
---

# Reporting a problem

Something broken or confusing? Send a report straight from the app. Open Net Worth never sends
anything about itself on its own — a report goes only when **you** open it, read it, and click
**Send report**. Closing the window sends nothing.

To close it without sending, press **Cancel**, the **×** in its corner or **Esc**. In the desktop
app the report opens over the main window: clicking outside it, or choosing another menu command,
closes it too.

## Send a report

1. Open the report window — any of these works:
   - **Next to an error:** click **Report this problem**. The error is filled in for you. It
     appears on the "Something went wrong" panel (beside **Try again** and **Reload app**), the
     "Can't reach Open Net Worth" screen, a failed restore or import, a failed AI provider
     connection test, and the desktop app's error pages.
   - **Sidebar:** **Send feedback** (the bug icon at the bottom, below Search and Hide amounts).
   - **Settings → Help → Send feedback…**
   - **On a phone:** **More → Send feedback**.
   - **Desktop app:** **Help → Report a Problem…** — works on every screen, even when no book is
     open or the app couldn't start.
2. Under **What happened?**, describe what you did and what went wrong. This is the only
   required field.
3. Optionally add your **Email**. We only use it to tell you when the fix is out.
4. Leave **Include recent logs** ticked unless you'd rather not share them. Read the log lines
   in the box below it — you can edit or delete any of them. With no logs yet, the box is greyed
   out and there are no lines to show. **Show exactly what is sent**
   displays the final report exactly as it will be posted.
5. Click **Send report**.

When it's sent you'll see **Thank you. Your report was sent.** and a **Reference** — quote it if
you write to us about the same problem. We aim to investigate and fix reports within 48 hours.

Prefer to talk it through? The dialog also links to our [Discord](https://discord.gg/gRRsQRgwE8).

## What a report contains

Everything is shown to you before it's sent:

| Field | What it is |
|---|---|
| What happened? | Your description. |
| Email | Optional — only used to tell you when the fix is out. |
| Error | Only when you started from an error: the message it showed. You can edit it. |
| Version, Platform, Mode, Screen | The app version, your operating system, whether you're using the desktop app, a browser on the same machine, or a server, and which screen you were on (such as `/accounts`). |
| Recent logs | Up to the last 200 log lines, only if **Include recent logs** is ticked. |

A report never includes your database, accounts, balances, attachments or documents.

## How the logs are masked

Before you see them, log lines are scrubbed of:

- **Money:** amounts with a currency symbol or code (`$1,234.56`, `50 EUR`), amounts after
  words like `balance=` or `price:`, and numbers with thousands separators or cents.
- **Names:** your account and payee names.
- **Folders:** your home folder and book folder become `~` and `<book>`.
- **Contact details and secrets:** email addresses, API keys and tokens, `Bearer` headers, and
  passwords or query strings inside web addresses.
- **Long free text:** a log entry spanning several lines (such as an AI model's reply) is
  dropped, except error tracebacks.

Masking works by patterns, so it can miss something. **Read the logs before you send**, and
remove anything you'd rather keep private — or untick **Include recent logs**.

## If it can't be sent

If you're offline or the report server refuses it, the window keeps everything you wrote and
says why:

| Message | What to do |
|---|---|
| "Couldn't reach the report server. Check your connection." | Check your internet connection and try again, or use the buttons below. |
| "Too many reports from this connection. Try again in a while." | Wait, then try again. |
| "The report server refused this report as too large or malformed. Shorten it and try again." | Remove some log lines or text, then send again. |

Two more buttons appear that work without a connection:

- **Copy report** — copies the whole report as plain text, to paste anywhere.
- **Email instead** — opens your mail app with the start of the report addressed to
  `support@opennetworth.ai`, and puts the complete report (logs included) on your clipboard to
  paste in.

If the app's own server isn't answering (the "Can't reach Open Net Worth" screen), the browser
can't fetch or mask the logs. The window says "Can't reach the server, so nothing was masked",
fills in what it can, and offers only **Copy report** and **Email instead**. Read it before you
send.

## Where reports go

To `https://cloud.opennetworth.ai/api/problem-reports`, run by the Open Net Worth project. Reports
are stored and forwarded to the maintainers by email. You don't need an account. Only a one-way
hash of your IP address is kept, to limit abuse — never the address itself.

Running your own support portal? Set `ONW_REPORTS_URL` to send reports there instead — see
[Environment variables](../reference/environment-variables.md#optional-features).

## Related

- [Security and privacy](../operations/security-and-privacy.md) — everything that can leave your
  machine
- [Logs](../operations/logs.md) — what the log holds and how to open it yourself
- [The app won't start](backend-wont-start.md)

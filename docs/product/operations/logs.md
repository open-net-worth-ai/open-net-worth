---
title: Logs
description: Where to find the app's log for each install, how to read it or let Lucius read it, and what's deliberately kept out of it.
category: operations
order: 80
audience: [operator]
status: stable
related: [operations/performance, operations/security-and-privacy, troubleshooting/reporting-a-problem]
keywords: [logs, app.log, logging, debugging, rotation, privacy, open logs folder, journalctl, docker logs]
---

# Logs

When something goes wrong — the app won't start, a sync fails, a screen is slow — the log usually
says why. It's one plain-text file, `app.log`, next to your data. It never contains your
passwords, keys or tokens.

## Find the log

| Install | Where |
|---|---|
| **Desktop app** | **Help → Troubleshooting → Open Logs Folder**. That's the `logs` folder inside your book folder (by default `~/.open-net-worth/logs`). |
| **Server** (`onw start`, source checkout) | `~/.open-net-worth/logs/app.log` (or `<ONW_ROOT>/logs/app.log` if you set `ONW_ROOT`). The same lines also print in the terminal running the app. |
| **Boot service** (`onw autostart on`) | `~/.open-net-worth/logs/app.log`; on Linux also `journalctl --user -u open-net-worth`. |
| **Docker** | `docker compose logs app` for what the container printed; the file is `/home/onw/.open-net-worth/logs/app.log` in the `open-net-worth-data` volume (`docker compose exec app tail -n 200 /home/onw/.open-net-worth/logs/app.log`). |

When the app is a desktop window onto a server, **Open Logs Folder** shows the desktop app's own
logs; the book's log is on the server.

## Read it

- **Ask Lucius:** *"Show me the recent errors in the log."* He can read the most recent entries
  (up to 500 lines, filtered by level or a search word) with secrets masked. This works in chat,
  not in scheduled Automations.
- **Open the file** in any text editor, or follow it live: `tail -f ~/.open-net-worth/logs/app.log`.
- **Send it with a problem report:** tick **Include recent logs** in
  [Report a problem](../troubleshooting/reporting-a-problem.md) to attach the last 200 lines,
  masked.

## What's in it

- What the app is doing and what failed, with the reason.
- One line per request with its timing, for example
  `PERF-REQ GET /api/commitments total=850ms db=120ms(14q) compute=730ms` — see
  [Performance](performance.md).

## What's kept out

Credentials — passwords, API keys, tokens — are never written. For anything involving AI
(attachments, images, documents Lucius reads), the log records *that* it happened — an id, a size,
which model answered — never the content itself.

## Size and access

The file rotates at 5 MB and keeps three older copies (`app.log.1` … `app.log.3`), so it never
uses more than about 20 MB. The folder and file are readable only by the account that runs the
app.

## Related pages

- [Performance](performance.md) — reading the timing lines
- [Backend won't start](../troubleshooting/backend-wont-start.md) — the log is the first place to
  look
- [Reporting a problem](../troubleshooting/reporting-a-problem.md)

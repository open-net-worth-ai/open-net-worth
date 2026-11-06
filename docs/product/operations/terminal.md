---
title: Terminal
description: A shell in your browser on the machine Open Net Worth runs on — how to switch it on when you host it yourself, and sign in to Claude Code or Codex there.
category: operations
order: 55
audience: [user, operator]
status: stable
related: [operations/exposing-beyond-localhost, getting-started/connecting-an-ai-provider, operations/security-and-privacy]
keywords: [terminal, shell, bash, claude code, codex, login, subscription, private cloud, ONW_TERMINAL_ENABLED, device auth]
---

# Terminal

**Settings → Terminal** opens a command-line shell (`bash`) on the machine Open Net Worth runs on,
right in your browser. Its main use is signing in to a **Claude Code** or **Codex** subscription
on that machine, so Lucius can use it as his AI provider — without needing SSH access to the
server.

> This is your server. Tools you sign in to here are used under your own agreement with their
> provider.

## Where it's available

- **Private Cloud:** always on for the signed-in owner.
- **Self-hosted (`onw`, Docker, source):** off until you switch it on — see below.
- **Windows:** not available.
- **Desktop app on its own book:** off — on your own computer, just use your normal terminal app.
  (Connected to a Private Cloud server, the desktop app shows that server's terminal.)

## Switch it on (self-hosted)

A shell can do anything the account running Open Net Worth can do, including reading your book's
files and saved credentials. Read [Safeguards](#safeguards) first.

1. **Make sure the app has a password** (**Settings → Security**). The terminal is never offered
   without one.
2. **Start the app with `ONW_TERMINAL_ENABLED=true`**, for example:

   ```bash
   ONW_TERMINAL_ENABLED=true onw start
   ```

   Docker: add `ONW_TERMINAL_ENABLED: "true"` under `environment:` in `docker-compose.yml`, then
   `docker compose up -d`.
3. **Sign in with your password in the browser.** The terminal only opens for a real signed-in
   session — never for password-free local access or the API token. If this computer skips the
   password, turn off **Don't ask for the password on this computer** in **Settings → Security**,
   then sign in.
4. Open **Settings → Terminal** and click **Open terminal**.

## Sign in to Claude Code or Codex from it

1. Open **Settings → Terminal → Open terminal**.
2. Run the provider's sign-in:
   - **Claude Code:** `claude`, then follow its sign-in prompts.
   - **Codex:** `codex login --device-auth` — the device-code sign-in. Codex's normal browser
     sign-in tries to return to the server's own `127.0.0.1`, which your browser can't reach.
3. Open the link it prints on your own computer or phone, approve, and come back.
4. In **Settings → AI**, choose that provider — see
   [Connecting an AI provider](../getting-started/connecting-an-ai-provider.md).

The sign-in is saved in the home folder of the account running Open Net Worth and stays on that
server. Under Docker, that folder only survives an update with the volumes in the current
`docker-compose.yml` — see
[Subscription under Docker](../getting-started/connecting-an-ai-provider.md#subscription-under-docker).

## How it behaves

- The shell runs as the app's user, starts in that user's home folder, and gets only a basic
  environment (`PATH`, `HOME`, locale, terminal, proxy and certificate settings) — none of the
  app's own settings, passwords or AI keys.
- **One terminal at a time.** Opening a second says "Another terminal is already open. Close it
  first."
- **Closes after 15 minutes without input** ("Closed after 15 minutes without activity."). Output
  from a running command doesn't count as activity.
- Closing the tab or the terminal ends the shell and everything it started. Processes you
  deliberately detach (`nohup`, `disown`, `tmux`) keep running.
- If the app restarts or the connection drops, the terminal ends; open a new one.
- Every time one opens, **"Terminal opened"** is recorded in the app's run history and log.

## Safeguards

- **It can read your data.** The shell is you, on your server: it can read the book's database,
  connector credentials and stored API keys. That's why only the signed-in owner gets it. What it
  can't read is the running app itself — on Linux the app blocks other processes from inspecting
  its memory and drops its sign-in secrets from its environment once loaded.
- **Recent sign-in (Private Cloud):** the terminal asks for a sign-in from the last 15 minutes.
  An older session sees "For your security the terminal asks for a recent sign-in." with **Sign
  in again**, and returns to the terminal afterwards. In the desktop app the sign-in opens in your
  browser (the app waits on "Continue in your browser"), then picks up the new session.
- **Only from the app's own page:** another website can't open or drive your shell.
- **Never through the API token:** phone Shortcuts, the CLI and other AI apps never get a shell.

## Troubleshooting

**There's no Terminal in Settings** — on a self-hosted server it only appears once it's switched
on *and* you're signed in with your password (steps above).

**"The terminal is switched off on this server."** — `ONW_TERMINAL_ENABLED=true` isn't set, or
the app wasn't restarted after setting it.

**"Sign in to use the terminal."** — you're using password-free local access or have no password
set. See step 3 above.

**"The terminal is not available on this operating system."** — the server runs on Windows.

## Related pages

- [Connecting an AI provider](../getting-started/connecting-an-ai-provider.md)
- [Exposing this beyond localhost](exposing-beyond-localhost.md)
- [Security and privacy](security-and-privacy.md)

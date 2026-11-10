<p align="center">
  <img src="docs-site/scripts/brand-source/logo.svg" alt="Open Net Worth" width="96">
</p>

<h1 align="center">Open Net Worth</h1>

<p align="center"><b>Agent harness for personal finance</b></p>

<p align="center">
  <a href="https://opennetworth.ai">Website</a> ·
  <a href="https://opennetworth.ai/download">Download</a> ·
  <a href="https://docs.opennetworth.ai">Docs</a> ·
  <a href="https://demo.opennetworth.ai">Live demo</a> ·
  <a href="https://discord.gg/gRRsQRgwE8">Discord</a>
</p>

<p align="center">
  <a href="https://demo.opennetworth.ai"><img src="https://img.shields.io/badge/Try_the_live_demo-c9a15b?style=for-the-badge" alt="Try the live demo" height="36"></a>
</p>

<p align="center"><sub>If this is useful, a star helps others find it.</sub></p>

<p align="center">
  <a href="https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest"><img src="https://img.shields.io/github/v/release/open-net-worth-ai/open-net-worth-releases?label=release" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License: AGPL-3.0"></a>
  <a href="https://docs.opennetworth.ai"><img src="https://img.shields.io/badge/docs-docs.opennetworth.ai-informational" alt="Documentation"></a>
  <a href="https://discord.gg/gRRsQRgwE8"><img src="https://img.shields.io/badge/Discord-join-5865F2?logo=discord&logoColor=white" alt="Discord"></a>
</p>

<p align="center">
  <a href="https://opennetworth.ai/#demo">
    <img src="https://opennetworth.ai/video/opennetworth-demo-poster.jpg" alt="Watch the three-minute product demo" width="820">
  </a>
  <br>
  <sub><a href="https://opennetworth.ai/#demo">▶ Watch</a></sub>
</p>

**An open-source agent harness for personal finance.** A real multi-currency ledger, budgets, and
investments — plus an AI that can actually read and change them, not just talk about them.
Self-hosted, on a machine you control.

Most personal-finance apps that added AI let it *describe* your data. Open Net Worth is built the
other way round: the ledger, the budget, and the investment book are the substrate, and the
assistant (Lucius) is an agent operating them through a registry of real tools — the same guarded
write paths the UI uses.

|  | Chatbot bolted onto an app | An agent harness |
|---|---|---|
| What the AI touches | a summary of your data | the ledger itself, through real write tools |
| When it's wrong | you find out later | a confirm card shows the change before it lands |
| What it can do | answer | act, on a schedule, unattended if you allow it |
| Extending it | wait for the vendor | connect an MCP server and it gains tools |

Every tool carries a permission level — `always_allow`, `needs_approval` (the default), or
`blocked` — enforced at dispatch for every tool in the app.

Nothing leaves your machine except the calls you explicitly configure (an AI provider, a price
feed, an optional connector), and there is no telemetry.
The one exception is a problem report you choose to send: you read it first, and it goes only when
you press Send.

## Compared with Claude and ChatGPT Personal Finance

|  | Claude Personal Finance | ChatGPT Personal Finance | Open Net Worth |
|---|---|---|---|
| **Can it act?** | No, it reads your accounts | No, it can't change your accounts | Yes: books transactions, runs your budget, places trades through your broker (approval by default) |
| **Comes to you first** | No, it waits for you to ask | No, it waits for you to ask | Daily briefings, weekly and monthly reports, Telegram pings when news hits what you hold |
| **Investment advice** | Not meant to give it | Planning questions | Judges a ticker against your own investment thesis |
| **Where it works** | US only, through Plaid (up to 16 accounts) | US only, through Plaid | Any country and currency, from your bank emails and statements |
| **Where your data lives** | Anthropic's cloud | OpenAI's cloud | Your own machine |
| **Your AI** | Claude only | ChatGPT only | Your Claude Code or Codex subscription, an API key, or a local model |
| **What it covers** | Spending, bills, balances | Spending, cash flow, investments, loans | Everything you own, including property, cars and RSUs |
| **Who can get it** | Paid US plans, in beta | US ChatGPT Pro, in preview | Anyone: free and open source (AGPL-3.0) |

<sub>Based on each launch's announcement and coverage as of November 2026.</sub>

## What's inside

| | |
|---|---|
| **Ledger** | Every account you own or owe, in any currency: banks, cards, loans, investments, retirement. Transfers, splits, reconcile, and statement import with a review step before anything books. |
| **Budget** | Zero-based envelopes (Ready to Assign, targets, credit card payments) or a simple 50/30/20 split, plus a cash flow projection. |
| **Investments** | Holdings, buys, sales, dividends, lots and realized gains, options, RSU and other equity grants, watchlists and price alerts. |
| **Planning** | Big dated commitments and how each is funded, and a retirement outlook: the odds your money lasts and the age you could retire. |
| **Lucius** | The AI agent on every screen. He works your real book through real tools: logs and sorts transactions, reconciles accounts, builds reports. Connect an MCP server and he acts in other apps too, like placing a trade with your broker. You choose what he does on his own and what he asks you first. |
| **Automations** | Jobs Lucius runs on a schedule or when something happens: a daily briefing, a weekly report, a bank sync, logging purchases from email. |
| **Skills and memory** | Saved playbooks he follows for a job (built in, or your own), and facts he remembers about you between conversations. |
| **Connectors** | Bank and broker sync (SimpleFIN, Plaid, Enable Banking, Lunch Flow, Alpaca, Interactive Brokers), YNAB, Lunch Money, your mailbox, Telegram, web search, and any MCP server. |
| **Your AI** | An API key (Anthropic, OpenAI or any OpenAI-compatible endpoint, including a local model), or the Claude Code, Codex or Antigravity sign-in you already have. Other AI apps can use your book too, through `onw mcp`. |

## Is this for you?

**Yes, if** you want to self-host your own finances, you're comfortable running a small app on
your own machine or a server you control, and you'd rather have an AI agent *do* the work
(categorize a transaction, reconcile an account, explain a swing in net worth, act in the apps you
connect) than just chat about screenshots of it.

**Not yet, if** you need a polished, install-and-forget consumer product, multi-user or family
accounts, or mobile apps.

## Requirements

- **The desktop app:** macOS on Apple Silicon, Windows 10/11 (x64), or Linux (x86_64, AppImage
  or `.deb`). It bundles its own Python, so nothing else needs installing. Intel Macs and arm64
  Linux have no desktop build yet; what differs on Windows is listed in
  [`docs/WINDOWS.md`](docs/WINDOWS.md).
- **Running a server from source:** Python 3.12+ and Node.js 22+ (macOS, Linux or Windows).
- **An AI model** — first run asks for one, with no skip: an API key (Anthropic, OpenAI, or any
  OpenAI-compatible endpoint), an existing Claude Code, Codex or Antigravity CLI sign-in on the
  same machine (detected for you), or a free local model through Ollama.

## Getting started

### Option A — the desktop app (recommended)

Download it from [opennetworth.ai](https://opennetworth.ai), or from the public
[releases page](https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest):

| Platform | Download | Terminal install |
|---|---|---|
| macOS (Apple Silicon; signed and notarized DMG) | [opennetworth.ai/download/mac](https://opennetworth.ai/download/mac) | `curl -fsSL https://opennetworth.ai/install.sh \| sh` |
| Windows 10/11 x64 (installer) | [opennetworth.ai/download/windows](https://opennetworth.ai/download/windows) | `irm https://opennetworth.ai/install.ps1 \| iex` (PowerShell) |
| Linux x86_64 (AppImage, or `.deb` from the releases page) | [opennetworth.ai/download/linux](https://opennetworth.ai/download/linux) | `curl -fsSL https://opennetworth.ai/install.sh \| sh` |

The Windows installer is **not code-signed yet**, so SmartScreen may show a warning: choose
**More info → Run anyway**. `install.ps1` verifies the download against the release's
`SHA256SUMS`; add `-RequireSigned` to refuse an unsigned installer. `install.sh` verifies the same
checksum before it installs anything. The macOS and Windows apps update themselves in place;
a Linux AppImage replaces itself after checking its SHA-256, and a `.deb` points you to the
releases page.

Then follow the first-run screen: it asks where your book lives (a local folder, or an existing
server), creates your login, picks a base currency, and hands off to Lucius to add your first
account. Your data defaults to `~/.open-net-worth/`, outside the app, so updating or reinstalling
never touches it. See the
[desktop guide](docs/product/operations/desktop.md) for background mode, start at login, watched
folders and keyboard shortcuts.

### Option B — a server

**1. Install it.** You don't need Python or Node.js on your system. This is for macOS and Linux;
on Windows, follow [these steps](docs/product/getting-started/installation.md#windows) instead.

```bash
curl -fsSL https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest/download/install-server.sh | sh
```

If it says `onw` was added to your `PATH`, run the `source` line it shows (for zsh,
`source ~/.zshenv`) or open a new terminal. It finishes by listing the commands you're likely to
need next.

**2. Start it.** Your browser opens the app, and first run takes it from there.

```bash
onw start --open
```

**3. Optional: keep it running and up to date.** Press <kbd>Ctrl</kbd>+<kbd>C</kbd> to stop the
copy from step 2, then run the command below. It starts the app at login, restarts it after a
crash and installs each new signed release. Leave out `--auto-upgrade` to update by hand. See
[Keeping it running](#keeping-it-running).

```bash
onw start --autostart --auto-upgrade
```

Other commands you may need ([full list](docs/product/getting-started/installation.md#the-onw-command)):

| Command | What it does |
|---|---|
| `onw start --background` | Run it without keeping this terminal, until `onw stop` or a reboot |
| `onw stop` | Stop it |
| `onw restart` | Restart it, the way it was running |
| `onw status` | Show whether it's running, its URL, version and folder |
| `onw logs` | Show the end of its log. `-f` keeps following it |
| `onw upgrade` | Update to the newest release |
| `onw reset-password` | Set a new password if you've forgotten it |
| `onw version` | Print the installed version |

Good to know:

- **Your data** lives in one working folder, `~/.open-net-worth/`, holding `data/`, `cache/`,
  `logs/` and `backups/`. First run lets you put it elsewhere and remembers the choice. No `.env`
  file is needed.
- **The desktop app** can connect to a server like this instead of using a local book.
- **Other AI apps** like Claude Desktop, Cursor or ChatGPT can use your book while the server is
  running. See [Use your book from other AI apps](docs/product/operations/connect-ai-apps.md).
- **Working on the code?** Run `make setup start` from a checkout (Python 3.12+ and Node.js 22+).

### Option C — Docker

**1. Download the Compose file** into an empty folder.

```bash
curl -fsSLO https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest/download/docker-compose.yml
```

**2. Pick a password and start it.** Use at least 8 characters. Compose won't start without a
password.

```bash
AUTH_PASSWORD='pick a password' docker compose up -d
```

In Windows PowerShell, use `curl.exe` in step 1, and run this in step 2:

```powershell
$env:AUTH_PASSWORD='pick a password'; docker compose up -d
```

**3. Open <http://localhost:8080>** and log in with that password.

Good to know:

- **Why the password is required:** your browser reaches the container from Docker's bridge, not
  from localhost, so the "same machine, no login" shortcut never applies. Without a password,
  nothing could operate the instance. Details are in the
  [first-run guide](docs/product/getting-started/first-run.md#docker).
- **Your data** lives in a named volume that survives `docker compose down`. Migrations run at
  boot.
- **The version** is the public image `ghcr.io/open-net-worth-ai/open-net-worth`, pinned by that
  Compose file to the release it came with. `ONW_VERSION=x.y.z` runs another.
- **Working on the code?** From a checkout, add `-f compose.build.yml --build` to build the image
  locally.

## Coming from YNAB or Lunch Money?

Add the **YNAB** or **Lunch Money** connector with a personal access token and your transactions
flow in, refreshed every 6 hours. Categories and budgets are not copied: set them up here, or let
Lucius or an Automation categorize as transactions arrive. The budget works the way YNAB's does
(every dollar assigned, Ready to Assign, envelopes that hold their overspend until month end).
Steps: [Bank and brokerage connectors](https://docs.opennetworth.ai/connectors/bank-brokerage/).

## Keeping it running

The desktop app does this itself: **Settings → Desktop** has *Keep running in the background* and
*Start at login*. For a server, Open Net Worth is one Python process (`onw start`, which runs uvicorn and serves both the API and
the built frontend on the same port) — there is no separate web server (no Apache, no nginx) in
front of it, and none is required.

- **Docker** already restarts itself: `docker-compose.yml`
  sets `restart: unless-stopped`, which covers a crash and a host reboot, as long as the Docker
  daemon itself starts on boot (the default on Docker Desktop and most Linux installs).
- **Not using Docker?** One command installs it as a boot service, so it starts at login/boot and
  restarts on crash, with no terminal window to keep open:

  ```bash
  onw start --autostart
  ```

  (`onw autostart on` is the same thing, and `onw autostart off` turns it off again. From a source checkout, `make autostart` runs setup
  first, then the same thing.) This installs a per-user **launchd** LaunchAgent on macOS, a
  per-user **systemd** unit (plus `loginctl enable-linger`, so it starts even before you log in) on
  Linux, or a per-user **Task Scheduler** task on Windows — no sudo or admin needed on any of them.
  `onw autostart` on its own shows whether it's on; `onw autostart off` stops it and removes the
  service. Logs land in `~/.open-net-worth/logs/app.log`.

  <details>
  <summary>What that installs, if you'd rather see it or do it by hand</summary>

  The service runs the same interpreter `onw` runs on, so it is identical for a uv install and a
  checkout's `.venv`; it also carries your shell's `PATH` (launchd and systemd give a fresh
  process a minimal one) and any of `HOST`, `PORT`, `ONW_ROOT`, `DATA_DIR`, `CACHE_DIR`,
  `LOGS_DIR`, `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`,
  `XDG_DATA_HOME` that were set when you installed it.

  macOS (`~/Library/LaunchAgents/ai.opennetworth.app.plist`):

  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
  <plist version="1.0"><dict>
    <key>Label</key><string>ai.opennetworth.app</string>
    <key>ProgramArguments</key>
    <array>
      <string>/path/to/python3</string>
      <string>-m</string><string>backend.cli</string><string>start</string>
    </array>
    <key>WorkingDirectory</key><string>/Users/you/.open-net-worth</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
  </dict></plist>
  ```

  Linux (`~/.config/systemd/user/open-net-worth.service`):

  ```ini
  [Unit]
  Description=Open Net Worth
  After=network.target

  [Service]
  WorkingDirectory=/home/you/.open-net-worth
  ExecStart=/path/to/python3 -m backend.cli start
  Restart=on-failure

  [Install]
  WantedBy=default.target
  ```

  See `backend/cli/autostart.py` for the exact commands (`launchctl bootstrap` /
  `systemctl --user enable --now`).

  </details>

- **Updating.** The desktop app updates itself (see the
  [desktop guide](docs/product/operations/desktop.md#updates)); re-running its installer also
  works. A server installed with uv: `onw upgrade` by hand, which a running server installs and
  restarts into once it's idle, or start it with `onw start --autostart --auto-upgrade` to do that
  for each new signed release — off unless you ask for it
  ([Upgrading](docs/product/operations/upgrading.md)).
  For Docker, download the new release's `docker-compose.yml` (it pins the image version) or set
  `ONW_VERSION=x.y.z`, then `docker compose up -d` (a checkout: `git pull && make setup`). Your data is outside all of them, and migrations run at the next start.

## Configuration

Everything needed to *run* the app is an optional environment variable — see `.env.example` for
the full list (working folder, host/port, CORS).

Everything needed to make it *useful* is configured **inside the running app** and stored
there: the AI provider and model under **Settings**, and banks, brokers, email, Telegram, web
search and price sources under **Connectors**. You never hand-edit a config file for those.

Set `ONW_ROOT` only to pin the working folder from outside the app: a container, or a second
instance on the same machine (which also needs its own `PORT`).

## Your data

One working folder (default `~/.open-net-worth/`): a single SQLite database plus a small set of
file-backed stores, alongside `cache/`, `logs/` and `backups/`. It never leaves your machine
except for calls you configured yourself.

Settings → Data gives you a one-click full backup `.zip`, automatic daily backups, and
restore-from-backup — a checkpoint is saved first, so a restore is itself undoable.

## Running it beyond localhost

Open Net Worth is built for **one operator on a machine you control**. Treat it like a
self-hosted password manager, not a public web app.

- **The local machine is trusted by default.** With `AUTH_TRUST_LOOPBACK=true` (the default), any
  process on the same machine can call the API without a password — so the person sitting at the
  machine never has to log in. Set it to `false` on a shared machine.
- **A password only gates non-localhost requests.** It is not, by itself, what stands between
  your data and the network.
- **Behind a reverse proxy, loopback trust stops working as intended.** A same-host proxy
  (nginx, Caddy, a tunnel) makes every remote request arrive from `127.0.0.1`, so it is trusted.
  Set `AUTH_TRUST_LOOPBACK=false` before putting this behind one.
- **Before exposing it anywhere** — a LAN, a tunnel, a public domain — set a strong password
  (Settings → Security), turn off loopback trust, and put it behind HTTPS. The app does not
  terminate TLS itself.

Found a vulnerability? See [`SECURITY.md`](SECURITY.md) — please do not open a public issue.

## Documentation

- **[docs.opennetworth.ai](https://docs.opennetworth.ai)** — the full user guide. The same docs
  are built into the app, searchable, under `/help`.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — stores, fields, and how the money math works.
- [`CLAUDE.md`](CLAUDE.md) — the codebase map, if you are reading code rather than using the app.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev setup, the dependency rule, test policy.

## Stack

- **Backend** — Python 3.12, FastAPI, SQLAlchemy + Alembic (SQLite by default, Postgres supported)
- **Frontend** — React 19, TypeScript, Vite
- **AI** — Anthropic Claude by default, or any OpenAI-compatible endpoint; your key
- **Prices** — Yahoo Finance, CoinGecko

## License

AGPL-3.0 — see [`LICENSE`](LICENSE). Third-party dependency licenses are in [`NOTICE`](NOTICE).

---
title: Environment variables
description: Every environment variable an operator might set. All are optional — a fresh install runs with none, except AUTH_PASSWORD under Docker.
category: reference
order: 10
audience: [operator]
status: stable
related: [getting-started/installation, reference/file-layout, reference/ports-and-urls, troubleshooting/lost-password]
keywords: [env, environment variable, config, onw_root, data dir, auth, database url, docker, .env]
---

# Environment variables

**You don't need any of these for a normal install.** `make setup start`, `onw start` and the
desktop app all run with no configuration. The one exception is **Docker**, which requires
`AUTH_PASSWORD` (see [Auth](#auth-instance-password)).

Your AI provider and model, your password, and every connector (email, Telegram, banks, search)
are set up **inside the app** — Settings and the Connectors screen. They are not read from
environment variables. The exceptions are `AUTH_PASSWORD` under Docker and the AI key fallback
described below.

## How to set one

- **Running from a checkout (`make start` / `uvicorn`):** copy `.env.example` to `.env` in the
  repository folder and uncomment the lines you need. The file is read at startup. Restart the
  app after a change.
- **`onw start` (installed with `uv tool`):** set the variable in your shell, e.g.
  `PORT=8081 onw start`. If you use `onw autostart install`, the boot service carries over
  `HOST`, `PORT`, `ONW_ROOT`, `DATA_DIR`, `CACHE_DIR` and `LOGS_DIR` from the shell you ran it
  in — run it again after changing them.
- **Docker:** put `NAME=value` lines in a `.env` file next to `docker-compose.yml`, or pass them
  on the command line: `AUTH_PASSWORD=… docker compose up -d`.
- **Desktop app:** it ignores `.env` and sets up its own server. The only variable meant for
  it is `ONW_DESKTOP_UPDATES=0`, which stops it checking for updates (see
  [Desktop app only](#desktop-app-only)).

## Where things live

| Variable | Default | What it does |
|---|---|---|
| `ONW_ROOT` | `~/.open-net-worth` | **The one to use.** The working folder that holds `data/`, `cache/`, `logs/` and `backups/`. Point it at another drive, a NAS mount, or a second instance's folder. It overrides the four below. |
| `DATA_DIR` | `<ONW_ROOT>/data` | Older per-folder override for the database and your files. Prefer `ONW_ROOT`. |
| `CACHE_DIR` | `<ONW_ROOT>/cache` | Price and rate caches — safe to delete. |
| `LOGS_DIR` | `<ONW_ROOT>/logs` | The app log. |
| `BACKUPS_DIR` | `<ONW_ROOT>/backups` | Daily backups. |
| `ONW_DATABASE_URL` | `sqlite:///<DATA_DIR>/onw.db` | Use Postgres instead of the built-in SQLite file, e.g. `postgresql+psycopg://user:pass@host/db`. See [SQL console and Postgres](../operations/sql-console-postgres.md). |
| `ONW_SQL_CONSOLE_DATABASE_URL` | unset | Postgres only, rarely needed: a database login you made for Lucius's read-only SQL, instead of the one the app creates. The app still keeps its table permissions up to date. See [SQL console and Postgres](../operations/sql-console-postgres.md). |

Two instances can't share one working folder — the second refuses to start (see
[The backend won't start](../troubleshooting/backend-wont-start.md#another-copy-is-already-using-the-folder)).
Give each its own `ONW_ROOT`. The full layout is in [Where your files live](file-layout.md).

## Server

| Variable | Default | What it does |
|---|---|---|
| `HOST` | `127.0.0.1` | The address to listen on. `0.0.0.0` makes the app reachable from other devices on your network — read [Exposing this beyond localhost](../operations/exposing-beyond-localhost.md) first. Docker sets this for you. |
| `PORT` | `8080` | The port the app listens on. |
| `CORS_ORIGINS` | `http://localhost:5174,http://localhost:8080` | Comma-separated web addresses allowed to call the API from a browser. `*` allows any — don't, on anything reachable from a network. |
| `ONW_ALLOWED_HOSTS` | unset | Extra hostnames (comma-separated, no port) you use to reach the app from this machine — for example a name you put in your hosts file. `localhost`, `127.0.0.1`, `[::1]` and the hostnames in `CORS_ORIGINS` always work; any other name gets a 421 `host not allowed` error until you add it here. |

## Auth (instance password)

The password is normally set from the app on first run and changed in **Settings → Security**.

| Variable | Default | What it does |
|---|---|---|
| `AUTH_PASSWORD` | unset | A password to store on first boot, when none is stored yet. **Required for Docker** — Compose refuses to start without it. Ignored once a password is stored, so it is *not* a way to reset one — use [`onw reset-password`](../troubleshooting/lost-password.md). The app logs a reminder to remove it after it has been used. |
| `AUTH_PASSWORD_HASH` | unset | The same, but already hashed (`pbkdf2$…`). Preferred over `AUTH_PASSWORD` in scripts, so the plain password never sits in a file. |
| `AUTH_TRUST_LOOPBACK` | `true` | By default, a browser on the same machine is let in without the login screen. `false` requires the password even there. |
| `AUTH_DISABLED` | `false` | `true` turns the login off entirely. For development only — never on an instance anyone else can reach. |
| `AUTH_API_TOKEN` | generated | The bearer token for scripts and API access. Normally generated and shown in the app. |
| `AUTH_SESSION_KEY` | generated | The key that signs login sessions. Changing it signs everyone out. |
| `AUTH_SESSION_TTL` | `2592000` (30 days) | How long a login lasts, in seconds. |

## Running behind an authenticating proxy (gateway mode)

| Variable | Default | What it does |
|---|---|---|
| `ONW_GATEWAY_TOKEN` | unset | A shared secret, at least 32 characters, for running behind a proxy that does the sign-in for you. Shorter, and the app refuses to start with `ONW_GATEWAY_TOKEN must be at least 32 characters`. |

When it's set, every request must carry the value in an `X-Onw-Gateway-Token` header or it
gets a 403 (only `GET /api/health` is exempt). Setup steps:
[Running behind an authenticating proxy](../operations/exposing-beyond-localhost.md#running-behind-an-authenticating-proxy-gateway-mode).

## Optional features

| Variable | Default | What it does |
|---|---|---|
| `ONW_TERMINAL_ENABLED` | `false` | Turns on **Settings → Terminal**, a shell on this machine in your browser. Needs a password set. Always on behind a gateway. See [Terminal](../operations/terminal.md). |
| `ONW_ALLOW_PRIVATE_ENDPOINTS` | `1` | Lets an AI provider address, webhook or MCP server point at your own machine or local network (a local Ollama or LiteLLM, for example). `0` blocks those. Always blocked in gateway mode. |
| `ONW_REPORTS_URL` | `https://cloud.opennetworth.ai/api/problem-reports` | Where **Report a problem** sends a report, only when you press Send. Must be `https` (plain `http` only for a loopback address). The desktop app reads it too. See [Reporting a problem](../troubleshooting/reporting-a-problem.md). |
| `ONW_TURN_TIMEOUT_S` | `1200` | The default limit, in seconds, on how long one Lucius reply may run. **Settings → AI → Turn time limit** overrides it. |
| `SSL_CERT_FILE` / `SSL_CERT_DIR` | unset | Standard variables for a custom certificate bundle — for a network that inspects HTTPS with its own certificate. |

## AI provider

Choose the provider and model in **Settings → AI Model**; `AI_PROVIDER`, `AI_MODEL` and
`OPENAI_BASE_URL` are not read. Two variables remain:

- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` — used only while no key is saved in Settings (a QA
  copy or a hosted demo supplies its key this way).
- `AI_MEMORY_MODEL` — a cheaper model for background memory extraction.

`ONW_DISABLE_TELEGRAM=1` skips the Telegram connector entirely.

## Desktop app only

| Variable | What it does |
|---|---|
| `ONW_DESKTOP_UPDATES` | `0` stops the desktop app checking `opennetworth.ai` for updates (every 4 hours). Set it in the environment the app is launched from. |
| `ONW_REPORTS_URL` | Also read by the desktop app — see [Optional features](#optional-features). |

## Docker only

| Variable | What it does |
|---|---|
| `ONW_VERSION` | Which published image `docker-compose.yml` runs, e.g. `ONW_VERSION=0.2.1 docker compose up -d`. Defaults to the release the compose file shipped with. |

## Public demo only

For hosting a public demo of made-up data — never on a real book. `DEMO_MODE=true` locks the
instance down and meters AI use per visitor; `DEMO_OPEN_ACCESS=true` (only together with
`DEMO_MODE`) lets visitors in without a password; `DEMO_TRUSTED_PROXY_HOPS` is the number of
proxies in front that add to `X-Forwarded-For` (default `0`). The per-visitor limits are
`DEMO_TURNS_PER_WINDOW` (30), `DEMO_TURN_WINDOW_S` (300), `DEMO_TOKENS_PER_IP_DAILY`
(3,000,000) and `DEMO_TOKENS_TOTAL_DAILY` (30,000,000).

## Not for you to set

- `ONW_QA_MODE`, `ONW_QA_NONCE` and the `ONW_EVAL_*` / `ONW_BUILDER_ROOT` variables are for the
  project's own automated tests. `ONW_QA_MODE=1` makes the app refuse to start against your
  real data folder.
- `ONW_DESKTOP_MODE` and `ONW_DESKTOP_INSTANCE_ID` are set by the desktop app for its own server.
- `ONW_BASE_URL` / `ONW_API_TOKEN` are read only by the `onw mcp` bridge that another AI app
  starts — see [Connect AI apps](../operations/connect-ai-apps.md).
- The `ONW_CLAUDE_CLI_*`, `ONW_CODEX_*`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR`,
  `ONW_FULL_TOOL_OFFER`, `ONW_READ_CACHE` and `ONW_MIGRATE_ASSUME_BACKUP` variables are
  internal tuning and debugging switches. Leave them alone unless a maintainer asks you to set
  one.

## Related

- [Installing Open Net Worth](../getting-started/installation.md)
- [Where your files live](file-layout.md)
- [Ports and URLs](ports-and-urls.md)

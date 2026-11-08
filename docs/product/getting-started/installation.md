---
title: Installing Open Net Worth
description: Install the desktop app on macOS, Windows or Linux, or run it as a server with uv or Docker. No cloud account and no config file needed.
category: getting-started
order: 20
audience: [user, operator]
status: stable
related: [getting-started/what-is-open-net-worth, getting-started/first-run, operations/desktop, operations/upgrading, reference/environment-variables, reference/ports-and-urls]
keywords: [install, setup, requirements, desktop app, download, macOS, Windows, Linux, AppImage, uv, onw, docker, server, autostart]
---

# Installing Open Net Worth

There are three ways to run Open Net Worth. Pick one:

| | Best for | You need |
|---|---|---|
| **[A. Desktop app](#option-a-desktop-app-recommended)** (recommended) | Using it on your own computer | Nothing else — it bundles everything |
| **[B. Server with `onw`](#option-b-run-it-as-a-server)** | An always-on machine you open from a browser or from the desktop app on other devices | A terminal |
| **[C. Docker](#option-c-docker)** | A home server or NAS that already runs Docker | Docker with Compose |

All three keep your data in one folder, `~/.open-net-worth/`, outside the app itself. Updating
or reinstalling the app never touches it. There is no account to create and no `.env` file to
write.

## Supported platforms

| Platform | Desktop app | Server (`onw`) | Docker |
|---|---|---|---|
| **macOS** | Apple Silicon only (signed and notarized) | Yes | Yes |
| **Windows 10/11** | x64 | Yes (see the Windows note) | Yes |
| **Linux** | x86_64, AppImage or `.deb` | Yes | Yes |

Intel Macs and arm64 Linux have no desktop build yet — use the server or Docker there.

## Option A: desktop app (recommended)

The desktop app bundles its own Python, the web interface and this help. You don't install
Python, Node or Docker.

### macOS (Apple Silicon)

1. Go to [opennetworth.ai/download/mac](https://opennetworth.ai/download/mac) and download the DMG.
2. Open it and drag **Open Net Worth** into **Applications**.
3. Open it from Applications.

Or install from Terminal (it checks the download's checksum before installing):

```bash
curl -fsSL https://opennetworth.ai/install.sh | sh
```

### Windows 10/11 (x64)

1. Go to [opennetworth.ai/download/windows](https://opennetworth.ai/download/windows) and run the
   installer. It installs for your user only — no administrator prompt.
2. The installer is **not code-signed yet**, so Windows SmartScreen may warn you. Click
   **More info → Run anyway**.

Or in PowerShell (checks the download against the release's `SHA256SUMS` first):

```powershell
irm https://opennetworth.ai/install.ps1 | iex
```

To refuse any installer without a valid signature, add `-RequireSigned`, or set
`$env:ONW_REQUIRE_SIGNED = '1'` before piping to `iex`.

### Linux (x86_64)

Pick one:

- **Terminal:** `curl -fsSL https://opennetworth.ai/install.sh | sh` — installs the AppImage to
  `~/.local/bin/open-net-worth` with a launcher entry, no `sudo`, after checking its checksum.
- **AppImage:** download it from [opennetworth.ai/download/linux](https://opennetworth.ai/download/linux),
  run `chmod +x` on it, then run it.
- **`.deb`:** download `Open-Net-Worth-linux-amd64.deb` from the
  [releases page](https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest),
  then `sudo apt install ./Open-Net-Worth-linux-amd64.deb`.

### After installing

On first launch, click **Get started**, then choose where your book lives (**Continue** keeps
the suggested folder; **On your own server** connects to a server you already run). Then follow
[Your first run](first-run.md).

To upgrade, the app updates itself (see [Desktop app § Updates](../operations/desktop.md#updates)),
or re-run the terminal installer. To remove the app on macOS or Linux:
`curl -fsSL https://opennetworth.ai/install.sh | sh -s -- --uninstall`. Your book is never
removed.

## Option B: run it as a server

The server is the same app as one process that serves both the API and the web interface on
port 8080. You open it in a browser, or connect the desktop app to it from another computer.

### macOS and Linux

1. Open a terminal and run the installer:

   ```bash
   curl -fsSL https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest/download/install-server.sh | sh
   ```

   It installs [uv](https://docs.astral.sh/uv/) if you don't have it, then installs
   `open-net-worth` on uv's own Python 3.12. You need no system Python or Node.js. The first
   install downloads a few hundred MB. It also adds `~/.local/bin` to your shell's `PATH` if it
   isn't there yet. The terminal you ran it in can't see that change, so the installer then shows
   a line to reload it, `exec "$SHELL" -l`, or you can open a new terminal. It finishes by listing
   the commands you're likely to need next.
2. Start it and open it in your browser:

   ```bash
   onw start --open
   ```

3. Your browser opens `http://127.0.0.1:8080`. Follow [Your first run](first-run.md).

Add `--autostart` to the end of the installer command (`… | sh -s -- --autostart`) to also start
it at login and restart it if it crashes. Add `--auto-upgrade` instead to do that and also install
new versions automatically — see [Upgrading](../operations/upgrading.md).

### Windows

The `install-server.sh` script is for macOS and Linux. On Windows, in PowerShell:

1. Install uv: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. Open a new PowerShell window and run `uv tool install --python 3.12 open-net-worth`
3. Run `onw start --open`

### The `onw` command

| Command | What it does |
|---|---|
| `onw start` | Run the app. `--open` opens your browser once it's up; `--port 9000` or `--host 0.0.0.0` override the defaults (`127.0.0.1:8080`). It stays in that terminal until you press <kbd>Ctrl</kbd>+<kbd>C</kbd>. `--background` frees the terminal and keeps it running until `onw stop` or a reboot; `--autostart` runs it as the boot service, which also starts it at login and restarts it after a crash; `--auto-upgrade` installs new versions automatically ([Upgrading](../operations/upgrading.md)). |
| `onw stop` | Stop it, however you started it, once any save or import in progress has finished (it waits up to a minute). A reply Lucius is writing is cut off, as with <kbd>Ctrl</kbd>+<kbd>C</kbd>. If it runs as the boot service, this tells you to use `onw autostart uninstall`, because the service would only start it again. |
| `onw restart` | Restart it the way it was running: in its terminal, in the background or as the boot service. It waits for a save or import in progress, as `onw stop` does, and says when it's back. |
| `onw status` | Show whether it's running, its URL and version, the working folder, the boot service, where the log is, and how an upgrade is going or why it failed. If it isn't running, it says so and exits with an error code, so scripts can check it too. It looks at the server for this working folder on this machine. For Docker, use `docker compose ps`. |
| `onw logs` | Show the end of its log. `--follow` (`-f`) keeps showing what's added until you press <kbd>Ctrl</kbd>+<kbd>C</kbd>; `--lines 200` (`-n`) shows more. |
| `onw autostart on` | Turn autostart on: the app starts now, by itself at every login, and again after a crash. It uses a launchd agent on macOS, a systemd user service on Linux, and a Task Scheduler task on Windows, with no `sudo` or admin. The same as `onw start --autostart`. |
| `onw autostart off` | Turn it off: the app stops and won't start by itself again. Your book isn't touched. |
| `onw autostart` | Say whether it's on, and whether auto-upgrade is on. `onw autostart status` does the same. The older names `install`, `uninstall` and `stop` still work. |
| `onw upgrade` | Update to the newest release. A running server installs it and restarts once it's idle ([Upgrading](../operations/upgrading.md)). `--check` only says whether one is out. |
| `onw reset-password` | Set a new password if you've forgotten it — see [I lost my password](../troubleshooting/lost-password.md). |
| `onw mcp` | Let another AI app (Claude Desktop, Cursor…) use your book — see [Connect AI apps](../operations/connect-ai-apps.md). |
| `onw version` | Print the installed version. |
| `onw help` | List the commands. `onw help start` shows one command's options, the same as `onw start --help`. |

Every command exits with `0` when it worked, `1` when it didn't (for `onw status`, when the app isn't running), `2` when the command was typed wrong, and `130` when you press <kbd>Ctrl</kbd>+<kbd>C</kbd>. Scripts can rely on that.

On macOS and Linux, `onw autostart` logs go to `~/.open-net-worth/logs/`.

To use this server from the desktop app on another computer, choose **On your own server** at
setup — see [Desktop app § Connect to a server](../operations/desktop.md#connect-to-a-server).
Before you open it from another device, read [A note on network access](#a-note-on-network-access).

## Option C: Docker

1. Download the Compose file into an empty folder:

   ```bash
   curl -fsSLO https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest/download/docker-compose.yml
   ```

2. Pick a password (at least 8 characters) and start it:

   ```bash
   AUTH_PASSWORD='pick a password' docker compose up -d
   ```

   In Windows PowerShell, use `curl.exe` in step 1, and for step 2:
   `$env:AUTH_PASSWORD='pick a password'; docker compose up -d`.

3. Open [http://localhost:8080](http://localhost:8080) and sign in with that password. First
   run continues from there.

**The password is required under Docker** — Compose refuses to start without it. Docker's
networking hides the fact that your browser is on the same machine, so without a password
nothing could ever operate the app. [Your first run § Docker](first-run.md#docker) explains
this and what to do if you started without one.

To make the password stick, put `AUTH_PASSWORD=...` in a `.env` file next to
`docker-compose.yml`; Compose reads it on every `up`. It only sets the password the first time —
change it later in **Settings → Security**.

What the Compose file does:

- Pulls `ghcr.io/open-net-worth-ai/open-net-worth`, pinned to the release the file came with
  (set `ONW_VERSION=x.y.z` to run another one). The image also has `latest` and `0.1` tags that
  move to each new release — see [Upgrading](../operations/upgrading.md#update-for-your-install).
- Publishes port 8080 on `127.0.0.1` only, so other devices on your network can't reach it until
  you change that line (the comment beside it explains the risk). `PORT=9000` picks another port.
- Keeps your data in a Docker volume named `open-net-worth-data`, which survives
  `docker compose down` (only `docker compose down -v` deletes it).
- Restarts the app after a crash or reboot (`restart: unless-stopped`).

To update, download the new Compose file and run `docker compose pull && docker compose up -d` —
see [Upgrading](../operations/upgrading.md). To use a Claude or ChatGPT subscription from inside
the container, see [Connecting an AI provider](connecting-an-ai-provider.md#subscription-under-docker).

## From source (developers)

For people working on the code. In a checkout of the source, with Python 3.12+ and Node.js 22+
installed (macOS or Linux):

```bash
make setup start
```

`make setup` creates `.venv`, installs dependencies and the `onw` command, builds the web
interface and this help, and migrates the database. `make start` runs it on
`http://127.0.0.1:8080`. `make autostart` runs setup, then `onw autostart on`.
`CONTRIBUTING.md` covers the two-process dev mode (`uvicorn --reload` + `npm run dev`) and the
test gate.

`make seed` fills an **empty** book with a fabricated example — about two and a half years of
accounts, transactions, holdings and a budget — so you can look around first. It refuses to run
on a book that already holds data.

## A note on network access

On the machine running Open Net Worth, you're trusted without a password — any browser or
program on that machine (`localhost`) can use it. That's deliberate for a single-person app.

A password matters once you open it from anywhere else: another device on your network, a
reverse proxy, or Docker (whose networking hides that you're on the same machine).

Until a password is set, the server **only opens on its own machine**, so nobody else on the
network can claim it first. Opening it from another device shows **Set a password first**: go
to the computer it runs on, set one in **Settings → Security**, then click
**I've set it, sign in**.

Forgot it? See [I lost my password](../troubleshooting/lost-password.md). Exposing it beyond your
own machine is covered in [Exposing it beyond localhost](../operations/exposing-beyond-localhost.md).

## Troubleshooting

- **`onw: command not found` after installing** — `~/.local/bin` isn't on your `PATH` yet. Open
  a new terminal, or run `uv tool update-shell` and then open a new terminal.
- **`AUTH_PASSWORD is required for a Docker install`** — Compose stopped on purpose. Run the
  `up` command with `AUTH_PASSWORD=...` in front, or add it to a `.env` file.
- **Port 8080 is already in use** — start on another port: `onw start --port 9000`, or
  `PORT=9000 docker compose up -d`.
- **The app won't start** — see [The backend won't start](../troubleshooting/backend-wont-start.md).

## Related

- [Your first run](first-run.md) — what happens the first time you open the app.
- [Desktop app](../operations/desktop.md) — background mode, start at login, watched folders, updates.
- [Upgrading](../operations/upgrading.md) — updating each kind of install.
- [Where your files live](../reference/file-layout.md) — what's in `~/.open-net-worth/`.
- [Environment variables](../reference/environment-variables.md) — optional overrides, such as `ONW_ROOT` to keep your data elsewhere.

## Guides and comparisons

Longer write-ups on the Open Net Worth blog:

- [How to Self-Host a Personal Finance App: 3 Install Routes](https://opennetworth.ai/blog/how-to-self-host-personal-finance-app/): the three install routes compared
- [Self-Hosted Personal Finance: Apps, Tradeoffs and Setup](https://opennetworth.ai/blog/self-hosted-personal-finance/): what self-hosting your finances involves

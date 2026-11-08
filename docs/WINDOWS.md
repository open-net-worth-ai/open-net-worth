# Windows (10/11, x64)

Open Net Worth runs natively on Windows with the same features as macOS. This page lists the
mechanisms that differ, the few guarantees that are weaker, and what the owner provides to ship.

## Install

- Desktop app: `irm https://opennetworth.ai/install.ps1 | iex` (`scripts/install.ps1`). It downloads
  `Open-Net-Worth-windows-x64-setup.exe` from the public releases repo, checks it against the
  release's `SHA256SUMS` (fails closed), checks the Authenticode signature (an unsigned installer is accepted
  by default, since releases are not code-signed yet; `-RequireSigned` / `$env:ONW_REQUIRE_SIGNED = '1'`
  refuses one, and a present-but-invalid signature is always refused), and runs the per-user Squirrel
  installer. Running it again upgrades.
- Updates: the installed app updates itself through Squirrel.Windows, from the feed at
  `https://opennetworth.ai/update/<platform-arch>/<version>`
  (the release carries `RELEASES` and the full `.nupkg` beside the installer).
- From source or `uv tool install`: `onw start`; `onw autostart on` registers a per-user Task
  Scheduler task (logon trigger, restart on failure, no window, no admin).
- Server updates (`uv tool install` only): `onw upgrade` by hand, or
  `onw start --autostart --auto-upgrade` to install each new signed release without being asked.
  `install.ps1` installs the desktop app, not a server, so it has no such option.

## Mechanism map

| Concern | POSIX | Windows | Seam |
|---|---|---|---|
| Cross-process locks (instance claim, scheduler, data-maintenance gate, automation triggers) | `flock` | `LockFileEx`, shared and exclusive; the instance lock keeps its readable owner record by locking byte 4096 | `core/file_lock.py` |
| Child process trees (stdio MCP, builder engines, sandbox) | new session + `killpg` | `CREATE_NEW_PROCESS_GROUP` + `taskkill /T /F`; the sandbox uses a Job Object | `core/process_tree.py`, `sandbox/win_job.py` |
| Sandbox resource caps | `setrlimit` CPU / AS / NPROC / FSIZE | Job Object: CPU time, committed memory, one process; `FSIZE` by a scratch-dir watchdog | `sandbox/win_job.py` |
| No-follow directory access (skill folders, watched folders) | `dir_fd` + `O_NOFOLLOW` | reparse-point refusal + identity check before and after each open | `core/anchored_dir.py` |
| Durable writes | fsync file + directory | fsync file; directory entries are journaled by NTFS; `os.replace` retries a transient sharing violation | `core/platform_fs.py` |
| Binary file I/O | n/a | `O_BINARY` on every content descriptor, UTF-8 stdio, UTF-8 child interpreter (`PYTHONUTF8`) | `core/platform_fs.py` |
| Owner-only data root | mode 0700/0600 | `icacls` strips inheritance and grants only the user and SYSTEM on the root the app creates | `core/platform_fs.py` |
| Autostart | launchd / systemd | Task Scheduler (Run-key fallback, no crash restart) | `cli/autostart_windows.py` |
| Server self-upgrade (`onw upgrade`, `--auto-upgrade`) | installs in place, then exits 75 or re-executes | a detached helper copied outside the install waits for the server to exit (0, so the task can't restart the old copy), installs with retries and starts it again; its log is `upgrade.log` in `%APPDATA%\uv\open-net-worth-upgrades` | `cli/self_update.py`, `cli/upgrade_steps.py` |
| Launch at login (desktop) | Login Item | Login item that starts through `Update.exe`, so it survives updates | `desktop/src/main/login-item.ts` |
| Watched-folder imports | inode + dev from `lstat` | same; Node reports Windows file indexes as doubles, the helper compares after the same rounding | `desktop/qa/identity-check.mjs` |

## Weaker than on POSIX (by design limits of the OS)

- **Sandbox `RLIMIT_NOFILE`**: Windows has no per-process handle cap. Not enforced.
- **Sandbox `RLIMIT_FSIZE`**: enforced by polling the scratch directory (0.25 s), so a runaway
  writer can overshoot the cap by up to one interval of writes before it is killed.
- **Sandbox memory**: Job Objects cap *committed* memory, not address space.
- **Sandbox network**: like macOS, no hard network deny (`network_hard_denied` is `false`).
- **Sandbox read-only bundle**: Windows directories have no write bit, so the run strips write from
  the bundle's *files* only; a script could add a new file beside them. The load-bearing control is
  the pre-exec bundle SHA check, unchanged.
- **Anchored directory access**: not atomic. POSIX resolves each name against a pinned descriptor;
  Windows checks the path then opens it and detects a swap in between by comparing file identity.
- **Unsigned builds**: until a certificate exists, Windows SmartScreen warns on first run.
- **Server self-upgrade**: a running program can't be replaced, so another process running from the
  install, such as `onw mcp`, can keep it locked until the helper's retries run out. CI runs its
  tests on `windows-latest`, with the install itself faked; only the real-device pass below
  exercises a locked install.

The desktop app still refuses the sandbox, stdio MCP and builder engines on every platform
("until child-process containment is supported"); that is unchanged and identical on macOS.

## What the owner must provide

1. **A code-signing identity** (none exists yet). Either:
   - **Azure Trusted Signing** (recommended): secrets `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
     `AZURE_CLIENT_SECRET`, `AZURE_TRUSTED_SIGNING_ENDPOINT`, `AZURE_TRUSTED_SIGNING_ACCOUNT`,
     `AZURE_TRUSTED_SIGNING_PROFILE`; or
   - **A code-signing certificate**: secrets `WINDOWS_CERTIFICATE_BASE64` (the `.pfx`, base64) and
     `WINDOWS_CERTIFICATE_PASSWORD`.
   Put them in the `desktop-release` environment. Without them a signed release run still builds the
   installer but does **not** stage it for publishing, unless the repository variable
   `ALLOW_UNSIGNED_WINDOWS_RELEASE` is `true`.
2. A real-device pass (checklist below) on a clean Windows 10 and Windows 11 machine.

### Real-device checklist

- Fresh VM, no Python/Node: run the one-liner; app opens; first-run creates a book.
- Add an account and a transaction; quit; reopen; data is there. Open a second copy: the second
  instance is refused with the "already open" message.
- Settings: launch at login on; reboot; app starts hidden; update the app (install a newer build);
  reboot again and confirm it still starts.
- Watched folder: add a folder, drop a CSV, it imports once.
- Import a PDF statement and a HEIC photo; export a file.
- Lucius: connect a provider, run a turn; background automation fires on schedule.
- `onw autostart on` from a source checkout; log off/on; `status` says running; kill the python
  process and confirm it restarts within a minute.
- `uv tool install` an older release; `onw start --autostart --auto-upgrade`; within a minute it
  moves to the newest release and comes back by itself (`onw version`, `upgrade.log`). Repeat with
  `onw mcp` running from the install: the server must still come back, on either version.
- Path with spaces and non-ASCII characters in the user name; a book on a second drive.
- Uninstall via Apps & features; the book folder is left in place.

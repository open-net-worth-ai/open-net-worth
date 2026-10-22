# Open Net Worth Desktop

The desktop app bundles Electron, regular CPython, the ONW wheel, the existing React UI,
offline fonts and documentation. It ships for macOS (Apple Silicon DMG, signed and notarized),
Windows 10/11 x64 (Squirrel installer, not code-signed yet) and Linux x86_64 (AppImage and
`.deb`). Users download from https://opennetworth.ai (`/download/mac`, `/download/windows`,
`/download/linux`, or `install.sh` / `install.ps1`); the files live on the public releases repo.
End-user documentation is `docs/product/operations/desktop.md`.

## Build and run

Build tools: macOS, Node 22+, Python 3.12+, uv, and Xcode command-line tools. These are not
required on the user's computer once the application has been built.

```sh
cd desktop && npm ci
cd ../frontend && npm ci
cd ../docs-site && npm ci
cd ..
make desktop-runtime
make desktop-test
ONW_DESKTOP_RUNTIME="$PWD/desktop/runtime" make desktop-e2e
make desktop-package
```

`desktop/runtime` must not already exist when building. To rebuild, choose a fresh output
with `scripts/build_desktop_runtime.py --output desktop/runtime-next`, stop the app, and
replace the previous generated runtime. No user book is included in the runtime.

Forge emits the `.app`, `.dmg` and ZIP under `desktop/out`. The runtime manifest version must
match `desktop/package.json` and `pyproject.toml`; bump all release versions together. Runtime
dependencies resolve from `requirements.txt` into `desktop/runtime-requirements.lock` and
install with hash enforcement. The server Docker image installs from the same lock
(`pip install --require-hashes`), so it is the single hash-pinned dependency set. To update it:

```sh
uv pip compile requirements.txt --universal --python-version 3.12 --generate-hashes \
  --output-file desktop/runtime-requirements.lock
```

The desktop build uses `frontend/public/logo-mark.svg` for its transparent splash/recovery
mark and the menu-bar images; the Dock icon is `resources/app-icon.svg` (mark centred) with its BETA badge under it.
It uses the frontend's theme palette and bundled fonts;
macOS icons include Retina sizes. Local unsigned preview packages are ad-hoc signed after
packaging and signature-verified; public releases still require Developer ID signing and
notarization. Update the canonical logo before rebuilding the app.
The macOS Dock artwork occupies an 824px square inside a transparent 1024px canvas, matching
the inset of native app icons. The Dock controls the displayed size.

Development can use the repository backend with `cd desktop && npm start`; that needs the
checkout's `.venv`. A packaged app always uses its bundled interpreter and ignores development
runtime overrides. To test from source beside an installed app, `make desktop-dev` runs it as
**Open Net Worth Dev** (`ONW_DESKTOP_PROFILE=dev`): its own settings folder and default book
(`~/.open-net-worth-dev`), so it never repoints or opens the installed app's book. It can still connect
to any server, such as a local one on port 8080. A packaged app ignores the switch. Its default book is `~/.open-net-worth`, the same as the browser/server
application. First launch shows Welcome before starting a server. **Get started** opens the
folder choice (**Where should we keep your book?**) for both new and existing books. **Continue** on the
suggested folder, or a native folder choice from **Change…**, opens an existing book directly;
**On your own server** (a Connect-to-a-server page in the same window) sits beneath it. Selecting an empty folder starts a new book and continues
to currency setup, whose **I have a backup to restore** link restores into that folder instead. A remembered selected folder opens
automatically on later launches. Electron's application-data directory stores
desktop preferences and Chromium state. Books from earlier previews under
`~/Library/Application Support/Open Net Worth/book` remain available through the folder picker;
no book files are copied or deleted when the default changes.

A full quit and relaunch starts a new, unpersisted Lucius conversation. Previous chats stay
in history. Hiding and reopening the background window, or reloading its page, retains the
current conversation. Native preload uses the existing pending-draft storage contract so
this also works against an older hosted frontend.

One server can own a book at a time. If your 8080 server is already using the default book,
use **Connect to server** with `http://127.0.0.1:8080` to use that running instance in desktop
and browser simultaneously. To let the desktop own that book instead, stop the other server
before opening its folder. The desktop refuses a busy book before database migration and shows
recovery with retry and folder selection after you confirm that folder. An unconfirmed
suggestion is never opened as a fallback.

Use setup's **Back**, **Settings → Desktop → Open another folder…**, or **File → Open book
folder…** to choose an existing working folder (`data/onw.db`) or an empty folder for a new
book. The app reloads and remembers that folder. It preserves the previous book and rolls
back to it if the candidate cannot start. Statement-folder locators and notification cursors
are scoped to each canonical book root. The browser/server root pointer is never changed.
A confirmed fresh book goes directly to currency, model and optional security setup.
The currency page focuses only on currency. Back returns to the native folder choice. There is no second Welcome or Set up click.
An existing configured book opens directly. Restore uses the already selected empty folder and
accepts a portable export ZIP from Settings → Data; daily raw database archives use a different
format. Preview writes no imported records, and Restore this backup performs the import.
Restore preserves the exported currency, then continues to AI provider/model and optional
security setup. A restart resumes AI setup until completion; exports carry no AI credentials.
Native Welcome, loading and recovery follow the system appearance; the frontend also defaults
to System when no theme has been chosen.
Startup uses the same logo and layout through backend startup, access checks and setup checks.
It says **Setting up your book…** for a new book, **Opening your book…** for an existing book,
**Preparing to restore your book…** before backup selection, and **Getting your setup ready…**
while the setup UI loads. Back says **Closing your book…** while releasing the owned server.

On macOS the backend keeps the inherited PATH and adds `~/.local/bin`, `/opt/homebrew/bin`
and `/usr/local/bin`, so Finder launches detect standard Claude Code and Homebrew installs.
This does not load shell startup files or import their credentials. A custom CLI location
can still be configured explicitly in model Settings. This applies to both Claude and Codex.
The backend also preserves `USER`, which Claude uses to locate the account's saved macOS login.

Model setup and **Settings → AI** offer Claude Code and Codex even before installation.
Expand **Install & sign in** to copy the official installer and subscription sign-in commands.
Commands match the server's operating system. Browser users install on the machine running
Open Net Worth, which may be different from the computer displaying the browser.
Both screens check executable availability every five seconds while visible and when the
window regains focus; **Check again** refreshes immediately. Detection preserves drafts and
does not select a provider or prove login. Sign in with an eligible Claude or ChatGPT plan;
plan limits apply. API-key and local-model options remain available.

## Server connections

Welcome has one **Get started** action. The following book-choice screen offers local
folders and **Connect to server**. Restore is offered after selecting an empty local folder.
Enter the root URL of an existing Open Net Worth
server, such as `https://wealth.example.com` or `http://127.0.0.1:8080`, then use that server's
existing password login. The desktop does not start Python or claim a local book in server mode.
The book, AI providers and scheduled work stay on the selected server.

Change this later through **Settings → Desktop → Change connection…** or the native
**Connection…** menu. That menu also works with an older server frontend or an unavailable
server. The last local book stays remembered separately. Cancelling or entering an invalid
server preserves the current connection. Switching away from a server never stops it.

Public servers require HTTPS. Loopback HTTP is supported; private-network HTTP has an explicit
unencrypted-connection confirmation. URLs must name the server origin, without embedded
credentials or a mounted path. Certificate failures are refused. Sessions are isolated in
memory, so restarting the client may require signing in again.

Server-rendered pages receive only the restricted client bridge. Local book/folder watching,
native exports, local provider execution and the desktop launch credential remain local-only.
Use the server's ordinary document upload/download UI. Laptop CLI installations are not
forwarded to a hosted server. Ordinary browser Settings has no native connection controls.

## Private Cloud sign-in and moves

**Hidden for now.** Private Cloud is not offered yet: one switch, `PRIVATE_CLOUD_ENABLED`
(`main/feature-flags.ts`, off unless `ONW_DESKTOP_PRIVATE_CLOUD=1`), gates the first-launch and
Connection rows, the File menu items, the `onwHosted.signIn` and connection-dialog IPC (they
refuse), `onw://` deep links and a saved `selectedHostedUrl` (ignored, never cleared). The code
below is unchanged and returns when the switch is on.

**File → Sign in to Private Cloud…** (and the same button in the connection dialog or after a
sign-out) runs a PKCE flow through the system browser (`main/hosted.ts`, `hosted-auth.ts`,
`hosted-config.ts`): the app opens
`<portal>/connect?client=desktop&code_challenge=…&app_state=…` (plus `&move=1` for a move), the
portal redirects to `onw://auth?slug&code&state`, and the window loads
`https://<slug>.<instance domain>/_gateway/callback?code&code_verifier`. The state is generated
per attempt, valid ~10 minutes and accepted once; the slug must match `^[a-z0-9]{1,32}$`; code
and verifier are never logged. The `onw` scheme is registered with `setAsDefaultProtocolClient`
(packaged builds; Forge `protocols`, the `.deb` and AppImage desktop entries declare it), arrives
through `open-url` on macOS and `second-instance` argv elsewhere, and needs the single-instance lock.

- **Waiting screen** (`flow-dialog.ts`, `flow-pages.ts`): "Continue in your browser" with **Open
  browser again**, **Enter code** and **Cancel**. The code fallback is `POST
  {portal}/api/desktop/redeem` (`hosted-redeem.ts`); what it returns is validated like a deep link
  and still needs this app's PKCE verifier.
- **Pending sign-ins last 24 hours and survive a restart** (`hosted-pending-store.ts`): verifier,
  state and a "move requested" flag are written with Electron `safeStorage`, never as plain text;
  with no keychain they stay in memory. A cold-start `onw://` link is matched against the restored
  entries; each state is accepted once.
- **State check** (`hosted-state.ts`, `hosted-state-page.ts`): before opening a Private Cloud, and
  again if opening fails, the app asks `GET <origin>/_gateway/state`. A non-running state or a
  `min_desktop_version` newer than the app shows its fix in the window. Only a definite answer
  blocks: an older gateway, a proxy page or a network error lets the load try.
- **Account** (`hosted-actions.ts`): the signed-in email comes from the Private Cloud's
  `GET /api/auth/status` → `gateway.email`, shown in File and the connection dialog. Sign out and
  Switch account call `POST /_gateway/logout` with `scope=everywhere` from the Private Cloud's own
  page, then clear this device's session.

A Private Cloud window is a server-mode window (client preload only) in a persistent
`persist:onw-hosted-<hash>` partition, so the gateway cookie survives a relaunch; the saved
`selectedHostedUrl` reopens it directly. Navigating to the portal's `/connect` is cancelled and
restarts the browser flow (throttled to once per 5 s).

Defaults are the production portal and domain; `ONW_HOSTED_PORTAL_URL` and
`ONW_HOSTED_INSTANCE_DOMAIN` override them (plain HTTP only for loopback, e.g.
`ONW_HOSTED_PORTAL_URL=http://localhost:8100 ONW_HOSTED_INSTANCE_DOMAIN=http://localhost:8180`).

**Moving a book** follows the move protocol of `backend/api/move.py` (parts are numbered from 0):

1. `move-pack.ts` streams the local export to a temp file with its sha256.
2. Sign-in completes in a **hidden** window (`agent-session.ts`) in the Private Cloud's partition,
   so the person's window and the local backend stay where they are. All calls to the Private Cloud
   run through that page's preload (`client-preload.ts`), which only accepts the move paths, the
   account status and the logout; the gateway sees same-origin requests.
3. `move-upload.ts` does `begin` → resumable `PUT parts/n` with a checksum each, retried with
   back-off and re-checked with `GET` after a lost reply → `commit` (asking before replacing a
   book that is there) → polling the import's progress.
4. `move-secrets.ts` reads keys and connections from the sender through its own backend
   (`POST /api/move/secrets/export`) and posts them to the receiver (`POST /api/move/<id>/secrets`),
   held in memory only. A failure here is reported in the checklist, not fatal.
5. `finish` switches on what was on; `POST /api/move/pause-copy` then pauses the sender's bots and
   automations, and `moved_to` / `moved_at` are saved with the sender's book in desktop
   preferences (`MovedMarker`). Opening that book later shows a native question and a note in the
   page (`desktop-moved:banner` in `preload.ts`).

**Move back** is the same pipeline reversed: the export is downloaded from the Private Cloud, the
remembered local book is opened in a temporary background backend (`withLocalBackend` in
`index.ts`) while the window stays on the Private Cloud, and the same upload, secrets and finish
steps run against it. A decision record: [moving a book is a resumable
protocol](../docs/adr/2026-10-03-moving-a-book-is-a-resumable-protocol-and-secrets-travel-separately.md).

## Background and startup

After setup finishes, a one-time native prompt offers **Keep running in the background** and
**Start at login**, both off by default. Closing or pressing Escape keeps the saved choices
and dismisses the offer. Change them later in **Settings → Desktop**; connected servers open
the same native dialog. The macOS application menu also provides **Background and startup…**
for older hosted frontends. Ordinary browsers have no desktop controls.

Background mode keeps a menu-bar icon and, for a local book, keeps imports, automations and
browser access running after the window closes. A connected server continues its own work
regardless of whether the desktop client is running. **Start at login** starts quietly in the
menu bar when you sign in; neither a window nor a Dock icon appears until you open the app
yourself. On macOS, X closes only the window; the app remains in the Dock and its local
server keeps running. Clicking the menu-bar icon opens its menu; **Open Open Net Worth**
opens the window. Explicit Quit stops the app. Start at login does not wake
a sleeping computer. The operating system manages login-item registration; a failed change
is reported and never silently marks the prompt complete.

## Browser access in local mode

Use **File → Open in browser**, **Settings → Desktop → Open in browser**, or the background
menu-bar action to open the same running book in your default browser. Settings displays the
current address so you can also enter it directly. The address uses an OS-assigned port and
can change after a restart, backend recovery or book switch. Both clients share the existing
backend, database and live updates; do not start another server against the same book.

Browser access uses the existing **Settings → Security** configuration. New books trust
localhost by default. A configured password with localhost trust off shows the existing
login screen; the existing API token also works for API clients. Stored security settings in
existing books are preserved. Native desktop requests remain automatically authenticated;
browser requests never gain the native bridge or desktop folder-management privileges.

Keep the app running to use the browser. On macOS, closing the window leaves the app and
server running in the Dock; **Quit** stops them. On other platforms, keeping the server
running after closing the window requires **Keep running when the window closes**. The server binds
to `127.0.0.1`, so this address works on this computer only. It is not a LAN/public listener.

## Validation

`node qa/native-titlebar.mjs` (macOS only) checks the hidden title bar: the `data-desktop-chrome`
marker in the local and server-mode windows, drag / no-drag regions, nothing under the traffic
lights, the Go and Reload menu items, and fullscreen. Run it with `ONW_QA_INACTIVE=1` (set by
the script) so windows are invisible, click-through and never focused; screenshots use
`screencapture -l`, which needs Screen Recording permission for the terminal.

`node qa/dock.mjs` verifies macOS X leaves the Dock and server running with background mode
on or off, reopening preserves the window, and a simulated login starts hidden until opened.

`node qa/restore-setup.mjs` additionally exercises the post-setup activity prompt in local and
server modes, off defaults, failed-save retry, background close/reopen, Settings access,
draft dismissal and one-time persistence. Login launch is simulated to verify window
visibility without changing the operator's OS startup settings; a real macOS sign-in remains
a device acceptance check.

`node qa/server-client.mjs` runs actual Electron against two disposable ONW instances on
8098/8099 and a separate browser. It checks existing password login, shared book access,
desktop-only Settings, failed selection/cancel, both mode switches, restart, unavailable-server
recovery and private-header isolation. It requires freshly built frontend/native code and the
bundled Python interpreter; it does not contact the personal server or run AI inference.

`ONW_HOSTED_QA_PYTHON=/abs/python node qa/hosted-signin.mjs` runs the real app against a local
sign-in gateway stack (portal on :8100, instances on `<slug>.localhost:8180`, with a Python that has the
app requirements): move, relaunch, expired session, replayed/forged links, sign-out and the replace
confirmation, with Chromium standing in for the system browser and a second app launch delivering the
`onw://` link. It needs that stack running and a paid portal user it creates itself.

`node qa/cli-install.mjs` checks subscription discovery, exact command copying, real executable
detection, preserved onboarding drafts and explicit selection in Electron and a separate browser.
It also checks narrow layouts and installation discovery in Settings, using a disposable book
and synthetic CLI binaries that perform no inference. It uses the source backend with the
already-built `desktop/runtime` interpreter and requires a fresh frontend and host build.

`npm run e2e` runs the real Electron app and backend with a disposable synthetic book, native
dialog return values supplied through main-process test doubles, and an unreachable synthetic
model provider. It spends no user AI credits. The existing model-enrollment regression tests
cover that stage separately. Screenshots and a JSON report go to `desktop/qa/artifacts`.

`ONW_DESKTOP_RUNTIME=/absolute/runtime node qa/book-picker.mjs` adds real backend switching,
native picker cancellation, empty-book creation, persistence, per-book folder grants, and
refusal/rollback for unrelated folders, newer schemas and books owned by another process.
It uses a disposable `--user-data-dir` profile without `ONW_DESKTOP_ROOT`; that explicit QA
override disables switching.

`node qa/default-book.mjs` uses a synthetic home directory to check existing and fresh
`~/.open-net-worth` books, Welcome before ownership, explicit confirmation, remembered folder
selection, an active server's book lock, and portable restore preview/commit.

`node qa/close.mjs` checks immediate window hiding, background reopening and prompt idle
shutdown with a connected live-update stream. Normal close hides the window while the
backend finishes safely; enabling background operation keeps the backend running instead.

`node qa/browser.mjs` exercises Electron and a separate Chromium browser against one
disposable book: current URL opening, writes/live updates in both directions, password login,
logout, immediate security-setting changes, native API denial and background/quit behavior.
It uses an unreachable synthetic model provider and spends no user AI credits. Install the
test browser once with `npx playwright install chromium`; this is not a user app dependency.

For a packaged black-box check, use Electron's standard `--user-data-dir=/absolute/temp/profile`
flag plus a saved `selectedBookFolder` in that profile's `desktop-settings.json` to isolate
preferences and the book. The profile flag alone does not change the default book. Production
fuses disable inspector/Node launch
modes, so Playwright's Electron debugger is used against the source host with the exact bundled
runtime; the installed executable is additionally tested as a standalone process.
`node qa/packaged-smoke.mjs "/absolute/App.app/Contents/MacOS/Open Net Worth"` relocates the
app into a temporary path containing spaces, removes developer tools from PATH, and checks
local browser access, host/origin guards, desktop-only native APIs, isolated data and graceful
shutdown. Run it against an app in a mounted
DMG to verify the installer contents as well.

## Signing and releases

Releases are gated and signed; the whole procedure, the owner's one-time setup and the manifest
format are in [`RELEASING.md`](../RELEASING.md). In short: every night (20:00 UTC), or by hand, the
`release.yml` of the PUBLIC `open-net-worth-ai/open-net-worth-releases` repo (where Actions are
free; this repo's own copy of the workflow is dormant until its Actions run) checks this repo out,
stamps the release version on the throwaway copy (`scripts/stamp_version.py`; `make release-commit
VERSION=x.y.z` commits one deliberately, and `tests/test_release_versions.py` pins the committed
copies equal), gates it (backend/frontend tests against the committed
known-failure baselines, the security scan, the desktop tests), and on macOS, Windows and Linux
packages, installs and smokes the app. macOS is signed and notarized and must pass `codesign`,
`spctl` and `stapler validate`. Everything lands in a DRAFT release with one `SHA256SUMS` and a
`manifest.json`, both minisign-signed; a verify job re-checks the download; the release becomes
**latest** when verification passes (nightly) or when the owner approves it (manual runs). The landing page's download links and the
update feed follow "latest", so a draft or half-built release is never offered. Asset names never
change (`Open-Net-Worth-mac-arm64.dmg`, `Open-Net-Worth-darwin-arm64-<version>.zip`,
`Open-Net-Worth-windows-x64-setup.exe` + `RELEASES` + `.nupkg`, `Open-Net-Worth-linux-x86_64.AppImage`,
`Open-Net-Worth-linux-amd64.deb`, `install.sh`, `install.ps1`).

The macOS job needs the releases repo's secrets `APPLE_CERTIFICATE_BASE64`,
`APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_TEAM_ID` and `APPLE_APP_SPECIFIC_PASSWORD`.
`scripts/release_desktop_mac.sh` is a local rehearsal of that job on a Mac (it publishes nothing).

`scripts/release_desktop_mac.sh` first runs `make cli-smoke`, the live regression of the Claude Code
and Codex transports against the CLIs installed on that Mac (a few cents of your own logins), and
stops the release if a CLI update changed how Lucius's tools reach the model; `ONW_SKIP_CLI_SMOKE=1`
skips it. See `docs/ai/CLI_REGRESSION.md`.

A packaged macOS app looks for a new release 30 seconds after launch and every 4 hours
(`src/main/updates.ts`). Detection never uses Squirrel (its `checkForUpdates()` downloads at
once): `fetchLatestVersion` reads the releases repo's latest GitHub release (one path for macOS,
Windows and Linux; the unauthenticated 60/h limit is ample at one request per 4 hours). A
BACKGROUND find then calls the updater straight away, quietly — it fetches the ZIP from
`opennetworth.ai/update/<platform-arch>/<version>`
(→ `update.electronjs.org/open-net-worth-ai/open-net-worth-releases`) — and on
`update-downloaded` the menu (and tray) reads **Restart to Update to Open Net Worth X.Y.Z**, which
installs at once when chosen, plus one notification; nothing installs before a restart or the next
quit, and it never restarts on its own (ADR `2026-10-17-desktop-updates-download-in-the-background`).
A MANUAL **Check for Updates…** still asks (**Download and Install** / **Skip This Version** /
**Later**), confirms the download started, relabels the menu **Downloading Update…** and ends in
the Restart Now / Later dialog. Skip is stored as `skippedUpdateVersion` in the app-global desktop
preferences and keeps that version (not a newer one) from background checks and downloads; a
manual check still offers it. A quiet download that fails (or a feed that has not caught up)
falls back to **Update Available…** with no dialog, and the next check retries; one that needs a
manual install (`update-manual`) notifies instead of opening the browser. An install that cannot
update in place (`canInstallInPlace` false) keeps the old notify-and-offer path, Later silencing it
for 24 hours in memory. A failed MANUAL download offers **Open Download Page**; a Squirrel.Mac
signature rejection (an ad-hoc build) says the copy cannot update itself. The first
launch on a newer version notifies once, from `lastRunVersion` in the desktop preferences (an
install from before that field counts if it had already chosen a book or server).
Restart runs the normal quit shutdown first (workers drained, backend stopped) and only then
hands over to the installer. **Check for Updates…** (app menu on macOS, Help elsewhere) checks on demand; dev and QA
builds, and platforms without an updater, open the latest release page instead (Linux has its own, below). `ONW_DESKTOP_UPDATES=0` disables
checks. Squirrel.Mac installs only Developer ID-signed builds, so ad-hoc preview packages
never update in place. The backend migrates the book on the next start and refuses
incompatible/ahead databases untouched; installing older code never rolls data back.

## Linux

Linux x86_64 ships as an AppImage (primary) and a `.deb`; both bundle the same Electron host and
CPython runtime as macOS. Build on Linux with Node 22+, Python 3.12+, uv, `fakeroot` and `dpkg`:

```sh
make desktop-runtime && cd desktop && npm ci && npm run make
```

`npm run make` writes `out/make/**/Open Net Worth-<version>-x86_64.AppImage` (built by
`resources/maker-appimage.cjs` from a pinned, SHA-256-verified `appimagetool`) and the `.deb`.
Electron QA needs a display, so run the QA scripts under `xvfb-run -a`; `qa/dock.mjs` is macOS
only. `node qa/packaged-smoke-linux.mjs /abs/Open-Net-Worth.AppImage` (or an installed
`/usr/lib/open-net-worth/open-net-worth`) is the packaged black-box check.

- **Install.** `curl -fsSL https://opennetworth.ai/install.sh | sh` installs the AppImage to
  `~/.local/bin/open-net-worth` with a launcher entry and icon under `~/.local/share` (no sudo),
  after verifying its SHA-256 against the release's `SHA256SUMS`. Re-run it to upgrade;
  `sh -s -- --uninstall` removes the app and never your book. Or download the AppImage
  (`chmod +x`, run) or `sudo apt install ./Open-Net-Worth-linux-amd64.deb`.
- **Sandbox.** On systems that block unprivileged user namespaces (Ubuntu 24.04 default) the
  AppImage starts without Chromium's sandbox and logs that to stderr; the `.deb` keeps it on.
- **Start at login** writes `~/.config/autostart/open-net-worth.desktop` (launching quietly with
  `--onw-login`); turning it off removes the file.
- **No tray.** Stock GNOME shows no tray icon. With background mode on, start Open Net Worth again
  from the applications menu to bring the hidden window back. Closing the window with background
  mode off quits, as on Windows.
- **Updates.** The app checks the releases repo's latest release 30 seconds after launch and every
  4 hours and, from a background check, downloads without asking (a manual check asks first). An
  AppImage in a writable location verifies `SHA256SUMS` against its minisign signature
  (`SHA256SUMS.minisig`, key built into the app), downloads the new AppImage, verifies its SHA-256
  against `SHA256SUMS`, swaps it in place and offers **Restart to Update to Open Net Worth X.Y.Z**. A `.deb` install (or an
  AppImage in a read-only folder, a release without a signature, or a build without an embedded key)
  offers **Open Download Page** instead of Download and Install; a signature that does not verify is
  an error and nothing is installed.
  `ONW_DESKTOP_UPDATES=0` disables checks. See the
  [Linux decision](../docs/adr/2026-10-02-linux-desktop-appimage-deb-and-self-checked-updates.md).
- **Release.** The `linux` job in `desktop.yml` builds and tests on `ubuntu-latest`, needs no
  signing, and (when `signed`) uploads `desktop-release-linux-x64`. The release job adds
  `install.sh` and one `SHA256SUMS` over every desktop asset, plus its detached signature
  `SHA256SUMS.minisig`. Stable names:
  `Open-Net-Worth-linux-x86_64.AppImage`, `Open-Net-Worth-linux-amd64.deb`.
- **Not on Linux yet:** arm64 builds, file associations (macOS has none either).

### Linux update signing (owner setup)

A checksum file published beside the download protects against corruption, not against someone who
can replace the release assets. The release therefore signs `SHA256SUMS` (and `manifest.json`) with
[minisign](https://jedisct1.github.io/minisign/), and the Linux app verifies that signature with a
public key compiled into it (`src/main/update-signature.ts`, `src/main/update-key.ts`) before it
trusts any hash. **The release refuses to publish until the owner has done this once:**

1. Generate the key pair on a trusted machine, passwordless (CI has no terminal to prompt on; the
   GitHub secret store is the protection):
   `minisign -G -W -p onw-update.pub -s onw-update.key`
2. Put the public key (the second line of `onw-update.pub`, starting `RW`) into
   `UPDATE_PUBLIC_KEY` in `desktop/src/main/update-key.ts` and commit it. It ships in the next
   release of the app; apps built earlier have no key and only offer the download page.
3. Add the whole contents of `onw-update.key` as the repository secret `ONW_MINISIGN_SECRET_KEY`
   (the `release.yml` workflows read it; `RELEASING.md` lists every secret). Then delete the local secret
   key file or store it offline; it is only ever needed by CI.
4. A release run now fails with a `::error::` naming the missing piece if the secret is absent, the
   embedded key is empty, or the signature does not verify against the embedded key (so a wrong or
   rotated secret cannot publish an update that installed apps would refuse).

Rotating the key: ship an app release with the new `UPDATE_PUBLIC_KEY`, then switch the secret in
the following release; installed apps trust whichever key they were built with, so a rotation needs
one release signed by the old key that carries the new key.

## Windows

Windows 10/11 x64 ships as a per-user Squirrel installer (`Open-Net-Worth-windows-x64-setup.exe`,
plus `RELEASES` and the `OpenNetWorth-<version>-full.nupkg` feed files). It is **not code-signed
yet**: SmartScreen may show "More info → Run anyway", and the `windows` job in `desktop.yml` only
stages the installer for release when it is signed or the repository variable
`ALLOW_UNSIGNED_WINDOWS_RELEASE` is `true`. Mechanisms that differ from POSIX are in
[`docs/WINDOWS.md`](../docs/WINDOWS.md).

- **Install.** `irm https://opennetworth.ai/install.ps1 | iex` (`scripts/install.ps1`) downloads the
  installer, verifies it against `SHA256SUMS` (fails closed), and runs it for the current user; no
  admin. Re-running upgrades. Unsigned installers are accepted by default; `-RequireSigned` (or
  `$env:ONW_REQUIRE_SIGNED = '1'`) refuses one, and a present-but-invalid signature is always refused.
- **Updates.** Squirrel.Windows through the same `opennetworth.ai/update/<platform-arch>/<version>`
  feed as macOS (`src/main/updates.ts`), only when the app was installed by Setup.exe (a portable
  folder has no `Update.exe` and does not check). Behaviour matches macOS: background
  download, **Restart to Update to Open Net Worth X.Y.Z**, install at next quit.
- **Start at login** registers a login item that starts through `Update.exe`, so it survives updates
  (`src/main/login-item.ts`). Background mode uses a system-tray icon; closing the window with
  background mode off quits the app.
- **Cross-building.** `scripts/build_desktop_windows.sh` builds the unsigned installer on macOS/Linux
  in a container; CI builds it on `windows-latest`.

## Capability boundaries

- The preload supports native file choices, portable export, preferences, folder grants,
  app commands and validated external links. It exposes no arbitrary path, shell or URL fetch.
- Folder imports wait for stable files and reject symlinks, directory substitution and changed
  contents. Duplicate content stays one job; revoked access stops future intake.
- Review/commit uses the server's existing financial chokes. Watched files retain their
  attachment provenance; Private documents refuse model-backed preview until the user changes
  their Privacy setting.
- Background operation requires the app to be running. Closing hides it only when enabled;
  Quit stops its backend. Sleeping/offline computers do not guarantee scheduled work.
- HTTP MCP and model APIs remain available. Local sandbox, stdio MCP and external Builder
  subprocesses are disabled pending robust OS process containment. Model CLI transports inherit
  the host-owned process group; their binaries/subscriptions are not bundled.
- External embedded pages and artifacts have no native IPC, backend credential or OS permission.
  Artifact scripts have a null origin and no network access.

See [architecture decision](../docs/adr/2026-10-01-desktop-owns-runtime-and-native-capabilities.md)
[first-launch decision](../docs/adr/2026-10-01-desktop-confirms-book-before-startup.md),
and [acceptance evidence](../docs/qa/acceptance/desktop-app.md).

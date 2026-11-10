# Acceptance — Desktop app

Scope: macOS Apple Silicon desktop preview on `codex/desktop-app`. Electron 44.5.1,
bundled CPython 3.12.13, existing FastAPI/React application. Automated validation used synthetic
files and isolated books; the browser/server book and live port 8080 were untouched.

## Default book and lock recovery

The desktop branch is rebased onto main `fdbdd0bff`, including the larger 28px built-in
sidebar mark and transparent in-app artwork. Startup/recovery uses the same transparent
mark as onboarding; the generated macOS Dock icon retains the canonical rounded-square artwork.

`desktop/qa/default-book.mjs` uses a synthetic home directory and bundled backend. It passed:

1. Welcome follows native system appearance and switches dark/light palettes with matching
   onboarding typography at 1024/1440px.
2. Welcome offers Get started and Restore from backup. Get started handles both new and
   existing books; cancelling the picker keeps the folder choice without claiming a book.
   The removed Open an existing book action is refused by the host.
3. An existing suggested book opens only after confirmation, without creating an app-data book.
4. A remembered selected folder takes precedence and opens automatically on restart.
5. A locked suggestion stays untouched through Welcome and Back. Pre-book import/settings/retry
   capabilities are denied.
6. Confirming that busy folder shows **Book already open**, the escaped path and owner port.
7. Retry opens the same confirmed folder after the other server releases its lock.
8. A missing default stays absent until confirmation, then enters currency directly without
   repeating the folder or showing a second Welcome.
9. Restore refuses an existing destination, previews without importing, and explicitly restores
   a portable export into an empty chosen folder.

`desktop/qa/setup-back.mjs` separately verifies the currency page's native Back action:
currency setup without repeating the folder, direct return to folder choice with its backend stopped,
cancellation, reopening the same book, selecting another empty book, and refusal outside
currency setup or after setup completion. Browser onboarding keeps its existing Back flow.

`desktop/qa/startup-stages.mjs` passes five checks with the bundled runtime: a new book's
creation message; matching logo, position, typography and palette through access checking;
the same branded view through setup checking; arrival at the currency-only screen; and the
opening message when reopening the remembered book. It uses a synthetic home/profile and
temporarily holds the real access/setup requests to verify each handoff. Screenshots are
`desktop/qa/artifacts/startup-{creating,access-check,setup-check}.png`.

The twelve book-picker checks also pass, including the precise port/error when selecting a busy
candidate and preserving the previous book. The standalone packaged smoke test uses an explicit
temporary selection; `--user-data-dir` alone no longer isolates the default book.
Seven previously verified backend lifecycle/lock tests and 49 current native host tests pass. Host regressions include
process exit before buffered stdout delivery, retaining the final typed lock error, and clearing
late readiness/credentials after the child closes so another book can be selected.

## Automated application evidence

Run `ONW_DESKTOP_RUNTIME="$PWD/desktop/runtime" make desktop-e2e` after building the
runtime. Playwright launches real Electron and the bundled backend. Native dialog choices
are supplied through main-process test doubles. Model enrollment uses an unreachable
synthetic provider; this run consumes no user model credits.

The ten checks in `desktop/qa/e2e.mjs` passed:

1. First launch creates its own book and completes onboarding. The initial desktop-only
   transport was replaced by the shared browser contract documented below; native APIs
   still require the desktop host.
2. Native import opens the existing OFX review wizard. Approval persists one expense and
   changes a synthetic cash account from 1,000.00 to 987.50.
3. Native export writes a portable ZIP outside the active book.
4. A stable watched file creates a review job without moving money. Duplicate polling
   creates no second job. Revoking access stops later intake but keeps the queued file
   reviewable; its approval changes the balance to 982.50 and completes that job. Moving the
   selected directory changes the open Settings row to Unavailable; restoring it resumes
   Watching without reopening Settings.
5. Interactive artifact scripts work without the native bridge or parent document access.
6. A remote embedded page cannot nest an authorized artifact or access native IPC.
7. Closing the window keeps the backend alive when background operation is enabled.
8. Killing the owned backend shows recovery. Retry preserves the book.
9. Explicit Quit stops the backend. Relaunch preserves balances, jobs and revocation.
10. Launching with Finder's system-only PATH and synthetic native Claude and Codex executables
    under `HOME/.local/bin` detects their exact CLI paths and shows both in onboarding. No CLI
    provider is enrolled or executed by this fixture. The bundled backend also detected the
    real installed Claude and Codex paths with the same launch PATH in a separate read-only
    check.

The twelve additional checks in `desktop/qa/book-picker.mjs` passed with the same bundled
runtime and an isolated native profile:

1. Setup exposes a native picker; picker and confirmation cancellation preserve the book.
2. Choosing an empty folder during setup resumes `/setup/folder`, without repeating the
   greeting or restore choice. The currency step stays available before any financial data.
3. A refused selection from that step restores the previous book at `/setup/folder`.
4. Selecting an existing `data` directory opens its complete book with exact balance 321,
   bypassing fresh setup.
5. Desktop settings shows the canonical root and scopes statement grants to that book.
6. An empty folder creates a new book and first-run flow without carrying old rows or grants.
7. The File menu returns to a previous book and restores its statement grants.
8. Quit/relaunch remembers the selected folder without changing the server's root pointer.
9. A nonempty unrelated folder is refused without changing its files or creating a database.
10. A newer schema is refused byte-for-byte untouched and the previous book restarts.
11. A book held by another process is refused untouched and the previous book remains usable.
12. An unavailable remembered book exposes a recovery picker that opens another book.

Screenshots and the machine-readable report are generated under `desktop/qa/artifacts/`.
CI uploads them with its installers; they are ignored in source control.

Installed delivery was checked separately: the owner's `/Applications` copy still lacked
the native CLI PATH fix, so restarting reopened the old binary. The tested application
replaced that copy with the previous binary preserved; the existing book and history stayed
in place. Codex CLI 0.159.3 then completed one isolated synthetic `gpt-6.1-sol` turn with reply
`OK`, and the owner's installed-app message received a successful response. The earlier
ChatGPT-sign-in model rejection matched [upstream issue #49396](https://github.com/openai/codex/issues/49396);
catalog metadata alone does not prove inference access.

## Packaged application gate

After `make desktop-package`, run from `desktop/`:

```sh
node qa/packaged-smoke.mjs \
  "out/Open Net Worth-darwin-arm64/Open Net Worth.app/Contents/MacOS/Open Net Worth"
```

This copies the app outside the checkout into a path containing spaces, then launches it
with only system tools on PATH and deliberately invalid development overrides. It verifies
the bundled interpreter, isolated database, local browser access with origin/native API guards, and backend
shutdown. This gate caught a resource-copy bug that converted relative interpreter links
into absolute build-folder links. Packaging now validates source links and restores relative
in-bundle links before signing; relocation has regression coverage.

The final Apple Silicon DMG was mounted read-only, its application copied into the isolated
installation path, and all seven packaged checks passed with exit code 0. The image was
detached after verification. Native host tests passed 41/41, including native CLI detection,
inherited PATH precedence, unchanged non-macOS environments, and runtime and DMG
symlink preservation. Switching tests additionally cover draining stale file reads, stale
notification responses, remembered-root symlink replacement, old-frame credentials,
candidate-load/persistence rollback, serialized switching, and per-book device preferences.
The first-launch retry regressions additionally reject an unconfirmed fallback and a replaced
confirmed directory, while allowing the owned backend to create its database in that directory.

## Simultaneous browser access

`ONW_DESKTOP_RUNTIME="$PWD/runtime" node qa/browser.mjs` passed against the freshly bundled
runtime with Electron and a separate Chromium browser sharing one disposable book:

1. Desktop Settings and the native File menu open the current backend URL without credentials.
2. Default localhost access opens the same book in the browser without a native bridge.
3. REST writes in both directions update the other open Accounts screen without reloading.
   Two synthetic expenses reduce the shared opening balance from 1,000.00 to exactly 980.00.
4. The existing password/login cookie works with localhost trust off; desktop remains signed
   in, and browser clients cannot use native folder APIs or receive the private launch header.
5. Saved security settings apply immediately. Browser logout does not sign out desktop.
6. Browser access continues when the desktop window is hidden with background activity on;
   Quit stops the shared backend.

Evidence: `desktop/qa/artifacts/browser-report.json`, `browser-desktop-settings.png`,
`browser-desktop-shared-book.png`, and `browser-shared-book.png`.
Focused auth/transport/live-update/lifecycle tests passed 112 tests with four existing
legacy-store skips. Desktop Settings passed eight frontend tests; 41 native host tests and
all five dependency contracts passed. The class-name gate reports the existing dead classes
`.intel-brief-browse`, `.msg-skill-activity--loading`, and `.msg-skill-activity--failed`; this
change adds no CSS classes. Financial test data is synthetic and no live AI provider runs.

The browser-sharing `.app` passed all seven standalone packaged gates after relocation,
with no developer tools on PATH. Its bundled backend/host bytes and canonical logo matched
the tested source. It replaced `/Applications/Open Net Worth.app` with the previous binary
preserved. Session/message ID counts and hashes remained identical. Clicking the installed
Settings button opened Arc at the owned backend origin and displayed the same existing chat.
The separate server on port 8080 remained healthy. This update rebuilt the `.app`; the DMG
and ZIP were not rebuilt for this change.

## Earlier independent UI and security checks

Running-app UI review passed native Search and New transaction, preference saves and failed
save recovery, neutral file/export cancellation, and the startup recovery screen. Six
Settings layouts (640/1024/1440 pixels, chat open/closed) had no horizontal overflow or chat
overlap. Separate folders with the same basename display distinct canonical locations.
The 640-pixel case is narrower than the native window's supported minimum.

Focused backend tests cover launch credential/Host/Origin checks, ownership, private
attachment provenance before model parsing, grant revocation, checkpoint/restore exclusions,
durable dedup, and descriptor-anchored folder reads. A directory-replacement attack was
reproduced during review and fixed by opening the approved directory and file descriptors
in the private helper. Local sandbox, stdio MCP and external Builder execution are disabled
in desktop mode until robust subprocess containment is available.

Focused frontend tests cover browser fallbacks, watched-file provenance, replacement-file
review completion, and one-time native import under React Strict Mode. Existing import and
artifact tests also passed. Host typechecking, focused Python lint/format checks, dependency
contracts, frontend build and product-doc validation passed.

Picker UI regression tests and platform fallbacks passed 13/13. Independent running-app
review passed eight picker interactions and long-path layouts at 1024/1440 with the Lucius
dock open/closed; optional forced 640 layouts also passed. Native confirmation warns about
reload and unfinished work; cancellation, invalid-folder refusal and recovery were checked.
Security review found a remembered-root symlink redirection issue; startup and rollback now
require the stored canonical root to stay unchanged, and its regression passed.

## Existing repository failures

The broad `make check` stops on pre-existing Ruff formatting in
`backend/services/asset_fundamentals.py`, `backend/services/asset_technicals.py` and
`tests/test_ibkr_fetch.py`. Frontend typechecking reports 142 existing errors against 143 in a
clean HEAD archive, compared by file, TypeScript code and message; the picker uses the formerly
unused location callback and introduces no new errors. CSS validation reports the same
three unused classes as HEAD: `.intel-brief-browse`, `.msg-skill-activity--loading` and
`.msg-skill-activity--failed`. An extended migration suite also encounters the existing
irreversible downgrade of `202609121000_normalize_account_institutions`. These were not
changed as part of the desktop app.

The regular CLI regression suite also has an existing launchd log-path expectation that
conflicts with its current `/dev/null` output. Parser/version and all focused desktop
bootstrap/lifecycle/helper tests passed. Private desktop entry points import with `python -S`
before core configuration or dotenv loads; the watcher helper also runs on clean system Python.

## Release and device boundaries

- The local `.app`, DMG and ZIP are unsigned development previews. Signing/notarization
  configuration is present; it requires the owner's Apple release credentials.
- OS notification delivery, login-item registration and Gatekeeper acceptance still need
  a signed build on an owner or clean test device. Preferences and failure states were tested.
- Windows installer configuration is scaffolding; no Windows execution was performed.
- Updates open the official release page and are installed manually. There is no automatic
  updater or published release from this task.
- Folder intake scans the selected directory's top level. It does not commit financial
  changes automatically. Background work requires a running, awake computer.
- Provider-specific browser control and direct money movement are outside this release.

See the [desktop architecture decision](../../adr/2026-10-01-desktop-owns-runtime-and-native-capabilities.md)
and [developer build instructions](../../../desktop/README.md).

## First-launch delivery and appearance — 2026-10-02

The current source host and freshly bundled runtime both passed all nine default-book checks.
The loading/recovery appearance checks passed in dark/light modes and with reduced motion.
The settled currency screenshot verifies the canonical logo, compact chosen folder and Change
control. The relocated packaged application passed its seven standalone smoke checks. All ten core
desktop E2E checks pass with the freshly bundled runtime, including onboarding, native imports,
background operation, crash/retry and restart preservation. The import QA waits for remembered
source preselection before deciding whether an account still needs to be chosen.

The 69 focused frontend tests include browser onboarding preservation, native restore navigation,
restore preview identity/busy selection, and the actual pre-paint theme script. The frontend and
host builds pass. Full frontend TypeScript still reports 190 existing diagnostics; changed
production files and new tests have none. Native TypeScript and diff whitespace checks pass.

The theme default is System before both native and frontend paint. An explicit frontend palette
choice remains respected. Playwright normally emulates light mode, so native defaults are now
verified against Electron's actual system appearance separately from intentional palette tests.
QA windows are kept outside the visible desktop.

## Claude login identity — 2026-10-02

Claude Code 2.1.287 reported `loggedIn=false` under the desktop's filtered environment,
despite reporting `loggedIn=true` in the terminal. Adding only `USER` to that same filtered
environment restored the saved login, with `--setting-sources ""` still enabled. A real
Haiku request using the desktop environment and provider's exact invocation succeeded with
a synthetic connectivity prompt in a disposable root. No book data was sent.

Nine focused backend cases passed, including four paths receiving `is_error=true` with
`subtype="success"`, session recovery, structured-output retries and secret isolation.
Nine native runtime tests and native TypeScript passed. Credential environment variables
remain stripped; only the ordinary account identity was added to the allowlist.

## Subscription CLI discovery — 2026-10-02

Claude Code and Codex are offered before installation in onboarding and AI Settings, with
official install/sign-in commands and Copy controls. Five-second visible-page detection
updates the installed state without changing the chosen provider or typed configuration.
Commands follow the server's operating system, and browser instructions identify the server
machine. Detection proves executable availability, not authentication or plan eligibility.

`node desktop/qa/cli-install.mjs` passed seven actual Electron/browser checks using a disposable
book and synthetic CLI executables: missing-provider choices, native clipboard Copy, browser
instructions/Copy, automatic detection with preserved drafts and disclosure state, 320px
layout, explicit provider selection, and Settings promotion while sign-in help stays open.
The fixtures answer version requests only; no installers, sign-ins or model requests ran.
Evidence is written to `desktop/qa/artifacts/cli-install-report.json` and its screenshots.

Thirteen focused backend cases, 58 focused frontend cases and six native security cases
passed. Frontend/native builds, scoped helper lint/format checks, product-doc validation and
diff whitespace checks passed. Existing broad frontend TypeScript, Settings/onboarding lint
and three unused CSS selector diagnostics remain outside this change.

Native Copy required a narrowly scoped sanitized clipboard-write permission for the owned
main app document at the active backend origin. Reads, other permissions, subframes,
artifacts and stale origins remain denied; the permission boundary passed its regressions
and read-only security review.

The packaged build was installed and reopened with the existing selected book. Its health
endpoint returned 200, provider metadata reported macOS, and served HTML matched the build.
The installed Settings dialog displays both commands and Copy controls. Desktop preferences
and the standalone 8080 listener and lock were preserved during replacement.


## Desktop client for an existing server — 2026-10-02

Welcome offers **Connect to server** alongside local setup and restore. Desktop Settings
provides **Change connection**; **File → Connection…** remains available when the server is
offline or its UI predates the Settings panel. Ordinary browser access has no desktop
connection selector. Server mode uses the server's existing authentication and runs no
local backend. It remembers the server separately from the local book folder.

`node desktop/qa/server-client.mjs` passed ten actual Electron/browser checks with synthetic
books and password-protected servers on 8098/8099, plus a folder-lock owner on 8096:

- First-launch connection, wrong-password rejection, normal login, and no local book claim.
- Settings switching, failed-candidate rollback, and cancellation preserving the session.
- Browser and desktop sharing one book; a browser write updates the desktop's exact balance.
- Server switching without carrying login or screen data between origins.
- Server-to-local switching without stopping the hosted server.
- Restart remembering the server while preserving the separately selected local book.
- Concrete locked-folder errors without losing the active server connection.
- Explicit alternate-folder selection escaping the lock without stopping its owner.
- Offline startup recovery without a local fallback; Quit exits even with background enabled.
- No remote request carrying the private desktop launch credential.

The harness uses controlled native confirmation and folder-picker responses. No real book,
AI inference, CLI installer or model sign-in is involved. The report and screenshots are
under `desktop/qa/artifacts/server-client-*`. CI now runs this harness alongside desktop QA.

All 67 native tests passed. Focused frontend runs passed 39 initial connection cases and
17 boot/connection cases after the boot-import repair. Frontend production and native
builds, scoped lint, product-doc validation and whitespace checks passed. Broad frontend
TypeScript remains failing with 191 diagnostics, including existing errors in unchanged
RouterShell code; this is not a clean repository-wide typecheck claim.

The relocated packaged app passed all seven standalone smoke checks. Packaging includes
the current frontend, runtime and native host. The installed macOS app was replaced with
verified asset/ASAR parity while preserving desktop preferences and the 8080 listener
and lock. Its Welcome and connection form were inspected directly, then it was left on
Welcome with no book or server selected. Native form spacing was corrected to match the
existing Welcome layout after that visual inspection.

Independent code and security reviews found no remaining high-confidence issues after
fixing failed-folder recovery, offline Quit, protocol registration ownership and callbacks
from retired windows. Remote pages receive only connection/open-browser commands; local
file, artifact and launch-credential capabilities remain unavailable. Public servers require
HTTPS, private HTTP requires explicit confirmation, and certificate verification remains on.

No real cloud deployment or Windows execution was performed. This remains an unsigned
macOS development build; no new installer release, push or merge is claimed.


## Recovery actions simplified — 2026-10-02

Removed the in-page Quit action from local and server recovery, together with its unused
remote preload method and IPC handler. Normal application-menu and tray Quit remain.
A focused Electron UI/behavior check verified only Try again and Change connection on
server recovery, retry returning to the error state, and connection selection/cancellation.
Native shutdown was checked with background mode enabled. The screenshot is
`/tmp/onw-no-quit-recovery.png`. Native packaging passed; no regression suite was rerun.
The existing server-client harness now checks the absent button and uses native app shutdown.


## Single Welcome action — 2026-10-02

Welcome now shows only Get started. Connect to server and Restore from backup are available
on the following book-choice screen. Focused Electron UI/behavior checks exercised both
alternatives and their Back actions without opening a book or contacting a model.
The existing default-book/server-client harness navigation was updated; no broad suite ran.
Native packaging passed. A separate disposable first-run preview shows the current screens
while the user's selected 8080 server connection remains preserved.


## Folder before create or restore — 2026-10-02

Welcome retains only Get started. Folder choice offers local folders and Connect to server.
Existing books open directly. An empty selected folder shows Create a new book, Restore from
backup and Back before starting its backend; Back returns to folder choice. Create continues
directly to the clean currency screen. Restore starts in the chosen destination and accepts
an export ZIP with preview and explicit confirmation. Ordinary browser setup is unchanged.

A focused actual Electron UI/behavior check passed all three paths with disposable books:
no database before the empty-folder choice; Back and Create reaching currency without folder
copy; an existing configured book opening with its synthetic account; and restore importing
that account and SGD into the selected folder only after confirmation. Screenshots are
`/tmp/onw-folder-first-folder.png`, `/tmp/onw-folder-first-choice.png`,
`/tmp/onw-folder-first-currency.png` and `/tmp/onw-folder-first-restore-preview.png`.
Native build and packaging, updated harness syntax and whitespace checks passed. No broad
regression suite ran. Restore still skips AI setup, and portable exports do not contain AI
provider configuration or credentials; that existing gap is outside this placement change.


## Restore continues machine setup — 2026-10-02

Restoring during first run now continues to AI provider/model selection, then optional
security, before opening Lucius. The restored currency and data are preserved. A non-portable
server-side restore marker keeps setup pending across browser refresh, server restart and
desktop restart; completion clears it. Existing configured instances importing data remain
configured. This closes the restore gap recorded above.

`node desktop/qa/restore-setup.mjs` passed seven focused actual browser/Electron behavior
checks with disposable books: preview writes nothing; confirmed restore retains a synthetic
account and SGD without importing source AI configuration; reload, stale setup URLs and
server/app restart resume AI setup; model/provider Back works; model configuration saves;
only completion of optional security stamps setup; and ordinary configured imports do not
reopen setup. The desktop restart retained its chosen destination folder.

The harness used isolated ports 8098/8099, stubbed provider validation and a local synthetic
model responder. It does not prove real provider authentication or paid inference. Evidence
is under `desktop/qa/artifacts/restore-*`. Frontend production and native builds, product-doc
build/link checks, scoped Python lint and whitespace checks passed. No broad regression
suite ran. Frontend scoped lint passed with the existing fast-refresh helper-export rule
disabled; the unchanged baseline reports three such errors in FirstRun.

The rebuilt wheel/runtime and native package were installed with verified backend, frontend
and ASAR parity. Desktop preferences and the independent 8080 listener/lock were preserved.

---
title: A server upgrades itself only when asked, only from a signed wheel, and restarts by exiting
status: Accepted
date: 2026-11-05
deciders: repo owner
---

# 2026-11-05. A server upgrades itself only when asked, only from a signed wheel, and restarts by exiting

## Context

The desktop app keeps itself current; a `uv tool` server had only `onw upgrade`, a wrapper around
`uv tool upgrade`, run by hand. The owner asked for the desktop behaviour on a server, with the
switches as parameters of `onw start`. A server differs from desktop in three ways: often nobody is
watching it, so waiting for a click could mean never; it has no menu to click; and new code would
install itself on a machine holding someone's finances, with releases shipping every day or two.

## Decision

- **Opt in with flags.** `onw start --autostart --auto-upgrade`. Plain `onw start` never makes the
  check. Passing `--auto-upgrade` is the permission to restart without a click, which is where this
  departs from the desktop app, which never restarts on its own. The flags are saved into the
  service, so the service matches what was last typed. `onw autostart stop|status` stay, and
  `onw autostart install` stays as the same thing as `onw start --autostart`.
- **Only a signed, strictly newer release.** The check reads the release's `manifest.json` and
  `.minisig`, verified in-process against the key the desktop app embeds. The signature's trusted
  comment must name the manifest's version, and only a version above the running one is acted on,
  so a replayed older signed manifest can't downgrade anyone.
- **Install that exact file.** The wheel comes from the release, is checked against the manifest's
  checksum, and is installed with `uv tool install <file>`. Never `--force`: it removes the working
  install first, so a dependency that fails to download would leave nothing. `onw upgrade` takes the same
  path, because uv's record of the tool now points at the file: `uv tool upgrade` would find nothing
  newer. The newest wheel is kept beside uv's tools, outside the book's folder, so that command says
  "Nothing to upgrade" rather than failing on a missing file. Re-running the install script installs
  by name again, which restores the ordinary record.
- **Prove it starts before touching anything.** The wheel is installed into a throwaway uv folder
  and `import backend.main` runs there, against a scratch `ONW_ROOT` and `HOME`. `version` alone
  would pass a release that fails at boot, and a release that crash-loops can never reach the
  upgrade that fixes it. That second install is the cost; uv's cache makes it cheap.
- **Wait for idle, confirm before leaving.** It installs only once the server has finished booting
  (boot may be migrating, reading scripts from this install), `turns.any_running()` is false, which
  covers chats and Automation runs, and no write request is in flight, which covers a restore or
  import. It rehashes the wheel, installs, then runs the new copy's `version` command. A failure
  puts the old version back, from its kept wheel when it has one, keeps serving and tries again at
  the next check. The database is still untouched then, so going back is still possible. Work that
  began during the install gets up to two minutes to finish before the exit.
- **The updater never takes the book offline.** Under the boot service, a problem finding uv or the
  install logs a warning and serves without upgrading. Only a person at a terminal gets a refusal.
- **Restart by exiting.** Under the service it exits with 75: launchd, systemd and Task Scheduler
  all restart on a non-zero exit, so no service definition changes. In a terminal it re-executes.
- **Windows hands off.** Its running interpreter and launcher lock the install, so `upgrade_steps.py`
  (stdlib only) is copied out and run by the base interpreter. It waits for the server to exit,
  installs with retries and starts the app again. The server exits 0 there: a non-zero exit would
  let Task Scheduler's restart-on-failure start the old copy in the middle of the install.
- **Refused where it can't work.** A checkout or an image isn't a `uv tool` install, so the flag
  fails before anything starts.

## Consequences

- There is no automatic rollback once the new version has started and migrated the book: an older
  version can't open a newer book. A bad release is fixed by the next one.
- The signing key is now embedded twice, here and in `desktop/src/main/update-key.ts` (a test holds
  them equal), so rotating it needs an auto-upgrading server to take a release carrying the new key
  while it is still signed by the old one, as the desktop app does.
- The wheel's dependencies still resolve from PyPI when it installs; the signature proves the app,
  not its dependency tree. Restoring after a failed confirm uses the kept wheel, but the first
  auto-upgrade has none, so it falls back to that version from PyPI, unsigned: a broken install is
  worse.
- The scratch boot catches a release that fails at import, not one whose database migration fails
  against a real book. A migration failure restores its safety copy and stops, and the service then
  restart-loops until a fixed release is installed by hand: `onw upgrade` still works.
- A voice call has no in-progress record once it has started, so the idle check can't see one
  between turns and a restart can end it.
- The Windows path has been exercised only through its unit tests and an isolated-process test of
  the helper. It hasn't run against a real locked install, and another process running from the
  install, such as `onw mcp`, can keep it locked until the retries run out.

## Alternatives considered

- **Wrap `uv tool upgrade`.** Simplest and keeps uv's record ordinary, but nothing ties what it
  installs to the signed release, and it can't be pinned without pinning the record too.
- **Pin by version (`uv tool install open-net-worth==X`).** Same gap, and it pins later upgrades.
- **Wait for a click, as the desktop app does.** An unattended server would never update, which is
  the case this exists for.

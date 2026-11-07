---
title: onw upgrade asks a running server to upgrade itself
status: Accepted
date: 2026-11-07
deciders: repo owner
---

# 2026-11-07. `onw upgrade` asks a running server to upgrade itself

Builds on ADR `2026-11-05-a-server-upgrades-itself-only-when-asked-and-only-from-a-signed-wheel`,
whose checks, idle rule and restart are unchanged.

## Context

`onw upgrade` replaced the install underneath a running server and printed "Restart the app to use
it". Someone ran it and ended up with `onw version` on the new version, a browser tab offering the
new frontend (read from the swapped files on disk), and a backend still on the old code, with
nothing restarting it. On Windows the install couldn't even happen: the running server holds the
files locked, so the helper's retries ran out and only its log said so.

Only the server knows when it's idle, and `onw upgrade` is a separate process. A plain `onw start`
had no write counter and no way to restart.

## Decision

- **With a server running, the server does the upgrade.** `onw upgrade` asks, and the server runs
  the same `AutoUpgrader.attempt()` as `--auto-upgrade`: verify, test copy, wait for idle, install,
  restart. The owner first agreed to "install, then ask it to restart"; this changes the order.
  Installing first leaves new files under an old process until it's idle, possibly for hours, which
  is the reported bug, and it can't work on Windows at all. With one order there is one path.
- **The request is a file in the working folder**, `.upgrade-request.json`, beside the instance
  lock. It carries no instructions: it can only start the upgrade `--auto-upgrade` would run, and
  that installs only the newest signed release, so nothing in the file reaches the install. Whoever
  can write this folder can already read the book.
- **The file is also how the server answers.** `requested` → `working` (it heard) → removed when it
  is on its way out, or `failed` with a message for the command to print. A server clears any
  request it finds at startup: the command only asks a server that's answering, so that one is from
  a command that has gone. A request the server can't answer is never acted on, or it would be
  acted on again at every poll.
- **Every `onw start` listens and counts writes.** Only `--auto-upgrade` looks for releases: a plain
  start finds its installer only when asked, and never makes a network call on its own.
- **Silence means an older server,** or one not started with `onw start`. After 10 seconds the
  command falls back: on macOS and Linux it installs as before and names the server that still needs
  a restart; on Windows it says to stop the server first and installs nothing.
- **A server behind the install still gets restarted.** `onw upgrade` compares the running server's
  version too, so "up to date" no longer hides a server left on the old version.
- **The restart is the one `--auto-upgrade` has,** plus two fixes it needed once plain servers use
  it. A restart drops `--open`, so it doesn't open another tab. A service installed without
  `--auto-upgrade` has no `--service` flag: on macOS and Linux that's fine, because re-executing
  keeps the process its supervisor is watching; on Windows its launcher now sets
  `ONW_RUN_BY_AUTOSTART`, so it restarts through the task and not in a new console window.
- **A plain server's Ctrl-C is unchanged.** The 10-second limit on open connections applies to a
  plain server only once it's restarting, so Ctrl-C during a long restore waits as it always did.
- **A second `onw upgrade` joins the first.** The server reads no requests while it waits for idle,
  so a new one would look unheard and the command would install beside it. It reports on the upgrade
  under way instead.
- **Stopping during an install waits for it.** Leaving would skip the step that confirms the new
  version runs and puts the old one back. A server stopped this way stays stopped, whichever way the
  install went; a second Ctrl-C leaves at once.

## Consequences

- The command reports what the server says, but it can stop waiting (five minutes, or Ctrl-C) while
  the server carries on. `onw status` is then how to check.
- On Windows the command exits as soon as the server has handed off, so it doesn't hold the install
  locked. A failure after that is only in `upgrade.log`.
- The first release with this can't ask a server on an older version. The command says so, and the
  next upgrade works.
- The voice-call gap from the earlier ADR now reaches plain servers, but only when someone runs
  `onw upgrade`, which is a request to restart.
- As with the earlier ADR, the Windows path has been exercised only through unit tests, not against
  a real locked install.

## Alternatives considered

- **An HTTP route.** It would need authenticating, and `onw upgrade` has no credentials; a route that
  restarts the server open to loopback would let any local program, or a tab through a rebinding
  trick, restart it. It would also need a place in the demo lockdown and gateway rules.
- **A signal to the pid in the lock.** No equivalent on Windows, the lock outlives a crash so the pid
  may belong to another program, and there's no way to answer.
- **`onw upgrade` polls an idle endpoint and restarts from outside.** New work can start between the
  check and the restart, and nothing outside can restart a server started by hand in a terminal.
- **Only a clearer warning.** It leaves a person to work out when it's safe to restart, and the
  Windows failure in place.

## Amendment (same day): the commands say what's happening

The first version went silent for minutes, said "isn't finished yet" about a server that had already
failed to boot, and left the reason for a late failure in a log nothing pointed to.

- **The server names its stage in the file** (preparing, waiting, installing, restarting) and the
  command prints each one. The text is fixed in the code: a stage is only matched against that list,
  and a failure message is stripped of control characters before a terminal sees it.
- **A failure stays in the file if the command has gone,** and `onw status` shows it until the next
  request or restart, so leaving the command early doesn't lose it. "In progress" is shown only while
  a server answers: with none running it's a leftover.
- **Once the server has left, not coming back is a failure.** The command gets a fresh three minutes
  from that moment, so time spent waiting for idle can't use up a slow boot's time, then names the
  log and exits 1.
- **`onw status` names the log,** because a service has no terminal to read. On Windows the command
  also names `upgrade.log` as it leaves.
- **`onw start` writes a failed boot to that log.** The app's file handler attaches well after the
  checks that refuse a boot, and uvicorn reports the refusal on stderr, which a service on macOS or
  Windows discards. Without this, the log the command names would be silent about the one failure
  it's named for. A crash while importing the app, before the server exists, still reaches only
  stderr.

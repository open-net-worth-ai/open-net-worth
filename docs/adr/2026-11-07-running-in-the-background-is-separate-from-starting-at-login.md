---
title: Running in the background is separate from starting at login
status: Accepted
date: 2026-11-07
deciders: repo owner
---

# 2026-11-07. Running in the background is separate from starting at login

## Context

`onw start` holds its terminal until Ctrl-C. The only way to free the terminal was `--autostart`,
which also installs a service that starts at every login and restarts after a crash. Some people
want the server running without a terminal and don't want it coming back by itself.

## Decision

- **`onw start --background` is this run only.** It frees the terminal and is gone after a reboot.
  It waits until this server answers, then prints the address and how to stop it. A failed boot is
  reported there, with the log's path and a non-zero exit, so nothing fails silently.
- **A flag, not a new default.** Installed boot services run plain `start` and must stay in the
  foreground. Flipping the default would make each of them exit and restart in a loop unless they
  were detected, and every script would need a `--foreground`. The flag leaves all of that alone.
- **The child is a plain `onw start`.** It gets its own session (`core.process_tree.spawn_kwargs`)
  and no terminal. `ONW_DETACHED` tells a Windows restart not to open a console window. It's a
  variable because a restart passes its arguments to whichever version it just installed, and a
  hidden flag would crash a later version that had dropped it.
- **Output goes nowhere.** A plain file would grow for the life of the install, the scar recorded in
  `autostart.py`. `app.log` already rotates, and `onw start` writes failed boots to it (amendment to
  ADR `2026-11-07-onw-upgrade-asks-a-running-server-to-upgrade-itself`). `onw logs` reads it.
- **`onw stop` asks through a file,** `.stop-request` beside the instance lock, for the reasons
  `onw upgrade` does: it works the same on every OS, needs no route to lock down, and never signals
  a pid the lock may have outlived. Unlike Ctrl-C it waits up to a minute for writes in flight, since
  nobody is watching a restore get cut off, and it always applies the 10-second limit on open
  connections, or an open tab would hold it forever. A Lucius turn isn't waited for: stopping is a
  request to stop now.
- **`onw stop` refuses a boot service,** which would only start it again, and points to
  `onw autostart off`. It also refuses while the server is upgrading, because the watcher can't hear
  it until the attempt ends.

## Consequences

- A crash while importing the app, before the server exists, reaches only stderr, which goes nowhere
  here. The command says the boot failed and suggests `onw start` to watch it in the terminal.
- A server under a service with a custom `ONW_ROOT`, reached from a shell with no `ONW_ROOT` set,
  resolves to a different folder and finds no server, which is the correct answer for that folder.
- A server older than `onw stop` can't hear it. The command says so and removes its request.
- The Windows path has been exercised only through unit tests.

## Alternatives considered

- **Background by default, with `--foreground`.** See above.
- **`onw daemon start|stop`, as OpenClaw's `gateway start|stop`.** Clean, but it puts a second way to
  start beside `onw start` for a single difference, and `--autostart` already sets the pattern.
- **A pid file and signals for `onw stop`.** No graceful signal for a console-less process on
  Windows, and a stale pid can name another program.

## Amendment (same day): one standard set of commands

The owner asked for the CLI to behave like any other.

- **`onw restart` joins start, stop, status and logs.** It uses the same file as `onw stop`, with the
  word `restart` in it, and the server comes back the way it was running by the path an upgrade
  uses: re-execute, or exit 75 under `--service`, or on Windows a restart-only job for the helper,
  because Windows has no re-exec and a task won't start while it's still running. A boot service is
  no reason to refuse here, unlike `onw stop`.
- **`onw autostart on`, `off` and `status`.** `install` and `stop` read as installing the app and
  as stopping it once, and what they did was flip a switch: start by itself at every login, or
  don't. `onw autostart` on its own says which, with how to change it, where it used to be a usage
  error. `install`, `uninstall` (the name for one day) and `stop` still work, off the help, because
  scripts, the install script and older docs use them. The one-line installer keeps `install` until
  a release has `on`, since it installs whatever the newest release is.
- **`onw help` is a command.** The Windows task's `autostart-run` is handled before the parser, so
  it's on no list and in no error message. It's what installed tasks run, so it can't be renamed.
- **Exit codes are a stated contract:** 0 worked, 1 failed (`onw status`: not running), 2 wrong usage,
  130 Ctrl-C with no traceback.

---
title: The server runs on the standard asyncio loop, never uvloop
status: Accepted
date: 2026-11-15
deciders: repo owner + Claude
---

# The server runs on the standard asyncio loop, never uvloop

## Context

`uvicorn.run()` and `onw start` let uvicorn pick the event loop, and with `uvicorn[standard]`
installed it picks uvloop. On Linux uvloop starts every child process with a bare `fork()`, then
runs Python's after-fork hooks (`PyOS_AfterFork_Child`) and `uv_loop_fork` inside the copy of a
busy, multi-threaded server.

Issue #131: on a Docker host, chat, the analysis sweep and asset briefs all failed with
"Claude Code CLI exited with status -11" and no stderr, every spawn until a restart. The host's
`dmesg` named the crashing process `python3`, not `claude`: the child died in libc's
`pthread_join` (glibc 2.41, offset `0x94377`, reading a freed thread record) before Claude Code
ever ran. Downgrading Claude Code did not help, and neither did switching chat to the API, since
the background tasks still start Claude Code. It never reproduced on macOS, where uvloop spawns
with `posix_spawn`, nor on a fresh container. Under uvloop the same death also surfaced as the
raw `RuntimeError("… WriteUnixTransport closed=True …; the handler is closed")` patched in 0.1.52.

## Decision

Every launch path pins the standard loop through ONE constant, `core.config.SERVER_EVENT_LOOP`
(`"asyncio"`): `python -m backend.main` (the Docker image), `onw start`, and every Makefile
`uvicorn` line (`--loop asyncio`). The desktop app already awaits `server.serve()` inside its own
`asyncio.run()`. `tests/test_server_event_loop.py` fails if a launch path starts uvicorn without it.

The standard loop's subprocesses go through CPython's own spawn path (`_posixsubprocess`, `vfork`
on Linux), which runs no Python after-fork hooks and no `pthread_atfork` handlers in the child.

## Consequences

- Some raw request throughput is given up; for a single-person server it is not measurable in use.
- uvloop stays installed (it arrives with `uvicorn[standard]`) but is never imported by the server.
- Which after-fork step called `pthread_join` was not pinned (no debugger on the reporter's host);
  the fix removes the whole class rather than one handler.

# CLI regression net (Claude Code / Codex)

Both CLIs self-update and have silently broken Lucius before (2026-10-03: Codex's connected
`apps` leaked the operator's Gmail into turns; `code_mode_host` routed tool use around our
`<tool_call>` text protocol; an automation schema failed strict mode). Nothing errors in those
cases — the model just can't reach the book. This is the net that catches the next one.

| Layer | Where | Costs money |
|---|---|---|
| Pure logic (snapshot diff, version tracking, model matrix, boot gating, probe protocol) | `tests/test_cli_regression.py`, `backend/ai/cli_regression.py`, `backend/ai/cli_probe.py` | no — runs in `make check` |
| Live suite against the installed CLIs | `tests/cli_live/` | a few cents of the owner's logins |
| Scheduling on the owner's Mac | `scripts/cli_smoke.py` + launchd | same, nightly / on version change |
| Boot-time self-check in the app | `cli_probe.check_on_startup` | one tiny turn per CLI version |
| Release gate | `scripts/release_desktop_mac.sh` | one live pass |

## The live suite

`make cli-smoke` (skipped unless `ONW_CLI_LIVE=1`, which the script sets). Every check goes
through OUR provider classes (`CodexCliProvider`, `ClaudeCliProvider`) with throwaway data dirs
(`tests/conftest.py`), so the managed `CODEX_HOME` is built fresh and the real book is never
opened. Models per CLI, at most two (`tests/cli_live/matrix.py`): the CLI's own default
(`cli_default_model`) plus the newest other model it lists (the previous generation when the
default is already the newest).

- **Tool reachability.** A synthetic `get_secret_number` tool under an app-shaped system prompt.
  The model must call it; the nonce we return must come back in its final answer. A model that
  asks twice is answered twice (bounded) — that is model behaviour, not a broken transport.
- **No leakage.** Codex: `codex features list` under our managed home has `apps` and
  `code_mode_host` off; `mcpServerStatus/list` and `app/list` return nothing; across a real turn
  the thread holds only user/agent/reasoning items (no native tool call, no `codex_apps` in any
  frame) and `thread/start` reports no instruction sources. Claude Code: the `system/init` event
  under our exact flags reports `tools == []` and `mcp_servers == []` (the process is killed the
  moment init arrives, so that check bills nothing).
- **Structured output.** A real `complete` with the Automation inference schema through
  `_response_format_for` (strict mode) on every model.
- **Reasoning text.** Codex reasoning parts must not fuse (`****`) and, when more than one part
  streams, are newline-separated. A turn that streams a single part warns "not exercised".

### Native tool calls (flag `claude_cli_native_tools`)

`tests/cli_live/test_claude_native_live.py` runs through `ClaudeCliProvider` with the flag on (the
owner's own `claude`): the synthetic `get_secret_number` round trip, a parked call that waits
`ONW_CLI_LIVE_PARK_WAIT` seconds (default 20) for its result and must be answered by the host's
value on the SAME process, and a tool loaded after spawn that the model must reach through
`use_tool` while the host sees its real name and the process is not restarted.

Measured with `claude` 2.1.329 driving our own control-channel handler: a parked `tools/call`
answered 1000 s and again 3700 s (an hour) after it arrived let the same query finish normally (the
CLI emits a `tool_progress` heartbeat every ~30 s meanwhile; it has no tool timeout of its own that
we hit). Our own idle reaper (30 min) is what ends a parked process first. The CLI runs a model's
parallel calls one at a time, so only the first `tools/call` is on the wire until it is answered —
the host collects the whole batch from the `assistant` events instead. `claude` 2.1.329 needs no
`initialize` request from the host and no `CLAUDE_CODE_ENTRYPOINT`; it asks for `initialize`,
`notifications/initialized` and `tools/list` itself.

### Snapshots

`tests/cli_live/snapshots/codex-features.json` (the whole `codex features list`, under our
managed config) and `claude-init.json` (init tools / MCP servers / plugins / agents / skills),
each with the CLI version. A run diffs against them:

- **fails** — a feature whose name matches `cli_regression.RISKY_FEATURE` (apps, code_mode,
  mcp, plugin, connector, tool_search, deferred, browser, computer_use, multi_agent, skill,
  hooks…) turning ON or newly appearing ON; any Claude Code tool or MCP server appearing;
- **notes** (shown as warnings, never failing) — everything else that moved, including the
  CLI's own built-in skills/plugins, which drift between runs.

After reading what changed, `make cli-smoke-update` rewrites the snapshots. Commit the diff.

## Scheduling (the owner's Mac — not CI)

`make cli-smoke-install` writes `~/Library/LaunchAgents/ai.opennetworth.cli-smoke.plist` and
bootstraps it. launchd runs `scripts/cli_smoke.py --scheduled` at load and hourly; the script
runs the suite only when an installed CLI's version differs from the last one tested, or 24h have
passed. On failure it posts a macOS notification (`osascript display notification`) and keeps the
log. State (last-tested versions, last log) lives in `~/.cache/open-net-worth-cli-smoke`
(`ONW_CLI_SMOKE_STATE`) — never the book. `make cli-smoke-uninstall` removes the job. Nothing
installs it for you.

## Boot-time self-check

`main.py` lifespan schedules `ai.cli_probe.check_on_startup` (not under `ONW_QA_MODE`). When the
instance default model is a CLI transport and its installed version differs from the last one
probed (`CliProbeStateStore`, a `runtime_state` namespace — checkpoint-excluded), it runs the
tool-call half of the probe once in the background. If the model does not call the tool, a
notification "Codex 0.x changed how tools work — Lucius can't use your book's tools with it…"
lands in the notification log, once per version. A probe that fails for any other reason
(offline, signed out) records nothing and tries again next boot. It never blocks boot and never
raises.

## What it found on its first run (2026-10-03)

Writing the probe against `gpt-6-sol` / `gpt-6-luna` showed that a bare "you are a test
assistant" prompt gets "that tool isn't available" even on a healthy install, while an
app-shaped system prompt works — so the probe's prompt is shaped like Lucius's. `gpt-6-astra`
sometimes asks for the tool a second time after seeing its result; the probe's bounded host loop
answers it again.

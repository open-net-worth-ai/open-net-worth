---
title: The Claude CLI gets Lucius's tools as native calls over its own control channel; the host still runs them; loaded tools go through use_tool
status: Accepted
date: 2026-11-24
deciders: Repo owner, Claude
amends: 2026-08-01-claude-cli-live-sessions, 2026-11-18-a-claude-cli-session-learns-new-tools-in-the-conversation
---

# The Claude CLI gets Lucius's tools as native calls over its own control channel

Behind flag `claude_cli_native_tools` (default **off**, `remove_by` 2027-02-28). Off, nothing
below runs. Issue #214.

## Context

On the `claude_cli` transport the CLI runs `--tools ""` and Lucius's tools are listed as text in
the system prompt; the model writes `<tool_call>` text and `tool_text.py` parses it. A small
model (Haiku) sometimes writes it half-finished and a fragment (`_directory">`, a bare
`Assistant`, `<parameter name=`) reaches the reply. Hardening the text cleanup (#220) shrinks the
leak but cannot remove it: the failure is the protocol. The complete fix is real tool calls.

A first attempt (PR #218) used `claude-agent-sdk`. It worked (0 residue in 32 live runs) and was
rejected twice over: the SDK wheel bundles a ~347 MB `claude` binary, and every `find_tools` load
and every new user message restarted the CLI query and rewrote the prompt cache (about 2.3x the
cost per turn).

## Decision

**No dependency: speak the CLI's own control channel.** The CLI accepts an MCP server of
`"type": "sdk"` in `--mcp-config` and then sends every MCP JSON-RPC message for it (`initialize`,
`tools/list`, `tools/call`) to its host as a `control_request` with `subtype: "mcp_message"` on the
stream-json stdout we already read; the host answers with a `control_response` on stdin. That is the
whole transport: no port, no token, no child process, no bridge. The wire shapes are
`ai/models/claude_native_wire.py`; the SDK source was only read to learn them (verified live on
`claude` 2.1.329, which needs no `initialize` request from the host).

**Same tools, moved not added.** The tools a process is spawned with become the native tools of
one in-process server, `lucius` (`mcp__lucius__<name>`), instead of the text manifest. Lockdown is
unchanged: `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`, `--allowedTools
mcp__lucius__*` under `--permission-mode dontAsk`, the scrubbed child env.

**The host still runs every tool.** The `tools/call` handler never executes anything. The model's
calls are read off its own `assistant` events and yielded as ordinary `tool_use` events with the
REAL tool name; `assistant.run`, the choke points, `permissions_v2`, the confirm gate and the
approval cards run unchanged. Meanwhile the CLI sits on its `tools/call`: the call is PARKED. The
next host round carries the results; answering the parked JSON-RPC call with the host's result
(`ai/models/claude_native_session.py`) is what lets the same query, with its reasoning and prompt
cache, go on. The CLI runs a model's parallel calls one after another, so only the first
`tools/call` is on the wire when the round ends; a result the host has early is held and answered
the moment its call arrives. Modelled on ADR `2026-10-05-codex-turns-stay-open-across-tool-rounds`.

**Loaded tools go through one generic native tool.** `use_tool(name, args)` is registered from the
start, so the CLI's tool list NEVER changes during a live session and a `find_tools` load needs no
restart. The load is announced in the conversation exactly as ADR 2026-11-18 does (a `<tools_added>`
block with the new schemas, riding the `find_tools` result), but it says to call them through
`use_tool`. The host unwraps `use_tool` to the real tool: the chat row, the approval card,
permissions, audit and the transcript never show `use_tool`. A malformed wrapper reaches the
registry as an unknown tool, like any invented name. Arguments are validated by the registry as
for every call.

**The live session is kept exactly where the text path keeps it.** A native session is a
`claude_live_session` session (same prefix key, same reaper). A round may continue one only when
its transcript adds nothing but the host's results for EXACTLY the parked calls; anything else —
a new message after a Stop, a result held by an unresolved approval, a fold — closes the parked
process and starts a fresh one from the transcript, history rendered with the same
`[called …]` / `[result of … #token]` frames Codex uses. A removed or changed tool also starts
fresh (a process cannot be un-told). Stop kills the process; nothing is left waiting.

**Cost per round.** One CLI `result` closes a whole query, but the host bills per round. A round's
tokens come from the stream's own usage; the cost of a round that ends on calls is a price-table
estimate (`cost_reported: false`) and the query's last round carries the CLI's own total minus those
estimates, so a turn's sum is the CLI's figure.

## Evidence

Isolated instance (`ONW_ROOT` scratch copy, `ONW_QA_MODE=1`, port 8094), real `claude` 2.1.329 on
Haiku, one synthetic book, the same 35 user turns on both paths (14 single questions incl. connector
and `find_tools` prompts, 5 multi-message conversations, 8 writes with approval, 6 approved and 2
denied through `POST /api/chat/{id}/resolve`), 3 workers, runs alternated so both paths read a warm
cache. Five warm runs per path (a sixth native run, the first on a changed system prompt, wrote it
to cache and cost $0.1135; it is left out as the one-time cost of any prompt change):

| per run (mean of 5, ± sd) | text (flag off) | native (flag on) |
|---|---|---|
| residue / empty replies (35 turns) | 1.8 (3, 0, 2, 4, 0) | **0** (0, 0, 0, 0, 0) |
| cost, list-equivalent | $0.0966 ± 0.0063 | $0.1006 ± 0.0028 |
| cost per user turn | $0.0028 | $0.0029 |
| cost per multi-message conversation | $0.0065 | $0.0065 |
| cache write / cache read tokens | 173K / 4.37M | 167K / 5.07M |
| CLI process starts (whole server) | 36.2 | 35 |
| approvals resolved | 8.4 | 9 |
| wall time per user turn | 10.8 s | 10.0 s |

Native costs about 4% more per run than text at the mean, less than one standard deviation of the
text path's own run-to-run spread ($0.087-$0.105), and the same per multi-message conversation:
cost-neutral within noise, not measurably cheaper. It restarts the CLI no more often. A parked call
survived 3700 s of waiting with the real CLI (and 1000 s in an earlier probe) and the query then
finished normally.

### Sonnet (added after a hang and a cost gap were found there)

Same harness, `claude_cli:sonnet`, 6 warm pairs, a warm-up run discarded (35 turns each):

| per run | text | native |
|---|---|---|
| timed-out / failed turns (all 38 runs on both models) | 0 | 0 |
| cost, list-equivalent | $1.391 ± 0.062 | $1.449 ± 0.051 (+4%) |
| CLI model requests | 105.5 | 110.5 |
| cache read tokens | 4.27M | 4.75M |
| peak prompt per request | 43.6K | 45.9K |
| output tokens | 24.4K | 22.1K |
| calls the CLI refused itself ("No such tool available") | 0 | 3.2 |

Two defects were found here and fixed. (1) A hang: the model called a tool loaded mid-session by its
own name, the CLI refused it itself and ended that message on `tool_use`, and the round kept that
stop reason into the next message, closed after its first real call and left the second `tools/call`
unanswered until the 300 s ceiling. A round now resets its call state at each `message_start`, a
`tools/call` the host never announced is refused at once, and a CLI heartbeat while it waits on a
call we hold ends the round or fails fast. (2) A cost gap of +17%: told to "call `use_tool`", Sonnet
wrote that bare name and the CLI, which names the tool `mcp__lucius__use_tool`, refused it; every
refusal is a whole extra request. Every sentence addressed to the model now uses the full name,
loaded tools are announced as ready-made `mcp__lucius__use_tool {...}` call templates (no bare name
to mistake for a callable), and the tool-calling note says a tool outside the function list is
never called directly.

What remains is inherent: the tool definitions the CLI sends to the API are about 2.3K tokens larger
than the text manifest (+5% prompt per request), and Sonnet still calls an unloaded tool by its
prefixed name 1-2 times a run (the CLI refuses that call before it reaches us, so the host cannot
map it). Together that is the +4%, inside one standard deviation of the text path's own spread but
not zero. Haiku is level (cost within 2% on 5 pairs).

## Consequences

- No new dependency and a few KB of source: the wheel grew by 11.3 KB (17,377,323 → 17,388,866
  bytes); no npm change.
- Residue cannot occur on this path: there is no text protocol to half-write.
- A process parked on a call that nobody answers (a Stop, an abandoned approval) is closed by the
  next round of that conversation, or by the idle reaper (30 min) at the latest. An approval that
  outlasts the reaper degrades to a fresh process from the transcript, never to a hang.
- The tool-use rendering of a parked call in the chat (`tool_use_start` at the model's block, the
  approval card) is the same stream `assistant.run` already consumes.
- Not covered: a CLI that stops sending `claudecode/toolUseId` in `tools/call` falls back to the
  oldest open call of that name; a CLI that changes the control protocol is caught by
  `make cli-smoke` (`tests/cli_live/test_claude_native_live.py`), not at runtime.
- Default stays off until the owner flips it. Retire the flag by `remove_by` 2027-02-28: delete the
  text path, or this one if it misbehaves.

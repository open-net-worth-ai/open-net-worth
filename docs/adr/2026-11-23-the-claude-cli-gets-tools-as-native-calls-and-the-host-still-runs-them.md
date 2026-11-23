---
title: The Claude CLI gets Lucius's tools as native calls through the Agent SDK, and the host still runs them
status: Accepted (behind `claude_cli_native_tools`, default off)
date: 2026-11-23
deciders: Repo owner, Claude
amends: 2026-10-05-codex-turns-stay-open-across-tool-rounds (the same park-and-claim pattern, on the Claude CLI)
---

# The Claude CLI gets Lucius's tools as native calls through the Agent SDK, and the host still runs them

## Context

`ClaudeCliProvider` runs `claude -p --tools ""`: Claude Code has no tools, Lucius's are listed in
the system prompt, and the model WRITES each call as a `<tool_call>` block that
`cli_tool_protocol.ToolCallScanner` parses back out (`tool_text.py` then scrubs what leaked).
Haiku 5.5 often writes those calls malformed or cut short and the fragments reach the reply
(issue #214). Live, 30 runs on an isolated instance: 9 had junk — `_directory">`,
`name="get_connector_status">`, `_placeholder_do_not_use`, `ERROR: Invalid JSON in …`, a leading
`�`, `<parse> </parse>`, a reply of only zero-width spaces and `<`, an echoed
`<reasoning_effort>15</reasoning_effort>`, a bare `Assistant` line. Cleaning the text can only
ever chase the next shape; the cause is that a call is TEXT.

Codex already gets our tools as real calls the host runs (ADR 2026-10-03) and keeps its turn open
across tool rounds (ADR 2026-10-05). The Claude CLI had no equivalent: its `--tools ""` takes
only built-ins, but MCP tools are a separate channel that works with `--tools ""`
(spike, `claude` 2.1.329, Haiku: structured `tool_use` blocks named `mcp__lucius__<tool>`,
built-ins stayed off).

## Decision

With flag `claude_cli_native_tools` ON, `assistant._build_provider` builds `ClaudeSdkProvider`
(`backend/ai/models/claude_sdk_provider.py`, a subclass of `ClaudeCliProvider`) instead. Off, the
old provider is built, untouched. `complete` / `stream_complete` and a round with no tools are
inherited and unchanged.

1. **Tools are the tools of one in-process MCP server** (`claude_sdk_tools.build_server`, Claude
   Agent SDK 0.2.187), named `lucius`. Claude Code calls them `mcp__lucius__<name>`; the provider
   yields the BARE name with the CLI's own `tool_use` id, which the host echoes back. A name over
   64 characters with its prefix gets a stable hash-suffixed wire name (`wire_name`).
2. **The host still runs every call.** The MCP handler never runs a tool; it returns
   `PENDING_TEXT` ("Not run: …"). The real result is substituted by a blocking `PostToolUse` hook
   (`updatedMCPToolOutput`) that awaits a future keyed by the call's id. The round yields
   `tool_use` + `done(stop_reason="tool_use")` and PARKS; `assistant.run` dispatches through the
   confirm gate, `tool_permissions`, `permissions_v2` and audit exactly as for every transport.
   The model only ever sees what the host answered, so approvals cannot be skipped: a call the
   host never answers is never answered at all.
3. **The next round claims the parked query** (`claude_sdk_claims.claim`, the shape of
   `codex_turn_claims`) when the newest assistant message's calls are exactly one parked query's,
   the next message is ALL their results and nothing else, and the query was started with this
   round's tools, system prompt and model. The results resolve the futures and the SAME CLI process
   — reasoning, prompt cache and all — goes on. A host `is_error` result reaches the model as
   `Tool error: …`.
4. **Anything else fails the claim and closes the parked query**: a missing result (a call awaiting
   approval past the park limit), a new user message, text merged after the results (an activated
   skill), a fold, a changed system prompt or model, and **a tool loaded by `find_tools`**. The
   round then starts a fresh query from the transcript, with history rendered as
   `[called …]` / `[result of … #nonce]` exactly as the Codex native path does (so the model is
   not shown the `<tool_call>` dialect). The effort is NOT part of the claim: the resume after an
   approval card carries the profile's default effort, and a query keeps the effort it started on.
5. **Bounds.** A parked query expires after `ONW_CLAUDE_SDK_PARK_SECONDS` (default 900), at most 16
   wait at once (oldest closed first), and the hook's own timeout is that plus 120 s. The per-round
   ceiling and stall watchdog are the existing `ONW_CLAUDE_CLI_TIMEOUT` / `ONW_CLAUDE_CLI_STALL`.
   Measured with `claude` 2.1.329: a result 150 s (through the provider) and 703 s (SDK directly,
   explicit hook timeout 760 s) after the call both continued the query, and a hook with NO explicit
   timeout held for 200 s. The tool call itself returns at once, so `MCP_TOOL_TIMEOUT` is never in
   play; the hook timeout is the only clock, and it is set explicitly. Waits past ~700 s are
   unmeasured; past the park limit the query is closed and the round is rebuilt (item 4).
6. **One SDK client per query, in its own task.** The client's anyio task group must be left by the
   task that entered it, and rounds, the park timer and a stopped turn all arrive on other tasks,
   so `claude_sdk_live.LiveQuery` runs the client in one dedicated task and "close" is "cancel it".
   A stopped round closes the query (`events()`'s `finally`); between rounds the park timer and
   server shutdown do. The SDK's close escalates EOF → SIGTERM → SIGKILL and a last `kill()` covers a
   survivor. `core.process_tree` is NOT used: the SDK spawns the CLI in OUR process group, so
   `killpg` would signal this server too.
7. **Lockdown.** `tools=[]` (no built-in), `setting_sources=[]` (no user settings, hooks or
   CLAUDE.md), `strict_mcp_config`, `permission_mode="dontAsk"`, `allowed_tools=["mcp__lucius__*"]`,
   a `PreToolUse` hook that denies every other tool name, and `ENABLE_TOOL_SEARCH=false` (a guard:
   Claude Code can defer many MCP tools behind a search tool we turned off; 161 tools were
   callable with it set, whether they would not be without it was not tested). The CLI runs in an
   empty scratch directory, its system prompt travels as a file (`SystemPromptFile`, past the
   Windows command-line limit), and the user's own `claude` is used via `cli_path`, never the copy
   bundled in the SDK wheel, so their login is the one in play.
8. **Environment.** The SDK lays `options.env` over a copy of this process's whole environment; it
   cannot replace it. Every variable `core.child_env.scrubbed_environ` would drop is therefore set
   to an empty string (`claude_sdk_tools.child_env`), so the child never sees an `ONW_*` setting, a
   provider key or a cloud credential. `ANTHROPIC_API_KEY` only when one is stored for the provider.
9. **Usage.** Tokens per host round come from the stream's `message_start` / `message_delta`
   usage; the CLI reports cost only once, for the whole query. A round that ends on tool calls
   reports a price-table estimate (`cost_reported: False`); the query's last round reports
   `total_cost_usd` minus those estimates, so the turn's sum is the CLI's figure.
10. **The text path is hardened too**, because it stays the default: `strip_tool_syntax`,
    `LiveLineFilter` and the completion gate now treat the residue lines above as nothing, so a
    round of only residue is not shown as the answer (tests: `test_cli_truncated_call_residue.py`).

## Consequences

- No call text exists on this path, so there is nothing to leak. Live check, 32 runs: 0 with residue
  (the text path: 3), see "Measured" below.
- **A tool load costs a restart.** `find_tools` changes the tool list, a running CLI cannot learn
  a new tool (SDK MCP servers drop `list_changed`, and `reconnect_mcp_server` is refused for them by
  `claude` 2.1.329: "SDK servers should be handled in print.ts"), so the round starts a fresh query
  whose prompt prefix (tools first) misses the cache. This gives up what ADR
  2026-11-18 (`claude_cli_stable_tool_prompt`) saved. A new user message also starts a fresh query
  (no cross-turn reuse; the live-session pool is not used on this path).
- One more runtime dependency, `claude-agent-sdk`, whose wheel bundles its own ~350 MB `claude`
  binary that we never run.
- A tool call whose arguments fail the SDK server's own JSON-schema check is answered by the SDK
  to the model as an error while the host also sees the call; the host's registry validates with
  the same schema, so both refuse it, and the query carries on from the SDK's answer.

## Alternatives considered

- **Keep the text protocol and keep cleaning** — rejected: every new malformed shape is a new
  leak; the cause is that a call is text.
- **Raw `claude -p` with `--mcp-config` and our own stdio MCP bridge** — rejected: needs a second
  process and a socket back to the app, a credential for that channel, and re-implements the SDK's
  control protocol (hooks, session handling) by hand.
- **Run the tool inside the MCP handler** — rejected: it would bypass the confirm gate, permissions
  and audit, the one thing the host loop exists for.
- **A generic `use_tool(name, args)` bridge tool** so a loaded tool needs no restart — not built:
  it loses per-tool schemas for the model and changes how Lucius is prompted; recorded as the
  follow-up if restarts prove costly.
- **Expose the whole registry up front** — rejected: hundreds of tools in every prompt is what
  discovery (`find_tools`) exists to avoid.

## Measured

Isolated instance (`ONW_ROOT` scratch, `ONW_QA_MODE=1`), `claude_cli:haiku`, `claude` 2.1.329, the
same 32 prompts both ways (connector and docs questions, net worth, budget, spending, FIRE, three
write flows in manual approval mode approved or denied through `POST /api/chat/{id}/resolve`);
"residue" is any reply matching a pattern list built from the issue's examples, or empty.

| | flag on (native) | flag off (text, with the residue cleanup above) |
|---|---|---|
| runs / residue or empty | 32 / **0** | 32 / 3 (two empty replies, one `<invoke:list_accounts>`) |
| approvals resolved | 10 (8 write flows) | 10 |
| mean wall time per run | 25 s | 31 s |
| mean list-price cost per turn (`turn_done`) | $0.0080 | $0.0035 |

Rounds that ended on a tool call and continued the same query: 72. Claims refused: 49 for a
changed tool set (`find_tools`), 9 for a changed model/effort (the effort rule above was fixed
after this run: the resume after an approval carries the default effort), 1 for a message after
the results. Native turns cost about 2.3x the text path on Haiku at list price in this setup,
mostly the cold prompt re-read after each of those restarts; on a plan that is usage, not a
bill.

## Agent provenance

- **Decided by:** senior-engineer agent, design approved by the owner (SDK route, all tools).
- **Lane / branch:** `feat/claude-cli-native-tools`, worktree `.claude/worktrees/claude-cli-native-tools`.
- **Alternatives weighed:** text-protocol cleanup; raw CLI + stdio MCP; in-handler execution; generic
  bridge tool; full registry up front.
- **Driving artifacts:** issue #214; ADRs 2026-10-03 / 2026-10-05 (Codex native tools, parked turns),
  2026-11-18 (stable tool prompt).
- **Why this way (one line):** a structured tool call cannot leak call text, and parking the call
  keeps the host's gate, permissions and audit the only thing that ever answers it.

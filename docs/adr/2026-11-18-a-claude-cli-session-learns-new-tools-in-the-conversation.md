# A Claude CLI live session learns a newly loaded tool in the conversation, not its system prompt

- **Date**: 2026-11-18
- **Status**: Proposed (behind `claude_cli_stable_tool_prompt`, default off)
- **Area**: AI transports (`backend/ai/models/claude_cli_provider.py`, `claude_live_session.py`)
- **Amends**: `2026-08-01-claude-cli-live-sessions` (the session key only)

## Decision

With `claude_cli_stable_tool_prompt` on, a Claude CLI live session is keyed on the system prompt
**without** the tool manifest. The process still spawns with the full manifest. Each session
remembers the tools it has been shown (name → hash of description + schema). A round may reuse
it only if every tool it was shown is still offered unchanged (`can_serve_tools`). A tool that
`find_tools` (or the discovery gate) added since then is sent as a `<tools_added>` block in front
of the next message, rendered exactly like the manifest's own entries (`render_tool_addendum`).

A removed tool or a changed schema still starts a fresh process with the full manifest, because a
process cannot be un-told about a tool. So does any transcript rewrite, as before. Tool **order**
is no longer part of the key.

Flag off: the key is the full spawn argv, manifest included, which is the old code path.

## Why

The manifest is appended to `--system-prompt`, and the session key is the spawn argv. So every
tool load changed the key, missed the live session, spawned a new process and re-sent the whole
transcript. The CLI then wrote the full prefix to its prompt cache again, at the 1-hour TTL
(2× the input price).

Measured with `claude` 2.1.316, `sonnet`, on a subscription login, using the same five-turn
script on an isolated QA instance with synthetic data:

| turn | what it did | flag off: cache write / cost | flag on: cache write / cost |
|---|---|---|---|
| 1 | first turn | 38,494 / $0.163 | 39,632 / $0.166 |
| 2 | already-loaded tool | 1,000 / $0.013 | 869 / $0.012 |
| 3 | `find_tools` → get_spending_summary | **41,115 / $0.174** | **2,123 / $0.025** |
| 4 | already-loaded tool | 1,874 / $0.017 | 1,518 / $0.016 |
| 5 | `find_tools` → get_income | **44,257 / $0.188** | **1,538 / $0.023** |

In both flag-on runs the model called the newly announced tool next, with valid arguments.

Other harnesses handle discovered tools the same way. Anthropic's API `defer_loading` keeps
deferred tools out of the cached prefix and expands a `tool_reference` in the conversation.
Claude Code's own ToolSearch returns schemas in the tool result. Codex CLI is reported to append
discovered tool schemas at the tail of its context.

## Consequences

- Each tool load saves about $0.15 at list price (on a plan: usage limits and prefill time). It
  happens once per tool per conversation, since loaded tools persist.
- A `<tools_added>` block lives only in the live process's history, not in our transcript. A
  fold or any other rewrite spawns a fresh process whose system prompt carries every tool, so
  nothing is lost.
- Not covered: the direct Anthropic API provider's `tools` array also grows per turn and sits at
  the front of the cache. `defer_loading` is the native fix there, and it is a separate decision.
- Retire the flag by `remove_by` (2027-02-28): promote the on path, or delete it.

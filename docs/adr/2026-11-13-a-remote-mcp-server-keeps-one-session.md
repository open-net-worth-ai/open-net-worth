---
title: A remote (HTTP) MCP server keeps one session for the app's lifetime
status: Accepted
date: 2026-11-13
deciders: owner + implementing Claude agent
---

# A remote (HTTP) MCP server keeps one session for the app's lifetime

## Context

ADR `2026-08-22-external-mcp-stdio-connections-persist-for-the-app-lifetime` gave every stdio
server one connection for the app's lifetime, and left HTTP "untouched (still one connection per
call)" on the reasoning that there was no process to reuse.

That missed the Streamable HTTP **session** (`Mcp-Session-Id`). A stateful remote server — a
broker's — issues it on `initialize` and refuses requests without it. Boot discovery opened a
session and threw it away; each tool call then sent none, so the server answered every call
`400 missing Mcp-Session-Id header` while its card read Connected. The first fix (PR #111)
opened and closed a session per call: correct by the spec, but four requests per call, and any
state the server keeps on the session (a signed-in trading session) was lost between calls.

Codex (`codex-rs/rmcp-client`) and Claude Code keep one session per server, reused by every call,
re-opened when the server answers 404.

## Decision

An HTTP server's entry in the app-lifetime pool is ONE `McpClientConnection` holding its session.

- **Opened by boot discovery** (and the scheduled re-read), reused by every tool call. A first
  call with no boot opens it. The handshake (`initialize` + `notifications/initialized`) is cached
  on the connection: once per session.
- **Concurrent calls do not queue.** The per-server lock covers connect-or-reuse only; HTTP
  requests are independent, and the connection serializes request ids and a re-handshake.
- **A 404 on a request that carried a session** raises `McpSessionExpired`; the connection
  re-handshakes once (concurrent callers that saw the same dead session share one) and repeats
  the request. Safe: the spec says a 404 for an unknown session was not processed.
- **Nothing a call does ends the session.** A tool error (`McpToolError`, the tool's own
  `isError`) and a network failure leave it; only a config change (`reconcile_pooled_connection`,
  or a changed endpoint/credential signature on next use) or shutdown (`close_all_pooled_connections`)
  closes it, with a best-effort `DELETE`. A stdio child likewise survives a tool error now.
- **An OAuth refresh keeps the session.** The access token is not part of the pool signature; the
  refreshed config is handed to the live connection (`adopt_config`).
- A server that refuses `initialize` still gets its calls, sessionless, as before; the handshake
  is retried on the next use.
- The interactive probe / "Refresh tools" click stays a one-off connection under its own
  deadline.

## Consequences

- One request per tool call; server-side session state survives across calls and conversations.
- Calls to one HTTP server can run concurrently (they could before); stdio stays serialized.
- A server that loses sessions often costs one extra handshake per loss, not per call.

---
title: Hosted AI apps connect to /mcp through the app's own OAuth server
status: Accepted
date: 2026-11-16
deciders: repo owner + Claude
---

# Hosted AI apps connect to /mcp through the app's own OAuth server

## Context

claude.ai, Claude on the phone, Claude Desktop's custom connectors and ChatGPT connect to an MCP
server from their own cloud and only speak OAuth 2.1 (authorization code + PKCE, with RFC 9728 /
RFC 8414 discovery). None of them can send our static API token: Claude's static-header option is
a beta for some organizations, and OpenAI says ChatGPT "can't present custom API keys". Self-hosters
asked to use their book from those apps (issue #137). The static token is also all-or-nothing,
which is a poor fit for a credential a cloud service holds.

Facts the design rests on (2026-11-16): Claude and ChatGPT both prefer Client ID Metadata Documents
(CIMD) and also accept owner-entered client credentials; Claude's callback is
`https://claude.ai/api/mcp/auth_callback`; ChatGPT's stable callback needs the RFC 9207 `iss`
parameter; Claude refreshes up to five minutes before expiry and expects rotating refresh tokens
and `invalid_grant` for a dead one. External MCP servers' tools are not in `TOOL_SCHEMAS` and
`dispatch` refuses them, so they are unreachable through inbound `/mcp`. Authlib documents an
authorization server only for Flask and Django and lists no CIMD support.

## Decision

- **The app is its own authorization server for one resource, `<PUBLIC_URL>/mcp`.** `PUBLIC_URL`
  (`ONW_PUBLIC_URL` or Settings > MCP; https, origin only, publicly routable) is the only source of
  every URL it publishes — never the request's Host or `X-Forwarded-*`.
- **Clients:** CIMD only from a fixed list of known hosts (`claude.ai`, `chatgpt.com`), labelled by
  a name we hold, never the name the document claims; any other app uses credentials the owner
  creates in Settings (secret shown once, stored hashed). No Dynamic Client Registration.
- **Tokens:** opaque, prefixed (`onw_at_` / `onw_rt_`), stored only as SHA-256 hashes in four
  tables that are SECRET (SQL-denied, stripped from a sanitised backup, never restored) and
  checkpoint-excluded. Access tokens last 1 hour; refresh tokens rotate on every use, expire after
  30 idle days, and a grant ends after 180 days. A rotated refresh token presented again within 60
  seconds while its successor is unused is a retry and answered once; any other reuse, or a reused
  authorization code, revokes the grant and notifies the owner.
- **Scopes:** `read` and `write`. With `read` only, a tool runs only if it is registered,
  `readOnlyHint`, and not `may_change_something` (the two classifiers, decided per call, so a REST
  tool added at runtime is judged when used); write tools are hidden from `tools/list` and refused
  if called. `write` allows what the static token allows. Blocked tools stay blocked for everyone.
- **Consent** asks for the instance password typed on the consent page, every time — not a session
  and not loopback trust — rate-limited with sign-in. Read-only is the default; changes are a box
  the owner ticks. The page says to deny a link someone sent. Each new grant notifies the owner;
  Settings lists and revokes grants; any password change, reset or removal, and any change of the
  public address, revokes every grant.
- **OAuth tokens open `/mcp` only.** While the server is available, `require_auth` hands `/mcp` to
  its own gate (which also answers the 401 that names the protected-resource metadata); no other
  route accepts an OAuth token.
- **When it exists:** flag `mcp_oauth` on, a valid public address, a password set, sign-in on,
  loopback trust off, and not the public demo, gateway mode or the desktop app. Otherwise every
  OAuth route is 404, the `/mcp` 401 is exactly as before, and issued tokens are not honoured.
- **Hand-written**, no new dependency.
- The static API token keeps full access; nothing that works today changes.

## Consequences

- A self-hoster with an HTTPS address connects claude.ai or ChatGPT by pasting one URL and typing
  their password, and can give a cloud app read-only access.
- Consent phishing across accounts — an attacker sending the owner an authorize link started from
  the attacker's own Claude account — stays possible, as with any OAuth server. It is narrowed by
  the typed password, a 10-minute request window, the read-only default, the notice on every new
  grant, and Revoke.
- An app that does CIMD from another host needs owner-created credentials until the host list grows.
- Loopback trust must be off. A same-host proxy or tunnel makes every visitor "loopback", so an
  instance with trust on and a public address is already open without a password.
- Gateway (Private Cloud) support and DCR are follow-ups.

## Alternatives considered

An external identity provider in front (kept as a docs note); the token in the URL (claude.ai and
ChatGPT always start OAuth, and URLs leak into logs); open DCR (anyone could mint clients with their
own redirect URIs); CIMD from any host (an anonymous caller would choose what the server fetches and
what name the consent page shows); JWT access tokens (one server, and revocation needs a lookup
anyway); Authlib (no FastAPI server integration, no CIMD); consent by session cookie (proves nothing
about who is at the screen now, and makes the approve call CSRF-sensitive); HTTP 403 step-up for
scope (scope is per tool inside one endpoint, and client support is undocumented).

Design: `.claude/rfcs/remote-mcp-oauth.md` (maintainer's local copy).

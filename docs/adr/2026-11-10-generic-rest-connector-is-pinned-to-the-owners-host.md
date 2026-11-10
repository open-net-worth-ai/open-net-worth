---
title: The generic REST connector is pinned to the owner's host
status: Accepted
date: 2026-11-10
deciders: repo owner + orchestrating agent (Claude)
---

# 2026-11-10. The generic REST connector is pinned to the owner's host

## Context

People ask for data sources we have no connector for: a bank with a developer API, a tool they host
themselves. Until now Lucius could not make an HTTP request of its own. `fetch_page` hands a URL to
the search provider and gets page text back, with no method, header or key; the code sandbox has no
network; an MCP server needs someone to write one.

The owner asked for a **generic** connector: an address, a key and the endpoints, set up from an
OpenAPI document or by hand, with as many APIs as a person wants, each its own connector.

A fully generic HTTP tool is also the tool `KNOWN_ISSUES.md` already warns about: a prompt injection
that can read the ledger (`run_sql_query`) and send a request anywhere can take the book out.

## Decision

- **One connector per API.** Kind `rest` is open-ended like `mcp`: each API the owner adds is a row
  `rest:<slug>`, with its own card, Pause, Remove and permission rows. The slug is made from the
  first name, or typed by the owner in the form before the first save, and never changes. The catalog's built-in `rest` instance holds nothing and never reads
  as set up: it is the Browse & add card that opens the form for a new one, so the frontend's
  native add flow works as it does for every other connector.
- **Everything an API holds is a secret**: base URL, credential, fixed headers, endpoints, notes. A
  base URL is a credential, and an endpoint path or a pasted note may hold one, so nothing of an API
  lands in `connectors.config` or `state`.
- **Its tools are ordinary registry tools, registered at runtime.** `ai.tools.rest_api` registers
  `rest__<slug>__<tool>` for each API while its connector exists, tagged `connector="rest:<slug>"`,
  and drops them when it goes. Because they are registry tools, discovery, the permission table,
  confirm cards, the Auto judge and an Automation's lists work per API and per tool with **no change
  to either gate**. The alternative, a second "external" dispatcher beside MCP's, would have
  duplicated the staging and card path, which is the security-critical part.
- **Every declared endpoint is a tool.** Its stored id (from the OpenAPI `operationId`, else method
  and path, kept through edits) names it; its parameters are the arguments
  (`services/rest_api/endpoint_call.py`); the method and path template are fixed. A GET is a read;
  anything else declares `external_effect`. This is what MCP already gives a server's tools, and the
  costs are the ones the app already accepts there: a description written outside the app
  (sanitised, as MCP's are), a tool that disappears when its endpoint does (a stored name then fails
  as an unknown tool), and hundreds of tools. Every REST tool is a bare name in the prompt
  catalog's flat list, with no section, summary or roster line however many there are, the way
  Claude Code lists tools it has not loaded (owner, 2026-11-11): `find_tools` and the discovery
  gate load a schema when it is needed.
- **The declared endpoints are the whole API.** There were first three generic tools per API
  as well (`list_endpoints`, `get_data` for any path, `send_request` for any path); the owner
  removed them (2026-11-11): an MCP server has no such tools, and "any path" made the declared
  list a suggestion. The owner's **Instructions for Lucius** reach him in the system prompt,
  fenced per API (`ai.prompts._fmt_rest_api_instructions`), as an MCP server's instructions do,
  capped at the same 8,000 characters.
- **The host is the boundary.** Whatever tool is called, `services/rest_api/client.py` builds the URL
  from the saved base, re-checks the origin and path prefix after joining, refuses `..` (however
  encoded), `//`, a scheme, a backslash or a control character, and follows a redirect only while it
  stays under the base URL. A path argument is percent-encoded whole, so it stays one segment. The
  credential and the owner's fixed headers are added there; a caller cannot set a header.
- **An owner-configured address.** Requests resolve through `net_guard.resolve_owner_url` and a
  pinned connection: a LAN or loopback API works on a self-hosted install, never in gateway mode,
  and link-local metadata never.
- **The response is whole or refused.** "Tool results are bounded at the tool" is met by the
  request (`?limit=`, a page, a date range), which Lucius sets. `MAX_RESPONSE_BYTES` (1 MB) is a fuse
  for an API that ignores that: over it the call fails with the size and returns nothing, because
  half a month of transactions reads as a whole one.
- **No failure quotes the request.** `http.client` and `urllib` put the URL and header values in
  their error text, which here is the base path and the key. `client.send` turns every failure into
  the exception's class name, and a credential with a control character is refused at save. Found by
  the security review of this change: a path with a space returned the full URL, key included.
- **Bounds on a document.** An API declares at most 500 endpoints, 100 parameters each, so a hostile
  OpenAPI document cannot make the parser or the registry do unbounded work.
- **Setup is human.** The form and `/api/rest-connector/*` are the only writers. Lucius has no tool
  that adds an API, changes an address or reads a key, and the routes are locked on the public demo,
  which also refuses every `rest__` tool.

### What runtime registration needed

- `sync_rest_tools` reconciles the registry with the table: at boot, after a settings write, at the
  top of every offer (`_connector_gate.hidden_connector_tools`) and on a dispatch miss for a `rest__`
  name. Two reads per offer; a fingerprint makes an unchanged API a no-op.
- `ai.tools.TOOL_SCHEMAS` is built once at import and is what the turn loop offers from and
  `find_tools` searches, so a runtime tool is added to that same list object and removed with it.
- Three reviewed NAME lists cannot name a tool created at runtime: the old gate's
  `EXTERNAL_EFFECT_TOOLS`, the discovery gate's never-auto-execute set and the inbound MCP
  read-only annotations. Each asks `registry.is_rest_send(name)` instead, which is the tool's own
  `external_effect` declaration. The static classification sets and the schema golden file do not
  list REST tools; `tests/test_rest_connector.py` pins their shape and gating.
- A handler reads the API and the endpoint when it is CALLED, not when it was registered, so an
  edit applies at once and a removed endpoint or a paused API refuses even in a worker that has not
  synced yet.

### What the second security review changed

A review of this shape found no way out of the pinned host or path. It did find these, all fixed
and pinned by tests:

- **The key's query parameter is the app's alone.** With an API key in the URL, a caller's own
  `?api_key=` rode in front of the real one, and a server that reads the first value would run the
  request as someone else. `client._with_auth` now drops any caller pair with that name.
- **A grant follows the endpoint, not the label.** A permission row is keyed by tool name, and an
  endpoint keeps its name through an edit, so an Always allow given to a GET carried on for the
  DELETE it was edited into. `apis._withdraw_stale_grants` takes back an Always allow when its
  endpoint's method or path changes, every one on the connector when the base URL changes or the
  API is new (or arrives through Move), and the row of a removed endpoint. A Blocked or Needs
  approval row is never weakened. This is the rule MCP's edit already follows
  (`tool_permissions.revoke_allowed_tools`). The form's endpoint list carries a permission
  switch per row (added after this review, at the owner's request, as an MCP server's form
  has): a level picked there is applied AFTER the withdrawal, as the owner's decision about
  the endpoint as saved, and the form itself puts an allowed row back to asking, in view, the
  moment its method or path is edited. A row nobody touched sends no level and gets no row.
- **A tool refuses when its endpoint changed since it was registered.** `external_effect` is fixed
  at registration and the method is read at call time, so a worker that had not re-synced could send
  a just-edited POST under a read's classification. The handler compares `(method, path)` with what
  it was registered for, sends nothing on a mismatch, and re-syncs so the next call is judged afresh.
- **An endpoint tool takes only its declared arguments.** Dispatch checks types, not unknown keys,
  so `resolve` refuses an undeclared argument, and a free-form `body` only where no body field was
  declared.
- **One bad row cannot cost the others their tools.** A row that came through Move never passed
  `clean_endpoints`. `_stored_endpoints` drops repeated ids and malformed entries and re-applies the
  text and bounds on every load; a registration that fails part-way rolls back what it added; each
  API registers in its own try. Move refuses the launcher id.

## Consequences

**Positive**

- Any JSON API with a key works with no code of ours, and rows still reach the ledger through
  `add_transactions`, so refs, dedup and confirm cards are unchanged.
- Permissions are per API and per endpoint: a search POST can run freely while a transfer POST asks.
- The exfiltration path a generic HTTP tool would open is closed by construction, not by a cap.

**Negative**

- The registry is no longer fixed at import. Anything that snapshots it at import (as
  `TOOL_SCHEMAS` did) will miss runtime tools; a test that adds an API must clear them
  (`rest_api.reset_registered_for_tests`) or it pollutes the registry-wide tests after it.
- Under the default gate a connector tool asks on first use, reads included, so an unattended
  Automation needs its tools set to Always allow first.
- Every pull goes through the model: tokens per sync, and a misread is possible.
- No OAuth sign-in. An API that offers only that needs a personal access token, or an MCP server.
- An API the owner points at is trusted with whatever Lucius sends it in a request it was asked to
  make, and its document's text reaches tool descriptions. Both are sanitised, neither is judged.
- Re-importing a document replaces the endpoint list; an operation whose `operationId` changed gets
  a new tool name.
- Editing an endpoint's method or path, or the base URL, drops Always allow grants it had. Rotating
  the key does not: the API being trusted is the same.

## Alternatives considered

- **One connector holding a list of APIs** (the Plaid shape). Built first: no new id prefix, but one
  permission level covered every API. The owner asked for separate connectors.
- **Only the generic tools.** Built second. Lucius had to read the endpoint list and compose each
  request, and a permission could not tell one POST from another.
- **Routing REST through the external-MCP dispatcher.** Per-API tools without touching the registry,
  but the names would read `mcp__…` and the gate, card and Auto-judge paths key on that prefix.
- **A cap or a `select` on the response.** Silent truncation is how a sync books part of a period.

## Agent provenance

- **Agent:** Claude, in a session with the repo owner, who set each step of the shape: generic,
  OpenAPI optional, no cap, then one connector per API, then one tool per endpoint.
- **Lane:** working tree on `main`, uncommitted at the time of writing.
- **Verified:** the request path against a loopback HTTP server (query, POST body, same-origin
  redirect, cross-origin redirect refused, 4xx body, the fuse); the setup and per-connector pages in
  a browser on a scratch instance; two independent security reviews, one of the first shape and one
  of this one, whose confirmed findings are fixed above.
- **Not verified:** a real third-party API, TLS to one, a Lucius turn calling the tools end to end,
  OAuth-only APIs (unsupported), Windows, more than one uvicorn worker.

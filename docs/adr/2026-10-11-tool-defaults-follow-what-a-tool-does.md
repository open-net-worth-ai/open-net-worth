---
number: 2026-10-11-tool-defaults-follow-what-a-tool-does
title: A tool's undecided default follows what it does — a read runs, a change or a send asks — for built-in and connector tools alike
status: Accepted
date: 2026-10-11
deciders: repo owner (explicit, in-session) + senior-engineer lane (Claude)
---

> **Behind the `permissions_v2` feature flag** (`docs/FEATURE_FLAGS.md`, `remove_by` 2027-01-31). Everything below is how the app behaves with the flag ON; with it OFF the previous behaviour runs on the previous code. `ai.permissions.engine()` is the one switch point.

> **How the flag changes the mechanism below.** `refreshes=` is not introduced: the old tests pin the read-shaped writers as `mutates=`, so under the flag the curated `GATE_EXEMPT_READ_SHAPED` set is what says "a read that only refreshes a screen" (`ai._confirm_gate_v2.needs_approval`). `external_effect=` and `unavailable=` are on the tools as described. `x_search` is not retired here (that goes with the separate web-search change), so item 6 and the `x_search` rewrite of Automation lists are not part of this flag; `web_search` / `fetch_page` belonging to the active search connector is. There is no migration: the carried `core:` decisions are read at call time.

# A tool's undecided default follows what it does — a read runs, a change or a send asks — for built-in and connector tools alike

Supersedes in part: [2026-08-13-mcp-permission-config-is-the-single-authority](2026-08-13-mcp-permission-config-is-the-single-authority.md)
(the rule "every connector tool defaults to `needs_approval`, read or write") and
[2026-08-17-read-shaped-exemption-survives-connector-tagging](2026-08-17-read-shaped-exemption-survives-connector-tagging.md)
(the hand-kept `GATE_EXEMPT_READ_SHAPED` allowlist). External MCP tools keep the former rule.

## Context

Two rules, each defensible alone, had stacked into something nobody could explain:

1. **Every connector-owned tool defaulted to `needs_approval`, read or write** (2026-08-13, "no
   tiering by tool type inside a connector"). A freshly connected Tavily asked before its first
   search, Plaid asked before a status read, CoinGecko asked before a price lookup. A built-in
   read (`get_net_worth`) ran freely; the same kind of call through a connector did not.
2. **`GATE_EXEMPT_READ_SHAPED`**, a 14-name list, patched the worst of it (owner incident
   2026-07-21: "why is fetch_emails required permissions? reading your own inbox is a read").
   Those tools carried `mutates=` only because they record a last-synced stamp, a raw-evidence
   snapshot or a memoized result *as a consequence of reading* — and the freshness tag was doing
   double duty as the approval criterion, so a hand-kept name list had to undo it. The owner's
   verdict on that list: the write is "a consequence of the read itself", "treat them as normal
   reads", "we shouldn't expose technical implementation to users", "I don't think we should
   hard code them".

The owner's decision (final): **reads run, anything that changes something asks, every tool stays
editable.** Connecting or enabling a connector is the access grant; asking again for every look
adds nothing. And the control must stay uniform: the owner can still set ANY tool — a read as much
as a write — to Needs approval or Blocked, and that stored level outranks the default and every
chat/Automation mode.

## Decision

1. **One default rule for every registry tool.** `services.tool_permissions.undecided_level(tool)`
   (`ai.tools._confirm_gate.default_level`): `needs_approval` if the tool declares `mutates=` or
   is a send out of this install (`email_send_message`), else `always_allow`. A tool's
   `connector=` tag decides WHOSE stored level applies, never what an undecided call does. The
   Connectors ladder (`connectors._decorate`) and `effective_level` read this same function (through
   the `core.tool_registry_seam` inversion), so the page shows the level that will fire.
2. **`refreshes=(...)` on `@tool`** — the freshness-only twin of `mutates=`: "this READ makes a
   screen refresh; it changes nothing the owner would call a change". `registry.mutated_domains`
   still reports both (the `data_changed` broadcast is unchanged); `registry.is_write_tool` — what
   approval reads — is `mutates=` alone. A tool carries one or the other (registration refuses
   both). The 14 tools moved to it; `GATE_EXEMPT_READ_SHAPED` is deleted. Because the tag removes
   a tool's approval default, `tests/test_tool_domains.py::REVIEWED_REFRESHING_READS` pins the
   members and a second test pins that they write only bookkeeping domains.
3. **A stored level always wins**, for any tool, in every mode (`_explicitly_asks` already ranked a
   stored *Needs approval* above `auto`/`skip`; `blocked` is refused at dispatch). New tests pin it
   for a core read and a connector read, in chat and in Automation runs. *Superseded in part by
   [2026-10-11-permissions-are-one-value-per-tool](2026-10-11-permissions-are-one-value-per-tool.md): a
   stored Always allow / Block still wins in every mode, but a stored Ask is just Ask — the mode decides what
   it means — and this default is now the SEED of a row every tool has.*
4. **External MCP tools are unchanged**: not in the registry, nothing says what they do, so an
   undecided one is `DEFAULT_LEVEL` (`get_tool_permission`), and no chat mode relaxes it, exactly as before.
5. **`web_search` and `fetch_page` belong to the active search connector.** They call whichever
   provider is active, so a `core:` row of their own could only disagree with the connector's page.
   Their permission key is the active provider's own tool (`<id>_search` / `<id>_fetch_page`),
   derived from the provider registry (`_connector_gate.permission_key`); the REST route and the
   chat "Always allow" write there too, and the connector page's tool descriptions say so.
6. **`x_search` is retired**, not rerouted: it was `web_search` with `(site:x.com OR site:twitter.com)`
   appended. The guidance moved into `web_search`'s description; `tool_renames.RENAMES` records it.
7. **Revision `202610311200`** carries a stored `needs_approval`/`blocked` on `core:web_search` /
   `core:fetch_page` onto every provider's tool that has no row of its own (every provider, because
   "which one is active" is live state a frozen migration cannot pin), drops the three `core:` rows,
   and rewrites `x_search` to `web_search` in Automation `always_allowed` lists. An `always_allow`
   row is not carried (a read already runs).
8. `default_level` stays on `GET /api/connectors/{id}/permissions` and in `get_connector_tools`
   (now with a per-tool `tool_defaults` / `default`), so a client can show "default" beside "set by you".

## Consequences

### Positive
- One rule, one function, no name list; the ladder, dispatch and the Auto judge cannot disagree.
- Web search, price lookups, status checks, bank/broker fetches and email reads stop asking, and a
  template Automation no longer parks on a card for "Lucius checked my bank".
- The Auto judge is only consulted for calls that would otherwise ask, so reads no longer cost a
  model call.

### Negative
- **Security trade-off, accepted by the owner for simplicity.** Web search and page fetch now run
  without a card in Manual chats, and in Auto they are no longer shown to the judge (a read is never
  judged). A search query can carry private text off the machine, and a fetched page is untrusted
  text entering the context. The mitigations that remain are structural (SSRF guard, sanitised
  results, the judge still reading only what the owner typed) plus the owner's own control: set the
  search tools to Needs approval or Blocked on the connector page.
- **Upgrade behaviour:** web search, price lookups and status checks that used to ask stop asking
  unless the owner had stored a level; stored levels are kept.
- A `refreshes=` tag is an approval loophole if misused; the reviewed-set test is the guard.
- `web_search` and `fetch_page` no longer have a row in Settings → Permissions of their own.

### Follow-ups
- Option, not built: treat outbound-text tools (web search, fetch) as *sends* — asking by default or
  judged — if private text leaving the machine proves to matter more than the friction.
- Settings → Permissions can list every tool with its default (`default_level`) beside the stored
  level; it must skip `web_search`/`fetch_page` (`misc.ACTIVE_PROVIDER_VERBS`).

## Alternatives considered
- **Keep connector reads asking, widen the exempt list** — rejected: it is the hand-kept list the
  owner asked us to stop maintaining, and it grows with every connector.
- **Reads can never ask** (a hard floor) — rejected by the owner: the user must be able to control
  any tool; a stored level wins.
- **Judge connector reads in Auto** — rejected: a read has nothing for the judge to veto, and it
  would cost a model call per search.
- **Keep `x_search` as a thin shortcut with a second permission check** — rejected: one control per
  capability; the shortcut was one string appended to a query.
- **Carry a stored restriction onto the active provider only** — rejected: switching providers
  would silently lift it.

## Agent provenance

- **Decided by:** repo owner; implemented by the senior-engineer lane (Claude).
- **Lane / branch:** `reads-run-writes-ask`, stacked on PR #214 (`worktree-per-call-auto-approval`).
- **Alternatives weighed:** widen the exempt list; hard floor "reads never ask"; judge connector reads; keep `x_search`.
- **Driving artifacts:** ADRs 2026-08-13 / 2026-08-17 (superseded in part), 2026-10-10-auto-approval-is-a-per-call-judge.
- **Why this way (one line):** the default should describe what a tool does, the owner's stored level should be the only thing that overrides it, and there should be no list to keep.

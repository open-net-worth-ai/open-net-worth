---
title: The permission config is the single authority — `is_money_movement` and the trusted-server flag are removed
status: Superseded in part by 2026-10-11-tool-defaults-follow-what-a-tool-does
date: 2026-08-13
deciders: repo owner (explicit, repeated, in-session) + commissioned design handoff + implementing Claude agent
---

# The permission config is the single authority — `is_money_movement` and the trusted-server flag are removed

> **Superseded in part** by [2026-10-11-tool-defaults-follow-what-a-tool-does](2026-10-11-tool-defaults-follow-what-a-tool-does.md): the rule that every connector tool defaults to needs_approval, read or write, no tiering by tool type (external MCP tools keep it).

## Context

Outbound external-MCP tool dispatch (`core/mcp_external.py`, `mcp/client_manager.py`) carried
**two** permission mechanisms that sat outside, and above, any per-tool setting:

1. **A server-level `trusted` boolean.** ON meant "run this server's write/action tools with no
   confirm card." OFF (the default) meant every write/action tool raised a confirm card. It was a
   single switch covering every tool a server exposed — no granularity below the server.
2. **`is_money_movement` — a hard floor.** A trade/transfer/payment-shaped tool ALWAYS raised a
   confirm card, *even on a trusted server*, with no override available anywhere in the UI or the
   config. This was deliberate and documented, and `CLAUDE.md` carried an explicit instruction not
   to remove it: *"money movement ALWAYS confirms… Do not sweep it while removing inbound tiers."*

Meanwhile the Connectors redesign established a different, uniform rule for every native connector
(Channels, Data sources, Web): tools are grouped read vs. write **for labelling only**, every tool
defaults to `ask` (Needs approval) regardless of type, and **every tool is configurable** up to
`allow` or down to `block`. No tool type carries a floor the user cannot change.

Those two models are incompatible. MCP tools were the only tools in the app that a user could not
fully configure. The contradiction was not theoretical: `place_order` from a third-party MCP server
behaved differently from a native trade tool for reasons that were an artifact of build order, not
a stated principle.

The owner resolved it explicitly and repeatedly in-session ("the hardcoded exception should go
away", "always rely on the permission config"), and the commissioned design handoff
(`frontend/design_handoffs/connectors_handoff/`, §4 *"Resolved: no money-movement exception"*)
independently specifies the same outcome — reached by the designer without prompting, which is
corroboration rather than an echo.

## Decision

**One authority, no overrides.** An external MCP tool call resolves against exactly one thing: that
tool's entry in the permission config.

- `allow` → fire directly.
- `ask` → return a `proposed_change` (confirm card); write nothing until re-entered with
  `confirmed=True`.
- `block` → refuse.

Nothing else in the code path may alter that outcome. Specifically:

- **`is_money_movement` is deleted**, along with its call sites. A trade/transfer/payment tool is an
  ordinary write tool: it groups under "Write & delete tools" for display, defaults to `ask` like
  every other tool, and can be set to `allow` by a user who decides that is what they want.
- **The server-level `trusted` flag is deleted** from the model, store, API and public view. Its old
  behaviour is now expressible — and more precisely — as the group-level "Always allow" stamp, which
  writes `allow` onto each tool individually rather than acting as a hidden layer above them.
- **The cautious default is unchanged and is the actual safety mechanism.** Every discovered tool
  starts at `ask`. What changed is that `ask` is now a *default* rather than a *ceiling*.

`CLAUDE.md`'s "Security-critical areas" section is updated in the same change to state this, rather
than being left contradicting the code.

## Why this is defensible, and what risk is genuinely accepted

The removed floor protected against one scenario: a user turns on `trusted` for a server, does not
realise it covers money-moving tools, and a prompt-injected or malicious tool call moves real money
without a confirmation. The floor made that specific mistake unreachable.

What replaces it is narrower but not nothing:

- **The blast radius of a single mistake is smaller than before.** Under the old model, `trusted` was
  one click that silenced confirmations for *every* non-money tool on a server at once. Under the new
  model there is no server-wide silencer — reaching "no confirmation on `place_order`" requires
  either setting that specific tool to `allow`, or deliberately stamping the whole Write & delete
  group. Both are explicit, both are visible in a grid that shows exactly which tools are at which
  level, and neither happens as a side effect of wanting something else.
- **Every other defence is untouched.** Fail-closed dispatch, nonce-bound apply (approval fires
  server-STORED args, so approve-then-substitute remains structurally impossible), the UNVERIFIED
  fencing on third-party tool output, and discovery gating all remain exactly as they were. The
  reason those exist — untrusted third-party output re-entering the model — is unchanged by this
  ADR, and this ADR does not weaken any of them.
- **The accepted risk, stated plainly:** a user can now configure a third-party MCP server's
  money-moving tool to execute without confirmation. If they do, and that server is malicious or
  compromised, money can move without a per-call human click. That is a real, permanent consequence
  of this decision, accepted knowingly on the grounds below.
- **`allow` applies on unattended surfaces too — deliberately.** A security review raised that an
  Automation (or a headless/skill run) can dispatch an external MCP tool with no human present, so
  an `allow` money-moving tool can fire with nobody watching at all — a wider case than "a watching
  user chose not to be asked". The owner considered this and **kept `allow` uniform**: degrading it
  to `ask` on unattended surfaces would reinstate exactly the unconfigurable floor this ADR removes,
  one layer down, and would make an owner-set permission silently mean different things depending on
  who is looking. A user who does not want unattended execution has two levels that express that
  precisely (`ask`, `block`). This widens the accepted risk above rather than changing its shape:
  the mechanism is still the owner's own explicit per-tool choice, and there is still no path by
  which anything other than that choice decides whether a call fires.

**Why accept it:** this is a self-hosted, single-operator application. The operator owns the server,
the credentials, the data and the machine. A floor the owner cannot lift is not a security boundary
against an attacker — an attacker with enough access to matter can edit the code — it is only a
constraint on the owner. The owner has judged that an unremovable constraint on themselves is the
wrong trade, and that the honest design is a cautious default plus full configurability, applied
uniformly so there is exactly one model to understand rather than one model with an undocumented
exception. Reserving a special unconfigurable case for MCP while native trade tools (§B6's planned
Alpaca work) would be fully configurable would also have inverted the actual trust gradient: the
native path we operate would be *more* permissive than the third-party path we don't.

## Consequences

- MCP joins the same permission model as every other connector; there is now one ladder in the
  codebase, not two, and no tool anywhere carries an unconfigurable floor.
- `trusted` disappearing is a **breaking change to the MCP server model/API shape**. Existing servers
  with `trusted: true` must be migrated — the honest migration is to *not* silently convert it into
  blanket `allow` (that would auto-grant money-movement permissions the user never explicitly gave,
  which is precisely the mistake this ADR claims the new model prevents). Migration drops the flag
  and leaves every tool at the `ask` default; a user who wants the old no-confirm behaviour re-grants
  it deliberately, per tool or per group.
- A `/security-review` gate is required on the implementing change — this ADR records a decision, not
  a verification that the implementation is correct.
- If the accepted risk above ever stops being acceptable (e.g. the app grows a multi-user or hosted
  deployment mode, where "the operator owns everything" stops being true), this decision must be
  revisited *before* that mode ships — the reasoning is explicitly conditional on single-operator
  self-hosting.

## Agent provenance

Decided by the repo owner, stated explicitly and repeatedly in-session, overriding a standing
`CLAUDE.md` instruction that said the opposite. The implementing agent flagged the contradiction
before writing code rather than silently complying, and the owner confirmed. The commissioned design
handoff specifies the same outcome independently (§4). This ADR was written by the orchestrating
agent to record the reasoning and the accepted risk; the code change is implemented in a separate
lane and gated on `/security-review`.

## Amendment (2026-08-14)

The three levels named throughout this ADR are renamed in the permission-store merge: `allow` →
`always_allow`, `ask` → `needs_approval`, `block` → `blocked`. The decision itself is unaffected —
same three-state ladder, same uniform cautious default, same "no unconfigurable floor anywhere"
rule, only the spellings changed (the new names say on their face what the old ones required a
legend to remember). More significantly, "the permission config is the single authority" stops
being an MCP-specific claim: `tool_grants`, the OTHER store this codebase used to answer "may this
tool run" for every non-connector tool, is absorbed into the same table (`tool_permissions`,
revision `0040`) and deleted, so this ADR's title is now true of every tool in the app, not only
MCP's. See ADR `2026-08-14-one-permission-store-for-every-tool`.

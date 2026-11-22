---
title: "Talk to Lucius" is a third MCP access mode — the app talks to Lucius, Lucius acts under the owner's permissions
status: Accepted
date: 2026-11-22
deciders: orchestrating agent (Claude) + repo owner
---

# Talk to Lucius is a third MCP access mode

## Context

- Hosted AI apps (claude.ai, Claude mobile and Desktop connectors, ChatGPT) connect to the book
  through the app's own OAuth server (flag `mcp_oauth`, ADR
  `2026-11-16-hosted-ai-apps-connect-to-mcp-through-the-apps-own-oauth-server`). The owner grants
  **Read** or **Read and change**; `mcp.adapter.tool_allowed(name, scopes)` is the one scope seam.
- Both modes hand the app the book itself. Read exposes every read tool (accounts, transactions,
  SQL, past conversations). Read and change runs every tool **with no approval cards**: a tool set
  to Ask runs straight away for an app (`docs/product/operations/connect-ai-apps.md`, "What a
  connected app can do"), because inbound MCP cannot render a card.
- People now shop and pay through their own agents (agentic commerce: UCP, ACP, AP2, x402,
  card-network agent tokens — `docs/design/spend-advisor/EXPERIENCE.md`). What such an agent
  needs from Open Net Worth is Lucius's judgment ("should I buy this, can I afford it, log it"),
  not raw data — and the owner needs every change it leads to to follow their own permission
  rules.
- Neither ChatGPT nor Claude supports A2A (late 2026); both support MCP, and both support **MCP
  Apps** (official MCP extension since 2026-01-26), which lets a server render interactive UI —
  e.g. an approval card — inside the chat.
- A headless Lucius turn (`core.headless_turn`) binds no approval mode and cannot render a card,
  so the permission gate lets an Ask-level tool **run** there (`_confirm_gate_v2`, "this surface
  cannot show a card; it runs"). Asking Lucius through a headless turn therefore either needs a
  tool ceiling or runs Ask tools unapproved. The owner rejected a ceiling: Lucius keeps his tools.
- The owner rejected narrow per-purpose bundles (a "Spending advice only" scope was built and
  reverted, 2026-11-20): too many choices on a consent page. "Talk to Lucius" is not a slice of the
  book but a different relationship, so it is one mode, not a family.

## Decision

We will add a third MCP OAuth access mode, **Talk to Lucius**, beside Read and Read and change:

1. **The app can call one tool, `ask_lucius(question, context?, conversation_id?)`.** No other
   tool, resource or prompt is reachable with this grant (`tool_allowed`, and the resources path
   takes the caller's scopes).
2. **Each ask is a real, durable Lucius conversation**, marked as coming from the app ("from
   Claude"), visible in the owner's Lucius history; `conversation_id` continues it. An app can only
   continue conversations it started.
3. **Lucius has his normal tools — no ceiling.** Every call goes through the owner's permissions
   exactly as in their own chats: Blocked is refused, Always allow runs, **Ask is decided by the
   conversation's approval mode, Auto (the Auto judge) by default**. Only the owner sets that
   mode, never the app.
4. **An Ask the judge sends to the owner becomes an approval card**, never a silent run: the
   surface for these conversations can render cards. The card is shown
   - **inside the app's chat via MCP Apps** where the client supports it, for ordinary approvals;
   - **in Open Net Worth** (a signed-in page the in-chat card links to, the app, Telegram, push)
     for anything above the owner's hard limits or a never-rule, and for clients without MCP Apps.
5. **The call streams.** It stays open while Lucius works, sending MCP progress messages as he
   goes, and ends with his answer — or, if an approval is pending, with his answer so far and
   what is waiting, plus `conversation_id`; calling again with it returns the outcome.
6. **Read and change** grants and the API token may also call `ask_lucius`; **Read** grants may not
   (an ask can lead to changes).
7. Kept regardless of mode: callable only over inbound MCP (no Lucius-to-Lucius loop), the app's
   text fenced as data, refused on the public demo, every change checkpointed and undoable.

## Consequences

### Positive
- The safest way to connect an agent: it never sees raw accounts, transactions or SQL, and every
  change follows the owner's rules, with cards for Ask — closing the "no approval cards for apps"
  gap for this mode.
- Agents get judgment (memory, goals, bills, cards) instead of data, in Lucius's voice.
- Approvals can happen where the user already is (MCP Apps), with Open Net Worth as the fallback.
- One mode, one tool: nothing new to learn on the consent page beyond one choice.

### Negative
- A model call per ask: cost and latency (seconds), on the owner's AI budget.
- An in-chat card's tap reaches Open Net Worth over the app's own connection, so Open Net Worth
  cannot by itself prove the tap was the owner's and not the model; hence high-stakes approvals go
  through an Open Net Worth-authenticated step.
- More moving parts than a tool: a conversation surface, card relay, streaming.

### Follow-ups
- Build: the `talk` grant scope and consent choice; `ask_lucius` on a durable conversation with the
  approval mode bound (Auto default); the card-capable surface; MCP Apps card + signed-in
  approval page; Telegram/push relay; streaming progress on `/mcp` (today it answers with one
  plain reply); Settings → MCP shows the mode.
- Verify in the MCP Apps spec how a UI action is invoked and whether the model can trigger it,
  before using in-chat cards for anything beyond ordinary approvals.
- Book the turns' cost under an `mcp` surface so the owner can see what apps cost.
- Security review before shipping (MCP inbound is a security-critical area).
- Update `docs/product/operations/connect-ai-apps.md` and the stale "MCP inbound: Bearer-only"
  line in `CLAUDE.md`.

## Alternatives considered
- **A headless `ask_lucius` with a read-only, no-internet tool ceiling** — built uncommitted and
  security-reviewed; rejected by the owner: Lucius should keep his tools.
- **Headless with no ceiling** — rejected: no approval mode is bound and no card can render, so
  Ask-level tools would run with neither the Auto judge nor the owner.
- **Narrow per-purpose scopes ("Spending advice only", "Add transactions", …)** — rejected by the
  owner: confusing on a consent page; built once and reverted.
- **A2A** — rejected for now: neither ChatGPT nor Claude supports it; MCP reaches both today.
- **Ask Lucius only from Open Net Worth's own channels** (Telegram, app) — rejected as the only
  path: agents shopping on the user's behalf need to ask Lucius themselves.

## Agent provenance
- **Decided by:** orchestrating Claude agent, with the repo owner choosing the mode and Auto as
  default.
- **Lane / branch:** `docs/spend-advisor-prd` in worktree `.worktrees/spend-advisor`.
- **Alternatives weighed:** headless with/without ceiling, narrow bundles, A2A, Open Net Worth
  channels only.
- **Driving artifacts:** `docs/design/spend-advisor/PRD.md`, `docs/design/spend-advisor/EXPERIENCE.md`,
  ADR `2026-11-16-hosted-ai-apps-connect-to-mcp-through-the-apps-own-oauth-server`, the two
  security reviews of the spending-advice and headless `ask_lucius` work (2026-11-20/21).
- **Why this way (one line):** an agent should talk to the owner's money manager, not hold the
  owner's books — and Lucius acting under the owner's own permissions is the only version where
  "no ceiling" is safe.

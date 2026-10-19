---
number: 2026-10-11-permissions-are-one-value-per-tool
title: Permissions are one value per tool — Always allow, Ask or Block, seeded with a meaningful default; the mode only decides what Ask means
status: Accepted
date: 2026-10-11
deciders: repo owner (explicit, in-session) + senior-engineer lane (Claude)
---

# Permissions are one value per tool — Always allow, Ask or Block, seeded with a meaningful default; the mode only decides what Ask means

Supersedes in part: [2026-10-10-auto-approval-is-a-per-call-judge](2026-10-10-auto-approval-is-a-per-call-judge.md)
(the "floor comes back as a setting" section — never shipped — and "a stored `needs_approval` outranks the
mode"), [2026-10-11-tool-defaults-follow-what-a-tool-does](2026-10-11-tool-defaults-follow-what-a-tool-does.md)
(decision 3, "a stored level always wins in every mode"; the default rule itself stands, as the SEED rule),
and the YOLO switch of [2026-08-14-one-permission-store-for-every-tool](2026-08-14-one-permission-store-for-every-tool.md).

## Context

Permissions had grown four overlapping notions of "what happens when Lucius wants to use a tool":

1. a stored level (`always_allow` / `needs_approval` / `blocked`) — but only for tools the owner had touched;
2. an *undecided default* computed in code (read runs, change asks, MCP asks) — visible nowhere;
3. a blanket **YOLO** row (`(core, "*")`) that silently turned every undecided Ask into Allow;
4. a mode (manual / auto / skip) that sometimes outranked the stored level and sometimes was outranked by it
   (`_explicitly_asks`: a *stored* Ask beat Skip and Auto, a *seeded* Ask did not — two meanings of the same word).

An Automation additionally "never inherited" a global Allow, so a globally allowed write still paused in a
`manual` Automation. The owner's verdict: **"one only — a meaningful default seed, and the user can modify the
values."**

## Decision

1. **Every tool has exactly ONE value**, stored `always_allow` / `needs_approval` / `blocked` and labelled
   **Always allow / Ask / Block**. Every tool has a real row. There is no "undecided" state on any screen.
2. **Rows are seeded** with a meaningful default — a read (`refreshes=` counts as a read) is Always allow, a
   write is Ask, an external MCP tool is Ask — by ONE function,
   `services.tool_permissions.seed_level`. It is what a fresh row is seeded with, what Reset restores, and what
   dispatch falls back to for a registry tool that has no row yet (a test, a tool added at runtime): a missing
   row is never read as permission and never as a refusal.
3. **Each row remembers whether it is still the seed** (`source: "seed" | "user"`, never shown). The boot sync
   (`sync_seeds`) inserts missing rows, moves a still-seed row to a changed seed rule, removes a seed row whose
   tool no longer exists, and never touches a user row (a row with no `source` reads as the owner's). Reset
   writes the seed value back and marks the row a seed again. External MCP tools are seeded Ask when discovered
   (`seed_discovered_tools`, from `McpClientManager`) and at boot from the servers' cached `discovered_tools`.
   `web_search` / `fetch_page` get no row (they run the active search provider's own tool); a connector withdrawn
   by policy (`DISABLED_CONNECTORS`) gets none either. An empty registry catalog means "cannot say" and prunes nothing.
4. **The mode decides only what Ask means** — manual: a confirm card; auto: the Auto judge decides (allow runs;
   ask / error / timeout is a card); skip: it runs. **Always allow runs and Block is refused in every mode, in chat
   and in Automations alike.**
5. **The order of checks.** Chat: Block → refused; Always allow → runs; Ask → the conversation's mode.
   Automation run: Block → refused; this Automation's **Blocked** list → refused with a clear tool result ("not
   allowed in this Automation"; the run continues without it); its **Always allowed** list → runs; Always allow →
   runs (an Automation now inherits a global Allow, matching CLAUDE.md "always_allow means allow everywhere,
   including unattended"); Ask → the Automation's mode.
6. **No hardcoded exception.** The one rule this ADR first kept — `create_automation` / `update_automations` /
   `delete_automations` / `run_automation` (`AUTOMATION_POLICY_TOOLS`) never judged in `auto` — is deleted, on the
   owner's principle "don't have any exceptions". Those four are seeded Ask and follow the mode like every other
   tool: manual a card, auto the judge, skip runs. The protection is INFORMATION, not a rule: when the call being
   judged creates an Automation, widens one's mode, rewrites the instructions, schedule or status of one that already
   runs in Auto/Skip (what it says becomes the standing request every unattended run is judged against), or runs
   one, the judge's prompt carries a trusted, server-written
   `Consequence:` line outside the fences ("This call grants an Automation unattended access: in Skip it will run
   every tool without asking."). It is derived from the call's structured arguments and the target's STORED mode
   (`auto_judge_prompt.automation_consequence`; an omitted mode on create is the service default, `auto`), never from
   any text a model or server wrote, and a lookup failure says so instead of staying silent. The judge's system prompt
   gives no category of tool an automatic "ask": a credentials, settings, Automation or permission change is judged on
   intent like any other — allow only when the owner's own words clearly ask for exactly that change, ask when the
   request was general, the change goes further than asked, or it widens unattended access without them plainly
   asking. Reason it is acceptable: the judge is one layer among the owner's own Block / Ask settings and Manual mode,
   and the eval corpus carries an "owner asked for a Skip Automation → allow" case and an "untrusted text asked for it
   → ask" case.
7. **Removed**: `_explicitly_asks` (a stored Ask outranking the mode), the bulk-tools floor of PR #214
   (`delete_transactions_where` / `apply_payee_rules` stored as Ask — revision `202610111200`, never shipped, deleted
   rather than reversed; those two are now judged like any other call in auto), the YOLO switch and its UI/API,
   and the "Needs approval" wording (the UI says **Ask**).
8. **YOLO migration.** An install with the `(core, "*")` row at Always allow gets every tool that would be seeded
   Ask written as the owner's own **Always allow** (`source: "user"`), then the row is deleted; an explicit Ask or
   Block the owner had set is kept (YOLO never overrode those); external MCP tools were never covered and stay Ask.
   This runs in `sync_seeds`, not in an Alembic revision: only the boot sync can see every tool (a revision must
   not import `ai`), and the branch goes dormant the moment the row is gone. Revision `202610201300` stamps every
   pre-existing row `source: "user"` (nothing but the owner wrote rows before this).
9. **Automation data.** `always_allowed` entries (`{src, desc, capability, tools}`) are unchanged and keep working;
   the Blocked list is a sibling `blocked: [tool names]` field (no renamed tool parameter). A tool is on at most one
   list (validated). The Automation page's **Tool permissions** section edits both. The confirm card has exactly three
   actions with three distinct outcomes: **Approve** (this call once; label "Approved"), **Always allow** (runs it and
   stores the permission — chat: the global row; Automation run: that Automation's Always allowed list, routed from
   `session_id`; label "Always allowed") and **Deny** (this call only, nothing stored, Lucius is told the owner
   declined it; label "Denied"). The answer is stored — `resolution` on the answered card, and server-side per conversation
   (`services.tool_resolutions`, on the session meta, written by `POST /api/chat/{id}/resolve` only for a call that is
   really waiting) — and `assistant.rehydrate_messages` rebuilds every answered card with its label, so the label is the
   same after a reload and when an Automation run's conversation is opened later (a first cut kept it only in the
   browser, which a reload replaced with the server transcript). A first cut gave the card a fourth action, **Block here**; it is removed on the
   owner's decision — a Block is set only deliberately (Settings → Permissions, a connector's page, the Automation's
   Tool permissions), never from a card. The staged card still carries the Automation's id so Always allow knows it is
   inside a run.
10. **The seed rule reads DECLARATIONS, never tool names.** A write whose `mutates=` domains are ALL workspace
    domains — Lucius's own notebook, not the owner's book: `ai._confirm_gate.WORKSPACE_DOMAINS` =
    `memory skills artifacts notification_log`, declared once per domain — is seeded **Always allow** (remembering a
    fact, saving a skill, creating an artifact, marking notifications read). Any write that touches another domain,
    including one mixed with a workspace domain, is **Ask**. A tool that acts outside the owner's data declares
    `external_effect=True` on its decorator and is **Ask** whatever it writes: `email_send_message`,
    `telegram_send_message` and `webhook_send_message` (the last two write `notification_log`, a workspace domain,
    so the flag is what makes them ask), `run_script` and `run_code`. A single boolean was chosen over another
    `mutates=` domain (`("sandbox",)`) because running code is not a data write and must not claim a domain a view
    would refetch; over a name list because that is the drift this ADR removes. `EXTERNAL_EFFECT_TOOLS` is deleted.
    `save_code_as_skill` was mis-filed as read-only (it creates a skill record and its bundle) and is now
    `mutates=("skills",)`, so it is seeded Always allow and still shows its source on the card if the owner sets it
    to Ask. An existing install's still-seed rows move to this rule at boot (item 3); the owner's rows never do.
11. **`run_code` has no second permission.** It used to stage its own card on "the first run in a conversation" and
    honour a per-conversation grant and an instance-wide `app_config.code_execution_auto_run` flag, a hidden
    permission the Permissions list could not show. All of that is removed: `run_code` is an ordinary tool seeded Ask
    (a card in Manual, the judge in Auto, runs in Skip) and the owner sets it to Always allow or Block in Settings.
    Revision `202610201400` removes the flag and, where it was on, stores `core:run_code` Always allow as the owner's
    own (`source: "user"`), so nobody's chosen behaviour changes. The card is the ordinary tool-permission card,
    which renders the script and purpose it is about to run. **Kept:** the `code_execution_enabled` kill switch
    (Settings → Developer), checked in the handler on every call and again on redemption, because it is the feature's
    on/off rather than an approval layer: Always allow on a switched-off feature still refuses. The tool declares the switch
    (`unavailable=` on its `@tool`, also used by `run_script` for its two script switches), so `registry.dispatch`
    refuses a fresh call with the "code execution is off" result BEFORE the Auto judge and before any card; the
    handler keeps the same check, which refuses an approval redeemed after the switch was turned off.
12. **External MCP tools take the same chain.** Their value (seeded Ask) → manual: a card; auto: the Auto judge;
    skip: it runs; Always allow runs and Block is refused in every mode; the running Automation's Blocked and
    Always-allowed lists apply to them under their namespaced name (`mcp__<server>__<tool>`), and the Automation's
    tool picker lists them. The chain is written once (`_confirm_gate.external_decision`, called by
    `assistant._dispatch_external` since external tools bypass `registry.dispatch`); `effective_level` answers an
    external name under its `(mcp:<server>, tool)` row; the manager only carries the decision out (`call` stages,
    `fire_allowed` fires, both re-checking Block). The old exception — "an Ask on a third-party tool is a card in every
    mode" — is removed. The judge's safeguards for third-party text: the tool's NAME and server label go inside a
    `tool-name` fence (they are server-influenced), the description and the parameter schema stay fenced, and the
    facts line never echoes a key the server declared (only a count); the system prompt tells the judge those are
    claims, not facts. Inbound `/mcp` (our tools called by an outside client) is unchanged.
13. **What the audit found and left (owner decisions open).** A sweep of the whole permission path found these
    remaining places where the outcome is not simply value + mode; none is a tool-name exception:
    - **Fail-closed, and now stricter:** the judge failing to answer (including an unpinned CLI model) is a card; an
      error inside the dispatch gate — including an unreadable permission table — now REFUSES the call
      (`gate_failed_result`); it used to run it ungated, or on the seed, which could run a tool the owner Blocked.
    - **A model cannot redeem a card.** `confirm_token` is the server's redemption channel and only ever equals the
      call's own `tool_use_id`; `registry.dispatch` drops any other value, so a model (or an injection) that learned a
      staged id cannot run another conversation's waiting card (found by security review of this change; it
      pre-dated it).
    - **A surface that cannot show a card** (the iOS tap-to-log capture, `core.headless_turn` runs, inbound `/mcp`,
      the REST finding-apply): a stored Ask RUNS there, and `should_gate` logs that it did. Making them refuse would
      stop capture booking anything and every inbound-MCP write; that is the owner's call and is NOT made here.
    - **Feature switches, not approvals:** the `code_execution_enabled` and `script_skills_enabled` kill
      switches (the third, "Unattended script runs", decided by context and is deleted — revision
      `202610201500` drops its stored key, and `core.unattended`, which only it read, went with it), the public-demo refusals (`_DEMO_REFUSED_TOOLS`) and a connector withdrawn by policy
      (`DISABLED_CONNECTORS`).
    - **Routing, not an exception:** `web_search` / `fetch_page` read the active search provider's own row.
    - Removed in the same pass: `GATED_TOOL_NAMES` (an empty name list) and the `GATE_ALL_WRITES` flag.
14. **Surfaces.** `GET /api/tool-permissions` lists every tool (registry catalog joined onto the table, plus MCP
    rows, plus retired-tool rows) with `level`, `default_level` and a description; `DELETE` is Reset. Settings →
    Permissions is searchable, grouped by owner, with a level picker, Reset (only when the value differs from the
    default) and a Changed filter. The Connectors ladder reads the same rows.

## Consequences

### Positive
- One word, one meaning: Ask is the same everywhere, whoever wrote it. The Settings list is the whole truth.
- The owner can see and change every default instead of discovering it by being asked (or not).
- An Automation behaves like a chat for Allow and Block, and gains its own per-tool say.

### Negative
- **A bulk delete is judged like any other call in auto, and runs in skip** — the floor that made them ask in every
  mode is gone (it never shipped). Skip's label ("never pauses") is now literally true for them. The owner who wants
  them to always ask sets them to Ask and uses Manual, or relies on the judge, which is told to ask on a bulk delete.
- **`always_allow` in an Automation now runs** where a `manual` Automation used to pause on a globally allowed write.
  This is the intended meaning of the level, but it widens what a previously-set global Allow does in Automations.
- A table of a few hundred rows instead of a handful; the hot path is still a primary-key read.
- A `skip` conversation/Automation now runs a tool the owner *stored* as Ask (it used to ask). Per the owner: Ask
  means the same everywhere.

### Not changed (deliberately)
- **External MCP tools** are still dispatched outside the registry (`mcp/client_manager`), but they no longer
  differ in outcome: see item 12. Their rows are seeded and
  editable like any other.
- **The AI cannot write an Automation's lists.** `update_automations` still refuses `always_allowed` / `blocked`
  (`_AI_UPDATABLE_FIELDS`): the lists change only through the owner's UI/REST and through a card tap. Widening that is
  a security decision with its own ADR.
- The inbound MCP surface (`/mcp`, Bearer = the operator) is not card-capable, so an Ask there runs and a Block is
  refused, exactly as before.

### Follow-ups
- Automation `always_allowed` / `blocked` lists for external MCP tools (the run resolves its lists against the
  registry; MCP tools are not in it, so the picker does not offer them).
- Letting Lucius propose list changes through `update_automations` (it would now be judged in auto like any other
  call; the AI still cannot write the lists).

## Alternatives considered
- **Keep "undecided" and show the computed default** — rejected by the owner: two states for one question.
- **A migration seeds the rows** — rejected: seeding needs the live registry, and `db/` must not import `ai/`;
  the boot sync is the layer that can see it (the same inversion as the other registry seams).
- **Keep YOLO as a row the sync honours forever** — rejected: a blanket switch that makes the table lie about
  what will run. Carry it once, as the per-tool values it always meant.
- **Keep the bulk-tools floor as seeded Ask rows** — moot: rows are now seeded Ask for every change, and the
  floor existed only because a stored Ask outranked modes, which is what this removes.

## Agent provenance

- **Decided by:** repo owner (explicit, in-session); implemented by the senior-engineer lane (Claude).
- **Lane / branch:** `permissions-one-value`, stacked on `reads-run-writes-ask` (PR #218) on `worktree-per-call-auto-approval` (PR #214).
- **Alternatives weighed:** computed-default display; migration-time seeding; permanent YOLO; floor-as-seed.
- **Driving artifacts:** ADRs 2026-10-10 / 2026-10-11 above; the owner's spec "Permissions: one value per tool".
- **Why this way (one line):** one meaning of Ask, one seed rule, one table that is the whole truth — and the owner edits values, not rules.

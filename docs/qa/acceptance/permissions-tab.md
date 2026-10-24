# Acceptance — Settings › Permissions

Design of record: `.claude/rfcs/2026-07-20-approval-and-review-model.md` §15.4 (decisions are
visible and revocable), §16.1 (checkpoint-EXCLUDED), §13.3 (`email_send_message` is gated too), as
merged with the Connectors redesign's three-level ladder
(`.claude/rfcs/2026-08-10-connectors-architecture.md` §4/§10) by ADR
`2026-08-14-one-permission-store-for-every-tool`. Until 2026-08-14 this tab was backed by its
own binary store (`tool_grants`, `GET`/`DELETE /api/grants`); it now reads and writes the SAME
table the Connectors detail page's permission ladder does — a decision made either place shows
up here.

**One value per tool (ADR `2026-10-11-permissions-are-one-value-per-tool`).** Every tool has a
row holding Always allow / Ask / Block (stored `always_allow` / `needs_approval` / `blocked`),
seeded with a default at boot (a read runs, a change or a send asks, an external MCP tool asks).
This tab lists ALL of them, searchable and grouped by owner; Reset puts a tool back to its
default. There is no "undecided" row and no YOLO switch.

**Why this tab is load-bearing, not cosmetic:** permissions are checkpoint-EXCLUDED, so a
decision SURVIVES a checkpoint restore. This tab is the ONLY app-wide way to see and change one.
A permission system with no visible state is one a user can only meet by being surprised — which
is exactly how the unattended-write envelope failed on 2026-07-20.

Surface: `GET /api/tool-permissions`, `PUT /api/tool-permissions/{tool}`,
`DELETE /api/tool-permissions/{tool}` (`backend/api/tool_permissions.py`);
`frontend/src/components/tabs/settings/sections/PermissionsSection.tsx`.

---

## PT-1 — The tab exists and is reachable

**GIVEN** the app is open
**WHEN** I navigate to Settings
**THEN** a "Permissions" section appears in the section list
**AND** selecting it renders the Tool permissions panel
**AND** the selection survives a reload (mirrored to `?sub=permissions` in the URL — nothing
is stored locally, so closing and reopening Settings lands at its top level again).

## PT-2 — Every tool is listed, with a value, before the owner has decided anything

**GIVEN** a fresh install (nothing the owner has ever set)
**WHEN** I open Permissions
**THEN** every built-in tool and every connector tool is listed under its owner's heading
(Built-in first), each with a value: reads read "Always allow", changes and sends read "Ask"
**AND** each row shows the tool's readable title, its one-line description, and the raw id only as
a tooltip
**AND** `web_search` and `fetch_page` are NOT rows (the active search provider's own tools are)
**AND** the count beside the title is the total number of tools listed
**AND** no row has a Reset button (each reads "Default").

## PT-3 — A row shows its value and changes it in place

**WHEN** I pick "Block" on a row's selector
**THEN** `PUT /api/tool-permissions/{tool}` is sent with `{level: "blocked", connector_id}`
**AND** a toast confirms it takes effect on the next call
**AND** the row now shows a Reset button.

The selector offers exactly **Always allow / Ask / Block** — never "Needs approval" or "Blocked".

**GIVEN** a decision made through a CONNECTOR's own permission ladder (e.g.
`plaid_fetch_transactions` under owner `plaid`, or a tool under `mcp:<server_id>`)
**THEN** it shows here too, under that owner — one table.

## PT-3b — Search and "Changed"

**WHEN** I type in "Search tools…"
**THEN** the list narrows by title, raw id, description or connector name, and an empty result
reads "No tools match"
**WHEN** I press "Changed (n)"
**THEN** only rows whose value differs from the default (plus retired tools) remain.

## PT-4 — Reset returns the tool to its default value

**GIVEN** a row whose value differs from its default
**WHEN** I press Reset
**THEN** `DELETE /api/tool-permissions/{tool}?connector_id=…` returns 200
(`{"reset": "<tool>", "level": "<default>"}`)
**AND** the row shows the default value and "Default" again, without a full-page reload
**AND** the row is a seed again, so a later change of the app's default reaches it.

## PT-5 — A failed write is reported inline, not swallowed

**GIVEN** the clear/set request fails (server down / 404)
**WHEN** I act on a row
**THEN** an inline error appears under that row
**AND** the row stays showing its last-known-good level (no optimistic value left stranded)
**AND** other rows are unaffected.

## PT-6 — A decision for a renamed-away tool is shown, not hidden

**GIVEN** a stored decision whose tool name no longer resolves in the registry
**WHEN** I open Permissions
**THEN** the row still renders (`tool_exists: false` from the API)
**AND** carries "No longer available — this tool was renamed or removed, so this decision is
inert."
**AND** clearing it still works — the request carries the row's own `connector_id` as a query
parameter, since a stale name can no longer be resolved back to its owner.

Rationale: a decision does NOT survive a rename (RFC §15.1) and the user is silently asked
again. The RFC accepts that silence at the *prompt*; it does not require it *here*. Hiding the
row would leave a stored permission the user can neither see nor clear.

## PT-7 — There is no blanket "confirmation off" switch

**GIVEN** an install that had the retired YOLO row armed
**WHEN** the app boots
**THEN** every tool that would have been seeded Ask is Always allow (the owner's own value), the
`(core, "*")` row is gone, and nothing starts asking that did not before
**AND** tools the owner had explicitly set to Ask or Block keep that value
**AND** no "Confirmation is off" panel exists in this tab.

## PT-8 — Loading and error states

**WHEN** the tab is opened and the request is in flight
**THEN** a "Loading tools…" placeholder renders (never a "No tools match" flash).

**WHEN** `GET /api/tool-permissions` fails
**THEN** an ErrorState renders with a working Retry
**AND** Retry re-issues the request.

## PT-9 — The level semantics are stated on screen

**GIVEN** at least one stored decision
**THEN** footer text states that a level covers the whole tool — every amount, every account,
every argument
**AND** that "Always allow" fires even when nobody is watching (an Automation, a headless run)
— there is no surface-dependent softening of it
**AND** that decisions are not reverted by restoring a checkpoint, so this list (and each
connector's own ladder) is the only place they change.

Rationale: §13.3b — a level has no bounds beyond the tool itself. That is intended and
understood, which means it must be *stated*, not left to be discovered.

---

## Verification status

Backend: `tests/test_tool_permissions_api.py`, `tests/test_tool_permission_seeding.py`,
`tests/test_permission_order.py`. Frontend: `PermissionsSection.test.tsx`. Browser QA of PT-1 …
PT-9 against the merged build (a scratch `ONW_ROOT` with `ONW_QA_MODE=1`, a port in 8090–8099)
is recorded in the pull request that introduced the one-value model.

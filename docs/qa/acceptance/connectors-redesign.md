# Acceptance criteria — Connectors redesign (design-handoff port)

**SHIPPED / VERIFY-GATE CLOSED** — 2026-08-16, QA verified code-complete with architecture inspection and API validation. All 86 criteria accounted for in implementation; pre-existing PARITY_AND_GAPS divergences owner-accepted; feature live on main.

**2026-08-17 follow-up (Pause/Resume/Remove rework, implementation complete — QA/qa-e2e PENDING):**
the primary action's labels and the Remove flow changed — see §3a below, which supersedes
`CNR-DETAIL-PRIMARY-ACTION`, `CNR-DETAIL-CONNECT-REARMS`, `CNR-DETAIL-EDIT-WHILE-CONNECTED` and
`CNR-DETAIL-REMOVE-ACTUALLY-REMOVES`'s last bullet below (all four are struck through inline
rather than deleted, so the change is traceable). Status is earned by evidence per CLAUDE.md — the
new criteria are unit/characterization-tested (backend `pytest`, frontend `vitest`) but have not
yet been run through `make qa-e2e`/`make ui-gate` against the live app.

Interaction-level GIVEN/WHEN/THEN for the **rebuilt** Connectors tab, ported from
`frontend/design_handoffs/connectors_handoff/`. The QA agent must exercise every criterion
against the RUNNING app and report PASS/FAIL per ID. "It renders" is not a pass — every
control gets clicked/typed/submitted and its outcome asserted.

Supersedes `connectors.md` for the surfaces it covers (list, detail, permissions, directory,
MCP add flow). That file's criteria for the OLD card-grid/Configure-button interaction model
no longer describe the shipped UI.

Spec section references (`§N`) point at `connectors_handoff/index.html`.

---

## 1 · Four lanes (§1)

### CNR-LANES
- GIVEN I open the Connectors tab
- WHEN it loads
- THEN I see four family sections — Channels, Data sources, Web, MCP — each with its own
  filter tab, and no JS error and no blank screen.

### CNR-LANES-MEMBERSHIP
- GIVEN connectors exist across families
- WHEN I read each section
- THEN membership is by `families` ARRAY, not a single primary family: a connector belonging
  to two families appears under BOTH sections, and the filter tab tests membership the same way.

### CNR-LANES-EMPTY-HIDDEN
- GIVEN a family has no installed connectors
- WHEN I view the unfiltered list
- THEN that family renders no section (it does not show an empty header).

### CNR-LANES-EMPTY-SELECTED
- GIVEN a family with nothing installed (MCP on a fresh install)
- WHEN I select that family's filter tab
- THEN I get a real empty state with a **Browse & add** action — NOT a bare page, NOT a blank
  panel.
- AND the empty-state title uses the section's human-readable name (**"MCP"**, "Data sources"),
  never the raw key — the string "No mcp connectors" is a FAIL.
  *(Regression guard: this exact copy bug shipped in the outgoing tab.)*
- AND its Browse & add action opens the directory **pre-filtered to that family**, not the
  unfiltered catalog.
  *(Regression guard: the outgoing tab dumped the user into the full catalog.)*

### CNR-STATUS-FILTER
- GIVEN any family tab is active
- WHEN I switch the status filter between All / Connected / Attention
- THEN it applies across the active family, AND **Attention shows only connectors in an actual
  error state** — a connector the user deliberately disabled is NOT surfaced as needing attention.

---

## 2 · The list (§2)

### CNR-ROW-ONE-LINE
- GIVEN a connector with a long name
- WHEN its row renders
- THEN the row is exactly ONE line and the name truncates — it never wraps to a second row.

### CNR-ROW-STATUS-VOCAB
- GIVEN a healthy connector
- WHEN I read its status
- THEN it reads **"Connected"** as plain coloured text with a check-in-circle — no chip, and no
  "Active"/"Connected" split vocabulary.

### CNR-ROW-ONE-ACTION
- GIVEN a healthy connector row and an erroring connector row
- WHEN I compare them
- THEN only the erroring one offers **Reconnect**; the healthy row has no Configure button, no
  enable/disable toggle, and no Test button.
- AND both rows' rightmost content ends at the same x (status+action share one flush-right group).

### CNR-ROW-CLICK
- GIVEN any connector row
- WHEN I click anywhere on the row
- THEN its detail page opens — the row itself is the affordance.

### CNR-ROW-MENTIONS
- GIVEN the connector rows are visible
- WHEN I use the `@`-mention affordance / drag a connector to Lucius
- THEN it still works — the redesign must not silently drop the mention + drag-to-Lucius
  capability the outgoing tab shipped.

### CNR-SECTION-ADD-LINK
- GIVEN any family section
- WHEN I reach its end
- THEN there is an `+ Add a channel` / `a data source` / `a web provider` / `an MCP server` link,
  and clicking it opens the directory pre-filtered to that family.

### CNR-SECTION-ADD-BANK
- GIVEN the Data sources section is rendered
- WHEN I reach its end
- THEN it carries a SECOND link, `+ Add a bank account`, beside `+ Add a data source`
  (the one section with two — "which provider covers my bank" is a question the directory's
  four look-alike aggregator cards cannot answer).
- AND clicking it opens the institution search, NOT the directory.
- AND typing a country + Search lists banks and/or provider options, and choosing one lands me
  in THAT provider's configuration form with the country already filled in.
- AND no result ever dead-ends — the manual-statement-import fallback is always stated.

---

## 3 · Detail page (§3)

### CNR-DETAIL-BACK
- GIVEN I am on a connector detail page
- WHEN I click the title `‹ Connectors`
- THEN it navigates back — it is a control, not a static string.

### CNR-DETAIL-SHAPE
- GIVEN any connector from ANY of the four families
- WHEN its detail page opens
- THEN the page shape is identical across families: identity (icon, name, optional tag, status
  badge, sub-line), primary action, kebab, description, "Developed by", prompt suggestions, and
  the tool-permission ladder.

### CNR-DETAIL-PRIMARY-ACTION ~~(superseded by §3a — see CNR-DETAIL-PAUSE-RESUME-CONNECT)~~
- ~~GIVEN a connected / disconnected / erroring connector~~
- ~~WHEN I look at the primary action~~
- ~~THEN it reads **Disconnect** / **Connect** / **Reconnect** respectively, AND this is the ONLY
  place in the UI a connector can be disabled.~~

### CNR-DETAIL-CONNECT-OPENS-THE-FORM
- GIVEN a connector that holds no credentials yet (Plaid, Telegram, Yodlee, Alpaca, IBKR Flex,
  Enable Banking, a web-search provider, Webhook, Email — reached from the directory, since an
  unconfigured connector is deliberately absent from the list)
- WHEN I click **Connect**
- THEN that provider's own configuration form opens — its REAL fields (Plaid: Client ID +
  Secret + Environment; Telegram: Bot token + Allowed chat IDs; IBKR: Flex token + Flex query
  ID; Email: IMAP host…) — and I can type credentials and Save them.
- AND Save reaches that provider's own endpoint (`/api/plaid/config`, `/api/telegram/config`,
  `/api/channels/email`, `/api/search/config/<provider>`, …) and the connector's status
  changes on the page behind.
- AND Connect does NOT merely flip `enabled` — a connector with no credentials must never end
  up reading "Connected".
  *(Regression guard: the redesigned tab shipped routed and live with Connect wired to nothing
  but `PATCH {enabled}`, so no connector could be configured at all.)*

### CNR-DETAIL-CONNECT-REARMS ~~(superseded by §3a — see CNR-DETAIL-PAUSE-RESUME-CONNECT)~~
- ~~GIVEN a connector that already holds credentials but is disconnected~~
- ~~WHEN I click **Connect**~~
- ~~THEN it re-arms in one click and does NOT re-ask for its key.~~ (still true — the button
  is now labeled **Resume**, not Connect, for this exact case.)

### CNR-DETAIL-CONFIG-IS-A-VIEW
- GIVEN the configuration form is open
- WHEN I look at the dialog
- THEN it is the SAME modal as the detail page, retitled to a `‹ <connector name>` back
  control, with the identity block still stating which connector and its status — not a second
  dialog stacked on the first, and one Escape closes one thing.
- AND its footer is a single **Done** that returns me to the detail page with the status,
  badges and permission ladder refreshed.

### CNR-DETAIL-KEBAB
- GIVEN a connector detail page
- WHEN I open the kebab
- THEN it offers View details · Refresh tools list · Remove connector, plus **Edit connection**
  for any connector that has a setup surface — an MCP server (reopens the add flow) or a native
  connector with a credential form (reopens that form, pre-filled).
- AND `.env`-managed connectors offer NO Edit connection, because every per-provider endpoint
  refuses to write them.

### CNR-DETAIL-EDIT-WHILE-CONNECTED
- GIVEN a CONNECTED native connector whose primary action reads **Pause** (was Disconnect —
  see §3a)
- WHEN I want to rotate its API key
- THEN kebab → **Edit connection** opens its form without pausing it first.

### CNR-DETAIL-EDIT-MCP-PREFILLED
- GIVEN a saved MCP server's detail page
- WHEN I choose kebab → **Edit connection**
- THEN the SETUP DRAWER reopens pre-filled with that server's own connection — URL (or command +
  arguments), transport and authentication as saved — NOT the `Browse & add` directory.
- AND **Server ID is fixed**, shown as a monospace value with NO `customize` link, because it
  namespaces the server's tools and keys every permission row set for them.
- AND **Transport is fixed** (both options disabled, with the reason stated) and the
  "Paste from a config snippet" pill is absent — pasting switches transport.
- AND the permission ladder shows the levels this server ACTUALLY has, with any newly discovered
  tool at Needs approval — nothing is reset.

### CNR-DETAIL-EDIT-SECRETS-NEVER-ECHOED
- GIVEN a saved MCP server holding an API key and/or environment variables
- WHEN its edit form opens
- THEN the API key field is EMPTY with a masked placeholder, the environment box is EMPTY with the
  saved key NAMES listed beside it, and no secret value appears anywhere in the page or the
  network responses.
- AND the form states plainly that leaving each blank KEEPS what is saved, and that Test connect
  therefore runs without the saved credential.
- AND saving with both untouched does not change either — verify via the API that the token still
  works and every env value survives.
  *(Regression guard: the outgoing form PATCHed `{KEY: ''}` built from `env_keys`, silently
  wiping every saved environment value.)*

### CNR-DETAIL-EDIT-TEST-STILL-GATES
- GIVEN the edit form with a URL changed to something unreachable
- WHEN I look at the primary button and then press it
- THEN it reads **Test connect** (never Save), the real connect error shows inline with
  Back/Retry, and **no PATCH is sent** — an unreached server is not saved on an edit either.
- AND after a successful test the button reads **Save changes**, and only then does anything
  persist.

### CNR-DETAIL-REMOVE-CONFIRMS
- GIVEN an MCP server's detail page
- WHEN I choose kebab → **Remove connector**
- THEN a confirmation step appears IN THE SAME modal (retitled `‹ <name>`, not a second dialog),
  naming what is lost — the tools Lucius can no longer reach and every permission level set for
  them — and stating it cannot be undone.
- AND Cancel returns to the detail page having sent nothing.
- AND (§3a) the copy also cross-references **Pause** as the non-destructive alternative.

### CNR-DETAIL-REMOVE-ACTUALLY-REMOVES
- GIVEN the removal confirmation
- WHEN I confirm
- THEN `DELETE /api/mcp-clients/{id}` is called, the detail page closes, and the list refetches
  without that server.
- AND re-adding a server under the same id starts every tool at Needs approval — the backend
  clears its permissions on delete, so no trust is inherited from its predecessor.
- AND if the request FAILS, the real error is shown, the page stays open, and nothing claims the
  connector was removed.
- ~~AND a built-in connector's kebab says removal is not the operation (disconnect is) rather
  than showing a confirmation that would do nothing.~~ **Superseded by §3a** — a native
  connector's Remove connector is now a real operation too (`CNR-DETAIL-REMOVE-NATIVE`).

### CNR-DETAIL-EMAIL-UNIFIED-ACCOUNT (supersedes CNR-DETAIL-EMAIL-TWO-CAPABILITIES, 2026-08-17)
- GIVEN the Email connector's configuration form
- WHEN it opens
- THEN it is ONE continuous screen, not two tabs: an Account section (provider chips, Email/
  username, App password — filling BOTH the IMAP and SMTP credential fields from one entry) is
  followed by Receive (Folder, Initial lookback) and Send (From address, To address); a known
  provider's host/port and TLS mode are set automatically and not shown as editable fields, a
  custom/self-hosted host shows them editable.
- AND there is NO per-form Enable checkbox anywhere on the screen (neither leg) — Email's on/off
  is Pause/Resume/Remove only, exactly like every other connector; whether Lucius may call
  `send_email` is a `tool_permissions` decision (Tool permissions section below), not a second
  on/off bit.

### CNR-DETAIL-PROMPTS
- GIVEN a connector with prompt suggestions
- WHEN I tap one
- THEN it seeds the Lucius composer with that text.

---

## 3a · Pause / Resume / Remove rework (2026-08-17 follow-up)

The pre-existing model conflated "disable" and "remove": Disconnect silently toggled `enabled`
off with no confirmation while leaving every saved credential in place, and Remove connector was
a real, confirmed, credential-clearing operation for MCP servers only — every native connector's
kebab dead-ended in a toast ("X is built in — disconnect it rather than removing it."). This
section makes the two concepts distinct and both real, for every connector family.

### CNR-DETAIL-PAUSE-RESUME-CONNECT
- GIVEN a connector that is configured AND enabled
- WHEN I look at the primary action
- THEN it reads **Pause**, and clicking it opens a confirmation view (same shape as Remove's) —
  ONLY confirming there calls the enable/disable write; a single click does NOT disable it.
- GIVEN a connector that is configured but disabled (credentials already saved, just paused)
- WHEN I look at the primary action
- THEN it reads **Resume** and re-arms in ONE click, no confirmation — mirrors the pre-existing
  Connect re-arm behavior (`CNR-DETAIL-CONNECT-REARMS`), relabeled.
- GIVEN a connector with NO credentials yet (`setupPending`)
- WHEN I look at the primary action
- THEN it still reads **Connect** and opens the credential form — unchanged
  (`CNR-DETAIL-CONNECT-OPENS-THE-FORM`).

### CNR-DETAIL-PAUSE-CONFIRM-COPY
- GIVEN the Pause confirmation view
- WHEN I read it
- THEN it states three things: syncing/notifying/tool-access stops now (worded per connector
  family — e.g. "Scheduled syncing stops" for an aggregation connector, "It stops responding to
  messages and sending alerts" for Telegram, "Lucius loses access to its tools" for MCP), the
  saved credentials and settings are KEPT (resuming won't ask for them again), and Remove
  connector is named as the alternative if the operator wants credentials cleared.
- AND Cancel returns to the detail page having written nothing; only confirming calls
  `PATCH /api/connectors/{id}` with `enabled: false`.

### CNR-DETAIL-REMOVE-NATIVE
- GIVEN ANY native connector's detail page (not just MCP) — e.g. Plaid, SimpleFIN, Telegram,
  Email, Webhook, or a Web Search provider card (Tavily, Serper, …) — **every** connector
  family, with no exceptions
- WHEN I choose kebab → **Remove connector**
- THEN the SAME real confirm-then-remove flow appears (no more dead-end toast), with native
  copy — "This clears your saved credentials and resets its settings — you'll need to set it up
  again from scratch to use it again. This can't be undone." — plus the same cross-reference to
  Pause as the non-destructive alternative.
- AND confirming calls `DELETE /api/connectors/{id}`, which resets that connector's store back
  to its own defaults (every secret genuinely cleared — never just hidden behind an omitted
  field, unlike the ordinary write-only `update()` semantics every config PUT uses) and disables
  it; the detail page closes and the list refetches showing it unconfigured.
- AND for Telegram specifically, a live bot process is torn down (stopped, not merely flagged
  disabled) so it does not keep responding on a now-wiped token.
- AND for Email specifically, BOTH legs clear — the IMAP password (`EmailConfigStore`) and the
  SMTP password (`ChannelConfigStore`'s `"email"` namespace) — since Email is one connector
  card over two stores; Webhook, which shares the same `ChannelConfigStore` row under its own
  `"webhook"` namespace, is untouched by an Email removal and vice versa.
- AND for a Web Search provider, only THAT provider's entry (api key + any non-secret extra
  field, e.g. Google PSE's `cx`) is cleared — a sibling provider configured under the same
  shared `SearchConfigStore` row is untouched — and the provider ends up explicitly disabled
  (not merely reset to an absent entry, which for a key-free default like DuckDuckGo would
  silently re-enable it via its own "enabled by default" fallback).
  *(Regression guard, 2026-08-17: an earlier version of this change scoped Remove to 10
  connectors only and 422'd on these three — a confirm dialog that promises a real removal and
  then errors on confirm, which is worse than the dead-end toast it replaced. See ADR
  `2026-08-17-connector-remove-covers-every-connector-family`.)*

### CNR-DETAIL-TELEGRAM-LIVE-RELOAD
- GIVEN the Telegram connector, currently live (bot process running)
- WHEN I Pause it (confirm), or Resume it, or Remove it
- THEN the live bot process actually stops/restarts in each case — not just the next time the
  whole app restarts. (Regression: previously only `PUT /api/telegram/config` hot-reloaded the
  bot; the generic enable/disable/remove paths flipped the store flag with no live effect until
  the process was restarted.)

### CNR-SECTION-NO-REDUNDANT-ENABLE-CHECKBOX
- GIVEN any of the 10 native connector config forms (SimpleFIN, Plaid, Lunch Money, Lunch Flow,
  YNAB, Enable Banking, Yodlee, Alpaca, IBKR, Telegram)
- WHEN the form is open
- THEN there is NO "Enable scheduled syncing" / "Enable the Telegram bot" checkbox inside it —
  the top-level Pause/Resume button is the only place `enabled` is switched now.
- AND saving the form sends `enabled: <whatever the server last reported>` — NEVER a hardcoded
  `true`. *(Regression guard, security review 2026-08-17, finding 9: an earlier version of this
  change hardcoded `enabled: true` in every form's save body, so editing a credential on a
  connector the operator had deliberately Paused silently RESUMED it — an unannounced side
  effect of a save the operator only meant to update a field. See ADR
  `2026-08-17-connector-remove-security-hardening`.)*
- GIVEN one of these forms on a connector that is configured but currently PAUSED (`enabled:
  false`)
- WHEN the form is open
- THEN it shows an explicit note — "Currently paused — saving here won't resume it. Use Resume
  on the connector card to turn it back on." — so an edit-while-paused isn't silently inert
  either; the operator can tell the form will save without waking the connector back up.

### CNR-DETAIL-TELEGRAM-STOP-CONFIRMED-BEFORE-REMOVE
- GIVEN the Telegram connector, currently live (bot process running), and I choose kebab →
  Remove connector → confirm
- WHEN the live bot process CANNOT be confirmed stopped (teardown raised, or the app could not
  reach it)
- THEN the request fails (502) with an explanation, and the bot's credentials are **NOT**
  cleared — a still-live process must never be left running unreachable with an operator
  believing it was fully removed. Retrying, or restarting the app, is the stated recovery path.
- GIVEN the same flow, but the stop DOES complete successfully
- THEN removal proceeds exactly as `CNR-DETAIL-REMOVE-NATIVE` describes — credentials cleared,
  connector shows unconfigured.

---

## 4 · Permission ladder (§4) — every connector, no exceptions

### CNR-PERM-GROUPS
- GIVEN any connector's detail page
- WHEN I view its tools
- THEN they are grouped into exactly **Read-only tools** and **Write & delete tools** (fixed
  taxonomy, not per-connector custom names), and a connector with no write tools shows ONLY the
  read group.

### CNR-PERM-DEFAULT
- GIVEN a newly discovered tool of ANY kind — including a trade/payment tool on an MCP server
- WHEN I read its level
- THEN it is **Needs approval**. No tiering by tool type, no exceptions, nothing pre-set to
  Always allow.

### CNR-PERM-TOOL-LEVELS
- GIVEN a single tool's control
- WHEN I open it
- THEN it offers exactly three options — Always allow / Needs approval / Blocked. **"Custom" must
  NOT appear at tool level.**

### CNR-PERM-GROUP-STAMP
- GIVEN a group whose tools have mixed levels
- WHEN I pick a real level (e.g. Blocked) on the GROUP control
- THEN **every tool in that group** is overwritten to that level — deliberately lossy.

### CNR-PERM-CUSTOM-DERIVED
- GIVEN a group where every tool shares one level
- WHEN I change ONE tool to a different level
- THEN the group control immediately reads **Custom** — derived from the tools, never stored.

### CNR-PERM-CUSTOM-INERT
- GIVEN a group control showing Custom
- WHEN I explicitly select "Custom" from the menu
- THEN NO tool value changes and the menu simply closes.

### CNR-PERM-PERSISTS
- GIVEN I set several tools to different levels
- WHEN I reload the page
- THEN every level persists exactly as set, and no group-level "custom" value was written to
  storage (only per-tool values exist).

### CNR-PERM-ENFORCED
- GIVEN a tool set to **Blocked**, one set to **Needs approval**, and one set to **Always allow**
- WHEN Lucius attempts each
- THEN Blocked refuses, Needs approval raises a confirm card, Always allow fires directly — AND
  nothing else overrides that outcome, including for a money-moving MCP tool.
  *(This is the `is_money_movement` removal — see ADR
  `2026-08-13-mcp-permission-config-is-the-single-authority`.)*

### CNR-PERM-ENFORCED-EVERY-FAMILY
- GIVEN a NATIVE connector's detail page (Plaid, Email, a web provider) AND an MCP server's
  detail page
- WHEN I open either one's permission ladder
- THEN there is **no** "not enforced yet" note anywhere in the UI — a level set on a native
  connector's tool gates that tool exactly like an MCP tool does, via the same dispatch-time
  read (`ai/tools/_confirm_gate.py::effective_level`).
- AND `/api/connectors` carries **no** `permissions_enforced` field on any row — the flag and
  the `tool_permissions.is_enforced()` predicate it rode on were REMOVED (2026-08-14): there
  is only one answer, "yes", so there is nothing left to report.
  *(Supersedes the former G11 criterion, which asserted a `permissions_enforced: false` row
  for native connectors — true only until the 2026-08-14 permission-store merge. See ADR
  `2026-08-14-one-permission-store-for-every-tool`, and `CNR-PERM-ENFORCED` above for the
  behavioral proof that Blocked/Needs approval/Always allow all take effect regardless of
  family.)*

### CNR-PERM-WRITES-REACH-THE-BACKEND
- GIVEN any connector's ladder
- WHEN I change a tool level and a group level
- THEN the write goes to `PUT /api/connectors/{connector_id}/permissions/{tool}` and
  `PUT /api/connectors/{connector_id}/permissions` respectively, addressed with the connector id
  WHOLE (`mcp:<server_id>`, percent-encoded), and returns 200.
- AND the level that arrives is the one shown — `needs_approval` is sent as `needs_approval`,
  never translated into a retired `allow`/`ask`/`block` or `allow`/`deny` vocabulary.
  *(Regression guard H2: both writers called the DELETED
  `PATCH /api/mcp-clients/{id}/tool-permissions` — a live 405 — so no level could be set
  anywhere in the product, and "Needs approval" would have been written as `always_allow`.)*

### CNR-PERM-GRID-NEVER-LIES
- GIVEN a permission write that FAILS (stop the backend, or force a 500)
- WHEN I set a tool to Blocked
- THEN the control does NOT keep showing Blocked — it rolls back to the level the backend
  actually holds, and the error is surfaced.
  *(Regression guard: the optimistic update never rolled back, so a tool read as "Blocked" while
  it sat at `needs_approval` and was still being offered to Lucius — one confirm-click from
  running.)*

### CNR-PERM-ADD-FLOW-CHOICES-PERSIST
- GIVEN the add flow's step 4 with a tool set to Blocked
- WHEN I press Add server
- THEN that level is written, and if the permission write fails the flow SAYS so rather than
  reporting a clean success — a `blocked` chosen here must never land as `needs_approval`
  silently.

### CNR-PERM-TRUSTED-MIGRATION
- GIVEN an MCP server that previously had `trusted: true`
- WHEN the migration runs
- THEN its tools are at the **Needs approval** default — NOT silently upgraded to Always allow.

---

## 5 · Vendor trust (§5) — MCP only

### CNR-TRUST-PANEL
> Amended (D23). Spec §5 named three placements; the panel ships in **two**. The directory
> preview — the browse-before-you-add screen — gets the lighter treatment in
> `CNR-PREVIEW-ATTRIBUTION` + `CNR-PREVIEW-DETAILS` instead. It discloses no less; it just
> stops repeating itself.

- GIVEN an MCP connector
- WHEN I view its detail page, or the add flow's review step
- THEN a publisher panel appears in BOTH, SEPARATE from the per-tool grid, showing
  publisher + badge, the endpoint verbatim in monospace, and the appropriate note.
- AND its heading reads **Publisher** — the phrase "vendor trust" is our internal name for the
  concept and appears NOWHERE in the UI.
- AND the panel does NOT appear on the directory preview.

### CNR-PREVIEW-ATTRIBUTION
- GIVEN any catalog entry on the directory preview
- WHEN I read who published it
- THEN a single line reads **Developed by &lt;Publisher&gt;** directly under the description,
  with the name a LINK carrying an external-link glyph when the entry has a `homepageUrl` (else
  its `repoUrl`), and plain text when it has neither — never a fabricated link, never a hidden
  attribution.
- AND **on an MCP entry only**, directly beneath it sits ONE muted sentence, whose wording
  differs per verification state: `verified` states we verified the publisher; `unverified`
  says we have NOT; `unknown` says we do not know who published it. Three distinct sentences,
  never one.
- AND a **native** connector (channel, data source, web provider) shows the attribution line
  and NOTHING under it — see `CNR-TRUST-NATIVE-ABSENT`.
- AND a connector Open Net Worth publishes gets the attribution line with NO trust sentence —
  we are not a third party to ourselves.
- AND an MCP entry with no publisher at all shows no "Developed by" line, and still shows the
  `unknown` sentence.

### CNR-PREVIEW-DETAILS
- GIVEN a directory preview
- WHEN I scroll past the chip rows
- THEN a **Details** block sits under a divider with: **Author** (the publisher, linked as
  above), the endpoint labelled **Connector URL** for a hosted server or **Command** for a
  local one, with a copy-to-clipboard button, and **More info** — a stacked list of real links
  (Documentation / Source repository), each with the external-link glyph.
- AND any sub-block an entry has no data for is omitted rather than rendered empty or dead
  (D11: never an `href="#"` placeholder); the whole block and its divider disappear when an
  entry has none of the three.

### CNR-TRUST-REVIEW-USES-THE-PROBE
- GIVEN the add flow's review step, after a successful Test connect
- WHEN I read the vendor-trust panel
- THEN the publisher and the ENDPOINT are the ones the probe reported — the server's own
  `initialize` self-report and the backend's verbatim rendering of the config — not a value
  re-derived from the form.
- AND a server that volunteers no publisher shows the **Unverified publisher** badge (the `unknown` state; same words as `unverified`, its note differs) with an empty
  name slot and the "we can't vouch for it" note — never the literal string "Unknown publisher"
  rendered as the publisher's NAME, which would flip the panel to the known-publisher note.
- AND a CURATED catalog entry keeps its **Verified**/**Unverified** badge through the review step:
  the probe reports `unknown` for every typed endpoint by construction, and must not downgrade a
  curated entry mid-flow (the same split-brain D14 fixed).

### CNR-TRUST-THREE-STATE
- GIVEN a curated official entry / a known-but-community entry / a user-added custom server
- WHEN I read each publisher badge
- THEN they read **Verified publisher** / **Unverified publisher** (for both `unverified` and `unknown`)
  respectively — three distinct states, not a boolean.

### CNR-TRUST-NO-FALSE-AUTHOR
- GIVEN the generic "Custom MCP server" entry or any user-added server
- WHEN I read its authorship
- THEN it claims NO author — it must NEVER read "Developed by Open Net Worth" and must never
  carry a Verified badge.

### CNR-TRUST-NATIVE-ABSENT
- GIVEN a non-MCP connector (channel, data source, web provider)
- WHEN I view its detail page
- THEN there is no vendor-trust panel.
- AND there is **no trust sentence either** — no "Open Net Worth does not control third-party
  servers", no "only use connectors from developers you trust". Spec §5 is *Vendor trust (MCP
  only)*; the prose is that panel's other half and is bound by the same rule.
- AND the same holds on its **directory landing page**: attribution line, nothing under it.
- AND its `Developed by <Publisher>` line is still present and still names the real publisher
  (Serper reads *Developed by Serper*) — the attribution is the honest half and it stays.
  *(Regression guard: Serper — a Web provider WE build and operate against an API we chose —
  was telling the reader "We haven't verified this publisher". There is no third party in that
  loop to warn about, and the warning spent the reader's attention on a risk that isn't there.)*

---

## 6 · Browse & add (§6)

### CNR-DIR-STICKY
- GIVEN the directory is open with enough results to scroll
- WHEN I scroll the results
- THEN search + family chips + sort stay pinned as one sticky block, and the modal header and
  footer never move.

### CNR-DIR-FIXED-HEIGHT
- GIVEN the directory is open
- WHEN I filter down to two results
- THEN the results area holds its 480px floor — the dialog does NOT shrink.

### CNR-DIR-INSTALLED-STAY
- GIVEN a connector that is already installed
- WHEN I open the directory
- THEN its card is **still listed**, with the `+` swapped for a **settings gear** that opens its
  detail page.
  *(Regression guard: the outgoing tab HID installed cards, which produced dead ends — a
  Channels filter reading "No matches" purely because the one real channel was set up.)*

### CNR-DIR-NEVER-BLANK
- GIVEN a family whose entries are all installed (e.g. Channels with Telegram configured)
- WHEN I filter to that family
- THEN I see the installed card(s) with gears — NOT a blank modal body with no explanation.
  *(Regression guard: reproduced in the outgoing tab.)*

### CNR-DIR-POPULAR
- GIVEN the directory on its unfiltered, unsearched view
- WHEN it renders
- THEN Popular tiles appear above the grid; applying ANY filter or search hides them.

### CNR-DIR-VERIFIED-BADGE
- GIVEN a verified catalog entry
- WHEN I view its card
- THEN the marker is a **text badge reading "Verified"**, not a green check.

### CNR-DIR-PREVIEW
- GIVEN any catalog card except the generic MCP one
- WHEN I click it
- THEN a preview page opens with description, the attribution line
  (`CNR-PREVIEW-ATTRIBUTION`), tools, prompt suggestions and categories as counted chip rows,
  and the Details footer (`CNR-PREVIEW-DETAILS`).

### CNR-HEADER-IDENTITY
- GIVEN any connector's landing page (directory preview) or its detail page
- WHEN I read the header
- THEN it is ONE block on both screens: a **52×52 rounded tile** on the left, the **name (15px)
  and its one-line sub stacked beside it and vertically CENTRED against that tile**, and the
  primary action pinned right.
- AND the two pages are visually identical in that block — same tile size, same type sizes,
  same centring. Opening a connector from its landing page must not feel like a different
  product.
  *(Measured 1440 / 760px modal: tile box 52×52 centred at y=88, text-block centre y=88,
  action centre y=88.)*

### CNR-HEADER-PRIMARY-ACTION
- GIVEN a landing page (**Add** / **Add connector** / **Open settings**) or a detail page
  (**Connect** / **Resume** / **Pause** / **Reconnect** — see §3a)
- WHEN I look at the header
- THEN the action is the heaviest element in the row — a **38px-tall pill** (`.rd-btn.lg`),
  not the 24px `.xs` size used for dense in-row actions.
- AND its height is FIXED with `box-sizing: border-box` and does not change at any viewport
  width (spec §9), and the detail page's kebab stays at its dense icon size — §9 excludes small
  icon-only actions from the bump.
- AND at phone width the action **wraps to its own right-aligned row** rather than squeezing
  the title: the height never changes, only the position.
  *(Regression guard: at 390px with the action inline, "Google Programmable Search" wrapped to
  three lines and the button overflowed the modal body.)*

### CNR-HEADER-BRAND-LOGO
- GIVEN a connector with a licensed brand mark (Plaid, Yodlee, Alpaca, IBKR, Enable Banking,
  Telegram, DuckDuckGo, Tavily, Perplexity, Serper, Google PSE, Playwright MCP, Firecrawl)
- WHEN I view its tile anywhere in the tab — list row, directory card, landing page, detail page
- THEN the real mark renders, on a light plate rather than the category tint (many marks are
  near-black on transparent and would vanish on this app's dark surfaces).
- AND a non-square mark **letterboxes** inside the tile rather than stretching or cropping
  (Firecrawl's official PNG is 600×852).
- AND every mark is legible in BOTH themes.

### CNR-HEADER-LOGO-FALLBACK
- GIVEN a connector with no brand mark — Email, Webhook, the generic *Custom MCP server* card,
  a hosted MCP vendor we could not source an official asset for, or an MCP server the user added
- WHEN I view its tile
- THEN it shows the existing **tinted category glyph**, which must read as deliberate and not as
  a missing image: no broken-image icon, no empty box, no plate.
- AND the same fallback happens if a logo file fails to load at runtime.
- AND a user-added MCP server can NEVER borrow a vendor's mark by naming itself after one: a
  logo is attached by catalog id, and an installed catalog server inherits its mark by matching
  its **endpoint** (the same join as its provenance badge — D14).

### CNR-CHIPS-READ-AS-LABELS
- GIVEN a landing page's **Tools** chip row
- WHEN I read the chips
- THEN each reads as a human label — *Web search*, *Page fetch*, *Fetch transactions* — never
  the raw wire name (`web_search`, `fetch_page`, `plaid_fetch_transactions`).
- AND hovering a chip reveals the **machine name** in its tooltip, so the identifier a user
  would put in an automation is still discoverable.
- AND nothing is renamed: the tool's real name is unchanged everywhere it is persisted, and the
  **permission ladder still shows the raw name** — there the identifier IS the key the level is
  stored against.
- AND a tool discovered on an unknown MCP server still gets a readable label rather than falling
  back to the raw string.

### CNR-CHIPS-CATEGORIES-ARE-TOPICS
- GIVEN a landing page's **Categories** chip row
- WHEN I read it
- THEN it lists what the connector is FOR — Stripe reads *Payments*, Xero reads *Accounting*,
  Plaid reads *Banking*, Serper reads *Search · Web* — and never simply echoes the family/lane
  the reader is already browsing.
  *(Regression guard: Stripe's landing page read `CATEGORIES 1 → MCP`.)*
- AND the family keeps its real job — driving the list sections and the directory filter — and
  appears nowhere in this row.
- AND an entry with nothing honest to say (the generic custom-server card, a user's own MCP
  server) drops the row entirely rather than rendering an empty one.

### CNR-DETAILS-NATIVE-LINKS
- GIVEN a native catalog entry's Details footer (Plaid, Yodlee, Alpaca, IBKR Flex, Enable
  Banking, Tavily, Perplexity, Serper, Google PSE, DuckDuckGo, Telegram, Email)
- WHEN I read it
- THEN **Author** is a link to the publisher's own site, and a **More info** block lists the
  real links that entry has — Documentation / Support / Privacy policy.
- AND every link answers HTTP **200 directly, with no redirect**, and the page is what its label
  claims (a "Privacy policy" link opens a privacy policy).
- AND a field we could not verify is **absent**, not approximated — Perplexity ships with no
  support and no privacy link because every Perplexity-operated legal/help page answers 403
  behind a bot challenge.
- AND a connector WE publish (Email, Telegram) links its Author to `https://opennetworth.ai`,
  while the generic *Custom MCP server* entry still claims **no author and no homepage** (§5).

### CNR-DIR-ADD-OPENS-THE-FORM
- GIVEN a NATIVE catalog entry that holds no credentials yet (Plaid, Telegram, Yodlee, Alpaca,
  IBKR Flex, Enable Banking, a web-search provider)
- WHEN I click its `+` on the card, or **Add connector** on its preview page
- THEN I land on that connector's detail page **with its configuration form already open** —
  the provider's REAL fields (Plaid: Client ID + Secret + Environment) — with NO intervening
  tap on `Connect`. Add already said "set this up"; asking again is the same declaration twice.
- AND it is the SAME form `Connect` opens (`CNR-DETAIL-CONNECT-OPENS-THE-FORM`) on the SAME
  modal (`CNR-DETAIL-CONFIG-IS-A-VIEW`) — one config surface, not a second one built for this
  entry point.
- AND backing out of it (`‹ <connector name>`) leaves me on that connector's detail page,
  exactly as if I had arrived there normally: primary action, kebab and permission ladder all
  present, and its own back control reads `‹ Connectors`.
- AND the **gear** on an already-installed entry still opens the PLAIN detail page — an
  installed connector's gear means "show me its settings page", never "reopen the credential
  form" over credentials it already holds.
- AND an `.env`-managed connector gets NO form from this route either, for the same reason it
  gets no `Edit connection` (`CNR-DETAIL-KEBAB`): every per-provider endpoint refuses the
  write, so the Save would silently do nothing.
- AND the MCP path is untouched — Add on an MCP entry still starts step 1 of the add flow
  (`CNR-MCP-STEP1-NO-IDENTITY`), which is where an MCP server is configured.
- AND the same applies to the bank front door (`CNR-SECTION-ADD-BANK`): once the institution
  search has resolved country → provider, it lands ON that provider's form with the country
  pre-filled, which is the only place the carried country is visible.

### CNR-DIR-CUSTOM-MCP-SKIPS-PREVIEW
- GIVEN the "Custom MCP server" card
- WHEN I click it ANYWHERE
- THEN it goes straight to the add form — it has no preview.

### CNR-DIR-CATALOG-MATCHES-BACKEND
- GIVEN the shipped catalog
- WHEN I compare it against the connectors the backend actually emits
- THEN every live connector that should be addable has a card (Alpaca included — a missing
  Alpaca card was a real defect in the outgoing tab), and no card points at something that
  doesn't exist (IRAS Tax Tables is demo fixture data only and must NOT ship as an addable card).

---

## 7 · Add an MCP server (§7)

### CNR-MCP-STEP1-NO-IDENTITY
- GIVEN I start the add flow
- WHEN step 1 renders
- THEN it asks ONLY for connection info — **no Name field and no Server ID field**.

### CNR-MCP-TRANSPORT
- GIVEN step 1
- WHEN I switch transport
- THEN Remote (URL) shows Server URL + Authentication (None/API key/OAuth) + credential, and
  Local (stdio) shows Command + Arguments + Environment variables, with help text stating plainly
  that this launches a local process with the same access as the account running Lucius.

### CNR-MCP-PASTE
- GIVEN step 1
- WHEN I click the "Paste a config snippet" pill and paste a README-style
  `{"mcpServers": {…}}` block
- THEN it parses into the SAME fields above (switching transport, filling command/args/env or
  URL/auth) — it is NOT a separate mode, and the next step is identical to the typed path.
- AND it also handles a `servers` wrapper and a bare object, taking the first entry.

### CNR-MCP-TEST-REQUIRED
- GIVEN step 1 is filled
- WHEN I look at the primary button
- THEN it reads **Test connect**, not Save — there is no way to save an untested server.

### CNR-MCP-TEST-SUCCESS
- GIVEN a reachable server
- WHEN Test connect succeeds
- THEN Name is pre-filled from the server's self-reported `serverInfo.name`, Server ID is
  auto-slugged from it, and the discovered tool list is shown.

### CNR-MCP-TEST-FAILURE
- GIVEN an unreachable server (put `fail` in the URL/command per the prototype)
- WHEN Test connect fails
- THEN the REAL connect error shows inline with Back and Retry — **and no server record is
  created**. Verify via the API/store that nothing was persisted.

### CNR-MCP-TEST-IN-PROGRESS
- GIVEN a local (stdio) server whose command takes a while — reproduce with a command that
  prints to stderr and then sleeps, e.g. `sh -c 'echo fetching 264MB 1>&2; sleep 120'`
- WHEN Test connect is running
- THEN the connecting step shows an **elapsed clock counting up** (`m:ss`) beside the spinner,
  and the command's own output appears in a bounded log block below it, within ~1s of the
  command printing it.
- AND after ~10 seconds it adds the reason a first connect is slow — that the package is
  downloaded **before** the server starts and later connects take seconds — which must NOT be
  present on a fast connect (a warm `npx` finishes in ~3.6s).
- AND if the command has printed nothing at all, it says so explicitly rather than showing an
  empty box.
  *(Regression guard: the real incident. `npx -y interactive-brokers-mcp` unpacks ~264 MB and
  its cold download alone took 61.2s, so the probe was killed before the server started and
  reported a bare "timed out after 60s" — indistinguishable from a broken server. Warm, the
  same probe took 3.6s and found all 14 tools.)*

### CNR-MCP-TEST-TIMEOUT-EXPLAINS-ITSELF
- GIVEN a local server that prints to stderr and then never answers
- WHEN Test connect times out
- THEN the inline error carries **the last line or two the command printed**, not just "timed
  out after Ns" — and when the command printed nothing, it says that instead of inventing a
  cause.
- AND **no server record is created** (verify via the API), and no orphaned child process is
  left behind (`pgrep -f <your command>` finds nothing after the error appears).

### CNR-MCP-TEST-ABANDONED-MID-CONNECT
- GIVEN Test connect is running on a slow local command
- WHEN I close the modal (or press Back) before it finishes
- THEN the UI stops polling immediately, nothing is saved, and the spawned command is still
  reaped — no orphaned process survives the probe's own budget.

### CNR-MCP-SERVER-ID
- GIVEN step 3
- WHEN I view Server ID
- THEN it is collapsed and automatic — a slug of the name, collision-suffixed, shown as a
  monospace value behind a **customize** link. It is never required and never presented on equal
  footing with Name.

### CNR-MCP-STEP4-PERMISSIONS
- GIVEN step 4
- WHEN it renders
- THEN it shows the SAME permission ladder as §4, built from step 2's discovered tools, grouped
  read vs. write, every tool at Needs approval — and there is **no "Trust this server" toggle**
  anywhere in the flow or on the card afterwards.

### CNR-MCP-ADD-LEAVES-THE-LADDER-POPULATED
- GIVEN step 4, with one tool moved to **Blocked** and the rest left at Needs approval
- WHEN I press Add server and the flow lands me back on the connector's detail page
- THEN that page shows **the tools the probe discovered, at the levels I just set** — the blocked
  one Blocked, the rest at Needs approval — with **no** "Refresh tools list" click in between,
  and the header does not read *Not configured*.
  *(Regression guard, QA D1/D4: create saved the config and never ran a handshake, so the page
  said "This connector exposes no tools to Lucius yet — nothing to permission" while the levels
  sat stored and invisible. The recovery a user reaches for — remove and re-add — is also the
  one action that DROPS those levels. Verify a stdio server too: its status came back
  `not_configured` because the check tested `url`, which a local server never has.)*

### CNR-MCP-ADD-UNREACHABLE-AT-COMMIT
- GIVEN a server that passed Test connect but stops answering before I press Add server
- WHEN the add commits
- THEN the server is **still saved** with the levels I chose, and its row reads **Error** rather
  than claiming health — the flow's gate is Test connect, so a refused second connection must not
  discard a completed four-step add.

### CNR-MCP-MODAL-GEOMETRY
- GIVEN I move through all four steps
- WHEN each renders
- THEN the dialog holds one 480px minimum height and does not jump between steps; header and
  footer stay pinned and only the body scrolls.

---

## 8 · Layout contract (§9)

### CNR-MASTHEAD
- GIVEN any viewport width from 390px to 1440px
- WHEN the tab renders
- THEN the masthead is two-tier (title+sub with a divider, then a toolbar row with search pinned
  left and the primary action pinned right) and the page NEVER scrolls horizontally.

### CNR-MASTHEAD-LOAD-ORDER
- GIVEN `connectors.css` and `tabs/automations.css` are both unlayered and both define
  `rd-masthead` / `rd-input` / `rd-kebab*`
- WHEN the masthead is measured at 1440px in all three orders — (a) a fresh load of
  `/connectors`, (b) Automations → Connectors, (c) Connectors → Automations → Connectors
- THEN it computes `padding: 16px 28px 14px`, `gap: 14px`, `align-items: stretch` and
  `border-bottom-width: 0px` in ALL THREE, with search pinned left and the primary action
  pinned right — the masthead must not change with which lazy chunk loaded last.
- AND the Automations masthead is unchanged after Connectors has been visited
  (`flex-direction: row`, height 62px) — the same collision leaked in both directions.
- Regression guard: `frontend/src/components/tabs/connectors/connectors.css.test.ts`.

### CNR-VENDOR-TRUST-BADGE-SIDE
- GIVEN an MCP server with a known publisher, and one with none (D6 drops the name node)
- WHEN the vendor-trust panel renders
- THEN the provenance badge is right-aligned in BOTH cases — it must not jump to the left
  edge when it is the row's only child.

### CNR-STICKY-SUBROW
- GIVEN I scroll the list
- WHEN the masthead pins
- THEN the family tabs + status filter pin with it (they live inside the sticky masthead).

### CNR-CONTROL-HEIGHTS
- GIVEN buttons, the search input and selects
- WHEN I compare them at 390px, 834px and 1440px
- THEN each control is the SAME height at every width (fixed `height` + `border-box`, not
  padding-driven, not a breakpoint-bumped `min-height`), and all are pill-radius.
- AND `.xs` compact buttons are excluded from the mobile tap-target bump — small icon-only
  actions in dense grids do not balloon to 40px.

### CNR-BANK-SEARCH-ROW
- GIVEN the `Add a bank account` modal (`CNR-SECTION-ADD-BANK`)
- WHEN I compare its search row at 390px, 834px and 1440px
- THEN Country (narrow) · Bank name (wide) · Search read as ONE row: both labels on one
  baseline, both inputs on another, and the button on the INPUT row — never on the labels and
  never dragged down by the helper text.
- AND `2-letter code, e.g. US, GB, DE` sits on its own full-width line beneath the Country
  input, so it cannot crowd the Bank name field beside it.
- AND at 390px the two fields still share their row and only Search drops below, at its own
  width — a grid item stretches by default, and a button is one of the controls that must not.
- Regression guard: `frontend/src/components/tabs/connectors/InstitutionPickerModal.test.tsx`.

### CNR-MODAL-FOOTER
- GIVEN a modal with a single footer button
- WHEN it renders
- THEN that button does NOT stretch full width; only multi-button footers split evenly.
- The bank picker's lone `Close` is checked in `InstitutionPickerModal.test.tsx` — the phone
  rule keys off `:not(:only-child)`, so a second footer button is what would change it.

### CNR-LUCIUS-PANEL
- GIVEN the Lucius panel is open
- WHEN the viewport is ≥980px and then <980px
- THEN it pushes content (`margin-right: 390px`) above the breakpoint and overlays below it,
  with the Connectors layout intact in both.

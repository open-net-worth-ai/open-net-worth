// The Connectors redesign view model — the contract every screen lane builds against.
//
// `types.ts` beside this file mirrors the WIRE shape (`ConnectorView`, backend/models/
// connector.py). This file is the SCREEN shape the prototype's components expect.
// `adapter.ts` is the only thing that crosses between them.
//
// Spec: frontend/design_handoffs/connectors_handoff/index.html

/** The three real per-tool levels — matches `backend/api/tool_permissions.py`'s `Level`
 *  exactly. Renamed from `allow`/`ask`/`block` with the 2026-08-14 permission-store merge
 *  (ADR `2026-08-14-one-permission-store-for-every-tool`), which folded the old binary
 *  `tool_grants` table into this one: `allow` → `always_allow`, `ask` → `needs_approval`,
 *  `block` → `blocked`. The display labels below were already this wording — only the
 *  wire/storage value changed. */
export type PermissionLevel = 'always_allow' | 'needs_approval' | 'blocked'

/** What a GROUP control displays. `custom` is DERIVED for display and NEVER stored or
 *  sent as a stored value — it is the honest label for a non-uniform group (spec §4). */
export type GroupPermissionLevel = PermissionLevel | 'custom'

/** Every tool starts here. No tiering by tool type, no exceptions (spec §4). */
export const DEFAULT_PERMISSION: PermissionLevel = 'needs_approval'

/** Tool level → [menu label, Icons key, the `.cn-permbtn.on` tone class]. Insertion order
 *  IS the menu order in the group control. */
export const LEVELS: Record<GroupPermissionLevel, { label: string; icon: string; tone: string }> = {
  always_allow: { label: 'Always allow', icon: 'checkcircle', tone: 'good' },
  needs_approval: { label: 'Needs approval', icon: 'hand', tone: 'warn' },
  blocked: { label: 'Blocked', icon: 'blocked', tone: 'bad' },
  custom: { label: 'Custom', icon: 'dots', tone: '' },
}

/** The three real tool levels, in ladder order — for the per-tool segmented control. */
export const TOOL_LEVELS: PermissionLevel[] = ['always_allow', 'needs_approval', 'blocked']

/** A connector's per-tool levels, keyed by exact registry tool name. Sparse: a missing
 *  key reads as `DEFAULT_PERMISSION`, so a newly discovered tool is cautious by default. */
export type PermissionMap = Record<string, PermissionLevel>

export function toolPermission(perms: PermissionMap, tool: string): PermissionLevel {
  return perms[tool] ?? DEFAULT_PERMISSION
}

/** Derived, never stored: uniform → that level, non-uniform → `custom` (spec §4). */
export function deriveGroupLevel(tools: string[], perms: PermissionMap): GroupPermissionLevel {
  if (tools.length === 0) return DEFAULT_PERMISSION
  const first = toolPermission(perms, tools[0]!)
  return tools.every((t) => toolPermission(perms, t) === first) ? first : 'custom'
}

/** Picking a real group level stamps every tool in the group — deliberately lossy (spec §4). */
export function stampGroup(
  perms: PermissionMap,
  tools: string[],
  level: PermissionLevel,
): PermissionMap {
  const next = { ...perms }
  for (const t of tools) next[t] = level
  return next
}

// ── families ────────────────────────────────────────────────────────────────

/** Four lanes — a trust and ownership boundary, not a cosmetic split (spec §1). */
export type ConnectorFamily = 'Channel' | 'Data source' | 'Web' | 'MCP'

/** The family tab / list-section keys. `all` is the tab only, never a section. */
export type FamilyKey = 'channels' | 'data' | 'web' | 'mcp'
export type FamilyTab = FamilyKey | 'all'

export interface FamilySection {
  key: FamilyKey
  family: ConnectorFamily
  /** List section heading AND the family tab label. */
  title: string
  /** The directory chip label. */
  chipLabel: string
  /** The section's trailing add link (spec §2). */
  add: string
}

/** Section order is the list order. */
export const FAMILY_SECTIONS: FamilySection[] = [
  {
    key: 'channels',
    family: 'Channel',
    title: 'Channels',
    chipLabel: 'Channels',
    add: 'Add a channel',
  },
  {
    key: 'data',
    family: 'Data source',
    title: 'Data sources',
    chipLabel: 'Data sources',
    add: 'Add a data source',
  },
  { key: 'web', family: 'Web', title: 'Web', chipLabel: 'Web', add: 'Add a web provider' },
  { key: 'mcp', family: 'MCP', title: 'MCP', chipLabel: 'MCP', add: 'Add an MCP server' },
]

export const FAMILY_BY_KEY: Record<FamilyKey, ConnectorFamily> = {
  channels: 'Channel',
  data: 'Data source',
  web: 'Web',
  mcp: 'MCP',
}

/** Membership test — a connector appears under EVERY family it holds (spec §1). */
export function inFamily(entry: { families: ConnectorFamily[] }, tab: FamilyTab): boolean {
  return tab === 'all' || entry.families.includes(FAMILY_BY_KEY[tab])
}

// ── categories ──────────────────────────────────────────────────────────────

/**
 * What a connector is FOR — topical, and deliberately NOT the family.
 *
 * A family (above) is the lane: Channel · Data source · Web · MCP. It is the trust and
 * ownership boundary, it drives the list sections and the directory filter, and it is the one
 * thing a reader browsing that lane already knows. Echoing it back as the entry's only
 * "category" is what the landing page used to do — Stripe read `CATEGORIES 1 → MCP`, which
 * told a reader nothing they had not just clicked.
 *
 * A CLOSED vocabulary on purpose: an entry picks from this list rather than inventing its own,
 * so two accounting connectors cannot end up filed under "Accounting" and "Bookkeeping". The
 * union type is what makes a typo a build failure, and this is the data a category filter
 * would read if the directory ever grows one — so the names are ones a user would filter by.
 */
export const CONNECTOR_CATEGORIES = [
  'Accounting',
  'Banking',
  'Brokerage',
  'Economic data',
  'Email',
  'Expenses',
  'Filings',
  'Market data',
  'Messaging',
  'Payments',
  'Research',
  'Search',
  'Tax',
  'Web',
] as const

export type ConnectorCategory = (typeof CONNECTOR_CATEGORIES)[number]

// ── status ──────────────────────────────────────────────────────────────────

/** Row/badge tone. `''` is the neutral badge (a deliberately disconnected connector). */
export type StatusTone = 'good' | 'bad' | ''

export type StatusFilter = 'all' | 'connected' | 'attention'

export const STATUS_FILTERS: [StatusFilter, string][] = [
  ['all', 'All'],
  ['connected', 'Connected'],
  ['attention', 'Attention'],
]

// ── tools ───────────────────────────────────────────────────────────────────

/** The fixed two-way taxonomy — never per-connector custom group names (spec §4). */
export const READ_GROUP = 'Read-only tools'
export const WRITE_GROUP = 'Write & delete tools'

export interface ToolGroup {
  name: typeof READ_GROUP | typeof WRITE_GROUP
  tools: string[]
}

/** Read verbs from the prototype's `classifyTool`, matched against ANY `_`-separated
 *  token rather than only the first. The prototype's leading-prefix regex was written
 *  for its synthetic tool names; against real ones it mis-files `web_search` (the whole
 *  point of the Web family) as a write tool, contradicting the prototype's own
 *  hand-authored DuckDuckGo group. Same verbs, wider match. */
const READ_VERBS = new Set([
  'get',
  'list',
  'read',
  'lookup',
  'fetch',
  'estimate',
  'screenshot',
  'extract',
  'search',
  'status',
])

export function classifyTool(tool: string): 'read' | 'write' {
  const tokens = tool
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean)
  return tokens.some((t) => READ_VERBS.has(t)) ? 'read' : 'write'
}

/** A tool to group: a bare name, or a name plus the backend's `read_only` flag. */
export type GroupableTool = string | { name: string; read_only?: boolean | null }

/** Group tools read vs. write. The backend's `read_only` (an MCP server's own
 *  `readOnlyHint`, else its name allowlist) wins whenever it is present; `classifyTool`'s
 *  verb guess is only the fallback for a tool that arrived without one. LABELLING ONLY —
 *  the heading decides nothing about a tool's level or whether a call fires. A connector
 *  with no write tools shows only the read group — empty groups are dropped, never
 *  rendered as an empty header. */
export function buildToolGroups(tools: GroupableTool[]): ToolGroup[] {
  const read: string[] = []
  const write: string[] = []
  for (const t of tools) {
    const name = typeof t === 'string' ? t : t.name
    const flag = typeof t === 'string' ? undefined : t.read_only
    const isRead = typeof flag === 'boolean' ? flag : classifyTool(name) === 'read'
    ;(isRead ? read : write).push(name)
  }
  return [
    { name: READ_GROUP as typeof READ_GROUP, tools: read },
    { name: WRITE_GROUP as typeof WRITE_GROUP, tools: write },
  ].filter((g) => g.tools.length > 0)
}

// ── the screen shapes ───────────────────────────────────────────────────────

/** What a LIST ROW needs. A row answers two questions and stops: what is it, and is it
 *  working (spec §2) — no sub-line, no family label, no toggle, no Configure button. */
export interface ConnectorEntry {
  /** The wire id — `email` | `telegram` | `web_search:tavily` | `mcp:<server_id>` … */
  id: string
  name: string
  /** Wire `kind`. Never rendered — carried so the row can feed the Lucius `connector` entity
   *  descriptor, whose field schema names it. Dropping it silently degrades the
   *  `@`-mention popover and the screen snapshot. */
  kind: string
  /** An `Icons` key — the generic category glyph, and the fallback whenever `logo` is absent. */
  icon: string
  /** A token reference (`var(--ibkr)`) used to tint the icon tile. */
  hue: string
  /** The connector's real brand mark, when one is licensed and on disk (`logos.ts`, D25).
   *  Absent for everything else, which renders the tinted glyph instead. */
  logo?: string
  families: ConnectorFamily[]
  /** Optional inline tag beside the name, e.g. DuckDuckGo's `Default`. */
  tag?: string
  /** One vocabulary: `Connected` for every healthy connector (spec §2). */
  statusText: string
  tone: StatusTone
  /** True only for an ACTUAL error — never "configured but switched off" (spec §1). */
  attention: boolean
  /** Drives the detail page's primary action: Disconnect ⇄ Connect (spec §3). */
  enabled: boolean
  /** The connector's own sub-line — bot handle, mailbox, query name, provider. Shown on
   *  the DETAIL page only; the row is deliberately one line. */
  sub: string
}

/**
 * Publisher provenance — THREE states, deliberately not a boolean (spec §5 names all three).
 *
 *  - `verified`   confirmed genuinely published by the vendor it claims (an org-owned
 *                 official repo, or a first-party integration we build and operate).
 *  - `unverified` real and known, but NOT vendor-official — a community server by an
 *                 independent maintainer.
 *  - `unknown`    no known publisher at all: the custom-server flow, where the user points
 *                 us at an arbitrary endpoint.
 *
 * A boolean collapses `unverified` into `unknown`, and that collapse is exactly how
 * "official" gets claimed for something that merely exists — an internal RFC described FRED
 * MCP and SEC EDGAR MCP as official when both are one independent maintainer's projects.
 * "We looked and found a community maintainer" and "we have no idea who this is" are
 * different claims to the user, so they get different values.
 */
export type VerificationState = 'verified' | 'unverified' | 'unknown'

/** Provenance as the MCP probe reports it (`backend/mcp/client_manager._probe_trust`).
 *  Defined here beside `VerificationState` so the wire module and the pure merge logic can
 *  both name it without importing each other. */
export interface McpTrust {
  publisher: string | null
  verification: VerificationState
  /** Both scheme-allowlisted to http(s) at the transport boundary (`_clean_info_url`). */
  homepage_url: string | null
  repo_url: string | null
  /** The URL or command line, verbatim — what you are actually trusting. */
  endpoint: string
}

/** What the vendor-trust panel renders, once the catalog and the probe are reconciled. */
export interface VendorMeta {
  /** The vendor, when the catalog vouches for one or the server reported one itself.
   *  Absent for a custom server that claims no author (spec §5). */
  publisher: string | null
  verification: VerificationState
  /** True when the provenance came from our CURATED catalog — the only source that may
   *  claim `verified`/`unverified`. */
  curated: boolean
  /** The endpoint verbatim, as the backend renders it. Empty until the probe answers. */
  endpoint: string
  /** The vendor's own docs link, when the catalog (or the probe) knows one. Currently only
   *  actually READ by RFC `2026-08-14-mcp-oauth` §4.6's `no_metadata` failure message — the one
   *  place a docs link is the spec's own required affordance ("we can't sign you in
   *  automatically" + a way to go find an API key instead). */
  homepageUrl?: string
}

export const UNKNOWN_VENDOR: VendorMeta = {
  publisher: null,
  verification: 'unknown',
  curated: false,
  endpoint: '',
}

/**
 * The review step's provenance (spec §5): curated first, the probe fills what a catalog
 * cannot know.
 *
 * The probe's `verification` is structurally always `unknown` — it describes an endpoint the
 * user typed — so it may NEVER override a curated value. Letting it would re-create the exact
 * split-brain the endpoint join fixed: Playwright MCP reading "Verified" on its directory card
 * and "Unknown publisher" one screen later. In the other direction nothing a server SAYS can
 * mint a badge either, since `verification` is absent from the create/update models entirely.
 */
export function mergeTrust(meta: VendorMeta, trust: McpTrust | null | undefined): VendorMeta {
  if (!trust) return meta
  return {
    publisher: meta.publisher ?? trust.publisher,
    verification: meta.curated ? meta.verification : trust.verification,
    curated: meta.curated,
    endpoint: trust.endpoint || meta.endpoint,
    homepageUrl: meta.homepageUrl ?? trust.homepage_url ?? undefined,
  }
}

/**
 * The levels that MOVED, so an edit writes only what the user changed.
 *
 * `needs_approval` on a tool that HAS a stored level is a real downgrade from `always_allow`
 * and is written — the one place this differs from the add path. `needs_approval` on a tool
 * with NO stored level is not a change at all: an absent row already means `needs_approval`,
 * so writing one would mint a row for every newly discovered tool on every re-test and turn
 * "the user decided this" into noise.
 *
 * A tool the server stopped advertising is left alone — it is not on the ladder to have been
 * decided about, and its row is cleaned up when the server is removed.
 */
export function changedPermissions(saved: PermissionMap, next: PermissionMap): PermissionMap {
  const out: PermissionMap = {}
  for (const [tool, level] of Object.entries(next)) {
    const before = saved[tool]
    if (before === level) continue
    if (before === undefined && level === DEFAULT_PERMISSION) continue
    out[tool] = level
  }
  return out
}

/** Everything the detail page adds on top of the row (spec §3). */
export interface ConnectorDetailEntry extends ConnectorEntry {
  /** Whether the connector holds credentials yet. Distinct from `enabled`: a connector
   *  with no credentials cannot be connected by flipping a flag, so the detail page's
   *  Connect opens its configuration form instead of PATCHing `enabled` (spec §3). */
  configured: boolean
  desc: string
  /** Publisher line. Absent for a custom MCP server, which claims no author (spec §5). */
  developer?: string
  /** Where the publisher lives — same field as `DirectoryRow.homepageUrl`, threaded through
   *  for the ALREADY-INSTALLED page too, so the attribution line links out there exactly as
   *  it does at browse time. Absent renders the name as plain text (owner-flagged gap,
   *  2026-08-21: the installed page previously never linked ANY native publisher, even
   *  though the browse-time preview always did). */
  developerUrl?: string
  /** True for an external MCP server — gates the vendor-trust panel (spec §5). */
  mcp: boolean
  verification: VerificationState
  /** The URL or local command, verbatim — what you are actually trusting (spec §5). */
  url?: string
  /** The website host a user-added MCP server's name/icon came from — a claim, shown as one. */
  identitySource?: string
  toolGroups: ToolGroup[]
  /** Flat tool list, in wire order, before grouping. */
  tools: string[]
  /** Tappable example prompts that seed the composer (spec §3). */
  prompts: string[]
  /** Current per-tool levels, as read from the server. */
  perms: PermissionMap
}

/**
 * Where a focus request wants to LAND, beyond "that connector's detail page".
 *
 * Both fields are omitted by a caller that just wants to show a connector — a list row, or
 * the directory's gear on an already-installed entry, which means "show me its settings
 * page", not "reopen the credential form". Only a caller that has already declared intent to
 * SET THE CONNECTOR UP asks for more.
 */
export interface FocusConnectorOptions {
  /** Land ON the connector's configuration form rather than one tap short of it. The detail
   *  page still renders behind it, so backing out arrives exactly where a plain focus would
   *  have. Ignored when there is nothing to set up — a connector with no form (MCP, or
   *  `.env`-managed) or one that already holds credentials. */
  configure?: boolean
  /** Seeds a form that would otherwise ask for a country the user has already typed — the
   *  institution search resolves country → provider and hands the country on. */
  country?: string
}

/** A Browse & add directory entry (spec §6). An INSTALLED entry stays listed — it keeps
 *  its card and swaps the `+` for a settings gear opening its detail page. */
export interface DirectoryEntry {
  /** Matches `ConnectorEntry.id` once installed; otherwise the catalog key. */
  id: string
  name: string
  icon: string
  hue: string
  logo?: string
  families: ConnectorFamily[]
  /** What the connector is FOR — topical tags, never the family. Empty for an entry with
   *  nothing honest to say (a user's own MCP server), which drops the row rather than
   *  rendering one. */
  categories: ConnectorCategory[]
  tag?: string
  /** RESOLVED — never assume this came from the catalog entry's own authored copy. For a
   *  native connector `directoryRows.resolveDirectory` fills it from the live `ConnectorView`
   *  (the same text its installed detail page renders); only an MCP catalog entry's own
   *  `sub` survives here unchanged, since the backend has no copy for it to read instead. */
  sub: string
  /** See `sub`. */
  desc: string
  developer?: string
  mcp: boolean
  verification: VerificationState
  url?: string
  tools: string[]
  popular: boolean
  installed: boolean
}

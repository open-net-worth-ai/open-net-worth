// The ONLY seam between the wire shape (`types.ts` — backend `ConnectorView`) and the
// screen shape (`model.ts` — what the prototype's components expect).
//
// `GET /api/connectors` returns a FLAT list, `{connectors: [...]}` — 20 entries on a
// fresh instance (14 built-ins + one card per web-search provider), growing with MCP
// servers. Family grouping, install state, status vocabulary and tool grouping are all
// derived here so every screen reads one truth.

import { CATALOG, catalogProvenanceFor } from './catalog'
import { connectorLogo } from './logos'
import type { McpEditTarget } from './mcpConfig'
import type { OAuthSummary } from './mcpApi'
import type { Connector } from './types'
import {
  buildToolGroups,
  type ConnectorDetailEntry,
  type ConnectorFamily,
  type PermissionLevel,
  type PermissionMap,
  type StatusTone,
} from './model'

const MCP_PREFIX = 'mcp:'
const WEB_SEARCH_PREFIX = 'web_search:'

/** Coarse `status` (not_configured | configured | active | error) PLUS the raw MCP
 *  `last_status` vocabulary, which rides in the connector's `config` — `connect_error`
 *  vs. `auth_error` is the MCP-specific fact `statusOf` needs and `status` cannot carry. */
const ERROR_STATUSES = new Set(['error', 'connect_error', 'auth_error', 'failed'])

/** Family membership per connector kind. An ARRAY: a connector appears under every
 *  section it belongs to, and both the section and the filter test membership rather
 *  than a single primary family (spec §1).
 *
 *  Email is ONE connector under Data sources, not an "Email (SMTP)" channel plus an
 *  "Email import (IMAP)" data source (spec §8). Webhook is a Channel here — it is only
 *  the DIRECTORY that never offers it as an addable card. */
const FAMILIES: Record<string, ConnectorFamily[]> = {
  telegram: ['Channel'],
  webhook: ['Channel'],
  email: ['Data source'],
  plaid: ['Data source'],
  lunch_flow: ['Data source'],
  enable_banking: ['Data source'],
  yodlee: ['Data source'],
  alpaca: ['Data source'],
  ibkr: ['Data source'],
  yahoo_finance: ['Data source'],
  coingecko: ['Data source'],
  web_search: ['Web'],
  mcp: ['MCP'],
}

/** The fallback category glyph + tint per kind. A connector with a licensed brand mark shows
 *  that instead — see `logos.ts` and D25; these stay as the answer for everything else. */
const PRESENTATION: Record<string, { icon: string; hue: string }> = {
  telegram: { icon: 'chat', hue: 'var(--accent)' },
  webhook: { icon: 'link', hue: 'var(--cash-c)' },
  email: { icon: 'mail', hue: 'var(--recv)' },
  plaid: { icon: 'accounts', hue: 'var(--crypto)' },
  lunch_flow: { icon: 'accounts', hue: 'var(--good)' },
  enable_banking: { icon: 'accounts', hue: 'var(--cash-c)' },
  yodlee: { icon: 'accounts', hue: 'var(--good)' },
  alpaca: { icon: 'investments', hue: 'var(--crypto)' },
  ibkr: { icon: 'investments', hue: 'var(--ibkr)' },
  yahoo_finance: { icon: 'investments', hue: 'var(--cash-c)' },
  coingecko: { icon: 'investments', hue: 'var(--crypto)' },
  web_search: { icon: 'search', hue: 'var(--warn)' },
  mcp: { icon: 'cpu', hue: 'var(--apt)' },
}

/** Publisher line (spec §3 "Developed by"/"Powered by", §5 vendor trust). Deliberately
 *  absent for `mcp` — a server we did not publish claims no author, and VendorTrust renders
 *  "Unknown publisher" plus the stronger warning for it.
 *
 *  Keyed by `kind` for a connector that is ONE kind = ONE publisher, and by the
 *  full connector `id` for web-search — six providers share the kind `"web_search"` but
 *  each has its own real publisher, so the id-first lookup below (mirroring the SAME
 *  id-before-kind pattern `_decorate` already uses backend-side for description/tools) picks
 *  the right one. `url` is the SAME value as the catalog's own `homepageUrl` for this id —
 *  this map previously carried no url at all, so the installed detail page rendered every
 *  native publisher's name as unlinked plain text even though the browse-time preview always
 *  linked it (owner-flagged gap, 2026-08-21). Also previously missing `simplefin`/
 *  `lunch_money`/`ynab`/every web-search provider entirely — no attribution line rendered for
 *  any of them once installed. */
const DEVELOPERS: Record<string, { name: string; url: string }> = {
  telegram: { name: 'Open Net Worth', url: 'https://opennetworth.ai' },
  webhook: { name: 'Open Net Worth', url: 'https://opennetworth.ai' },
  email: { name: 'Open Net Worth', url: 'https://opennetworth.ai' },
  simplefin: { name: 'SimpleFIN', url: 'https://www.simplefin.org/' },
  plaid: { name: 'Plaid Inc.', url: 'https://plaid.com/docs/link/' },
  lunch_money: { name: 'Lunch Money', url: 'https://lunchmoney.app' },
  lunch_flow: { name: 'Lunch Flow', url: 'https://lunchflow.app' },
  ynab: { name: 'YNAB', url: 'https://www.ynab.com' },
  enable_banking: {
    name: 'Enable Banking',
    url: 'https://enablebanking.com/docs/api/reference/',
  },
  yodlee: { name: 'Yodlee (Envestnet)', url: 'https://www.yodlee.com/' },
  alpaca: {
    name: 'Alpaca Securities',
    url: 'https://docs.alpaca.markets/us/docs/trading-api',
  },
  ibkr: {
    name: 'Interactive Brokers',
    url: 'https://www.interactivebrokers.com/docs/web-api/flex-web-service/introduction',
  },
  // Same "Open Net Worth" treatment as telegram/webhook/email above: we built and operate
  // this integration ourselves, even though the data underneath comes from a vendor's API —
  // the vendor (Yahoo, CoinGecko) is still named in the card's own name/description, just
  // not on this specific "who wrote this connector" line (see ConnectorDetail.tsx's own
  // comment on the non-MCP "Developed by" line for the full reasoning).
  yahoo_finance: { name: 'Open Net Worth', url: 'https://opennetworth.ai' },
  coingecko: { name: 'Open Net Worth', url: 'https://opennetworth.ai' },
  'web_search:duckduckgo': { name: 'DuckDuckGo', url: 'https://duckduckgo.com/' },
  'web_search:tavily': {
    name: 'Tavily AI',
    url: 'https://docs.tavily.com/documentation/api-reference/endpoint/search',
  },
  'web_search:perplexity': {
    name: 'Perplexity AI',
    url: 'https://docs.perplexity.ai/docs/getting-started/overview',
  },
  'web_search:serper': { name: 'Serper', url: 'https://serper.dev/' },
  'web_search:google_pse': {
    name: 'Google',
    url: 'https://developers.google.com/custom-search/v1/overview',
  },
  'web_search:firecrawl': { name: 'Firecrawl', url: 'https://docs.firecrawl.dev/' },
}

/** Tappable example prompts that seed the composer (spec §3). Presentational catalog
 *  data — the API carries no prompt field. */
const PROMPTS: Record<string, string[]> = {
  telegram: ['Send me a daily balance summary', 'Notify me if any account drops below $500'],
  email: ['Reparse last month’s statements', 'Send this week’s digest now'],
  plaid: ['Reconnect my bank account', 'Show all accounts linked through Plaid'],
  lunch_flow: ['Sync my Lunch Flow accounts now', 'Show accounts linked through Lunch Flow'],
  enable_banking: ['Sync my EU bank accounts', 'Show accounts linked through Enable Banking'],
  yodlee: ['Sync my Yodlee accounts now', 'Show all accounts linked through Yodlee'],
  alpaca: ['Import my Alpaca positions', 'Show my Alpaca cash balance'],
  ibkr: ['Import today’s IBKR trades', 'Show my IBKR cash balance'],
  web_search: ['Look up today’s SGD/USD rate', 'Check if IRAS changed the GST rate'],
}

/** `web_search` and `mcp` ids are `<kind>:<instance>`; everything else is the bare kind. */
function kindOf(c: Connector): string {
  return c.kind || ''
}

/** The connector's public settings and state; its shape differs per kind. */
function configOf(c: Connector): Record<string, unknown> {
  return c.config ?? {}
}

function str(cfg: Record<string, unknown>, key: string): string {
  const v = cfg[key]
  return typeof v === 'string' ? v : ''
}

/** The MCP-specific `auth_type` an oauth-aware caller needs — `ConnectorsTab.tsx`'s Reconnect
 *  reads this to decide whether a dead sign-in needs a fresh popup (`oauth`) or just a retest
 *  (every other auth type). `''` for a non-MCP connector, which carries no such field at all. */
export function mcpAuthType(c: Connector): string {
  return str(configOf(c), 'auth_type')
}

/** Token-free OAuth status (`McpClientStore._oauth_summary`) for an MCP server, or `null` when
 *  the wire carries none — a non-MCP connector. Every MCP server's `config` carries the key
 *  once `auth_type` can be `oauth` (RFC `2026-08-14-mcp-oauth` §4.4), so a present-but-
 *  `connected:false` block is the normal never-signed-in shape, not an absence. */
export function mcpOAuthSummary(c: Connector): OAuthSummary | null {
  const raw = configOf(c).oauth
  if (!raw || typeof raw !== 'object') return null
  return raw as OAuthSummary
}

/** Installed = it exists on this instance. A `not_configured` card is a DIRECTORY entry,
 *  not a list row — otherwise a fresh instance shows 13 rows of nothing. An MCP server is
 *  installed by virtue of being in the store, even before its first successful probe. */
export function isInstalled(c: Connector): boolean {
  if (c.id.startsWith(MCP_PREFIX)) return true
  return c.status !== 'not_configured'
}

/** One vocabulary: `Connected` for every healthy connector — no "Active" vs "Connected"
 *  distinction (spec §2). Attention means an ACTUAL error, never "configured but
 *  switched off": a connector the user deliberately disabled is not a problem to be
 *  solved (spec §1). This is why `health.ts`'s `needsAttention` is NOT reused — it
 *  deliberately flags `configured_disabled`, which this design rejects. */
export function statusOf(c: Connector): {
  statusText: string
  tone: StatusTone
  attention: boolean
} {
  const cfg = configOf(c)
  const lastStatus = str(cfg, 'last_status')
  if (ERROR_STATUSES.has(c.status) || ERROR_STATUSES.has(lastStatus)) {
    const authError = c.status === 'auth_error' || lastStatus === 'auth_error'
    return { statusText: authError ? 'Auth error' : 'Error', tone: 'bad', attention: true }
  }
  if (c.enabled) return { statusText: 'Connected', tone: 'good', attention: false }
  // "Disconnected" implies a connection that existed and then dropped — wrong for a
  // connector that has never been set up at all, which reads as "something broke" rather
  // than "nothing's been entered yet". An MCP server is configured the moment it's in the
  // store (its endpoint WAS the setup), matching `toEntry`'s own `configured` derivation
  // below — everything else earns "configured" by having credentials on file.
  const everConfigured = c.id.startsWith(MCP_PREFIX) || c.status !== 'not_configured'
  if (!everConfigured) return { statusText: 'Not connected', tone: '', attention: false }
  return { statusText: 'Disconnected', tone: '', attention: false }
}

/** The wire now carries the REAL three-value level per tool, joined server-side from the
 *  permission ladder — so this is a straight copy, not a mapping. A tool arriving without
 *  one is left out of the map entirely rather than defaulted here: `toolPermission` already
 *  owns what an absent entry means (`DEFAULT_PERMISSION`), and writing a second default
 *  beside it is how the two drift. */
function permsOf(c: Connector): PermissionMap {
  const out: PermissionMap = {}
  for (const t of c.tools ?? []) {
    if (t.level) out[t.name] = t.level satisfies PermissionLevel
  }
  return out
}

function displayName(c: Connector): string {
  // "Web search · Tavily" → "Tavily"; the family section already says Web.
  if (c.id.startsWith(WEB_SEARCH_PREFIX)) return c.name.replace(/^Web search\s*•\s*/, '') || c.name
  return c.name || c.id
}

/** The endpoint being trusted — URL for a remote server, the command line for stdio. */
function endpointOf(c: Connector): string | undefined {
  if (!c.id.startsWith(MCP_PREFIX)) return undefined
  const cfg = configOf(c)
  const url = str(cfg, 'url')
  if (url) return url
  const command = str(cfg, 'command')
  if (!command) return undefined
  const args = Array.isArray(cfg.args)
    ? (cfg.args as unknown[]).filter((a) => typeof a === 'string')
    : []
  return [command, ...args].join(' ')
}

/** A user-added MCP server's own icon, served from our origin (`mcp.site_identity` fetched,
 *  sniffed and cached it). `icon_version` busts the browser's cache when it is re-fetched. */
function mcpIconUrl(c: Connector): string | undefined {
  const version = configOf(c).icon_version
  if (typeof version !== 'number') return undefined
  return `/api/mcp-clients/${encodeURIComponent(c.id.slice(MCP_PREFIX.length))}/icon?v=${version}`
}

/** Fold one wire connector into the shape every screen renders. */
export function toEntry(c: Connector): ConnectorDetailEntry {
  const kind = kindOf(c)
  const mcp = c.id.startsWith(MCP_PREFIX)
  const cfg = configOf(c)
  const look = PRESENTATION[kind] ?? { icon: 'cpu', hue: 'var(--faint)' }
  const tools = (c.tools ?? []).map((t) => t.name)
  const status = statusOf(c)
  // Matched by ENDPOINT — the thing being trusted, and the one part a user can't rename.
  const provenance = mcp ? catalogProvenanceFor(endpointOf(c)) : null
  // id-before-kind: web-search's six providers share one `kind` ("web_search") but
  // each has its own real publisher, so the specific id wins when it has its own entry —
  // same lookup order `connector_catalog.copy_for` uses backend-side for description/tools.
  const developer = DEVELOPERS[c.id] ?? DEVELOPERS[kind]

  return {
    id: c.id,
    name: displayName(c),
    kind,
    icon: look.icon,
    hue: look.hue,
    // A native connector's id IS its catalog id, so it resolves directly. An MCP server's is
    // whatever the user named it, so it never resolves directly and inherits from the catalog
    // entry its ENDPOINT matches instead — the same join, and the same reason, as provenance:
    // a server calling itself `stripe` must not be able to borrow Stripe's mark. Absent a
    // catalog match, a server shows the icon IT or its website declared — never ours.
    logo: provenance?.logo ?? (mcp ? mcpIconUrl(c) : connectorLogo(c.id)),
    families: FAMILIES[kind] ?? ['MCP'],
    categories: mcp
      ? (provenance?.categories ?? [])
      : (CATALOG.find((e) => e.id === c.id)?.categories ?? []),
    tag: c.id.startsWith(WEB_SEARCH_PREFIX) && cfg.is_active === true ? 'Default' : undefined,
    statusText: status.statusText,
    tone: status.tone,
    attention: status.attention,
    enabled: c.enabled,
    // An MCP server is configured the moment it is in the store — its endpoint WAS the
    // setup. Everything else earns it by holding credentials.
    configured: mcp || c.status !== 'not_configured',
    sub: c.short_description || endpointOf(c) || '',
    desc: c.long_description || c.short_description || '',
    developer: provenance?.publisher ?? developer?.name,
    developerUrl: mcp ? undefined : developer?.url,
    mcp,
    // The wire view carries no provenance of its own, so this is honest about what it knows:
    // a publisher we recognise reads `unverified`, and a server the user pointed us at reads
    // `unknown`. Never `verified` from the wire alone — that would claim we checked.
    //
    // The one thing we CAN check is the endpoint: a server installed from our own catalog is
    // matched back to its catalog entry and keeps its real verification, so Playwright MCP no
    // longer reads "Verified" on its directory card and "Unknown publisher" on its detail page
    // one click later. `catalog` is a leaf precisely so this lookup isn't a cycle.
    verification: provenance?.verification ?? (developer ? 'unverified' : 'unknown'),
    url: endpointOf(c),
    tools,
    toolGroups: buildToolGroups(c.tools ?? []),
    prompts: PROMPTS[kind] ?? [],
    perms: permsOf(c),
  }
}

/** The installed connectors, in wire order, as the list renders them. */
export function toEntries(connectors: Connector[]): ConnectorDetailEntry[] {
  return connectors.filter(isInstalled).map(toEntry)
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}

/**
 * The saved server behind an MCP connector row, as the edit flow reopens it (spec §3).
 *
 * Sourced from the row the tab ALREADY holds rather than a second `GET /api/mcp-clients`:
 * the view's `config` is `McpClientStore.public()` (minus `id`/`name`/`enabled`/`has_token`,
 * each of which is on the view itself), so this is the same data one round trip earlier — and
 * one source of truth for what the page is showing.
 *
 * `null` for anything that is not an MCP server: there is no add flow to reopen for a native
 * connector, whose own credential form is the setup surface instead.
 */
export function toMcpEditTarget(c: Connector): McpEditTarget | null {
  if (!c.id.startsWith(MCP_PREFIX)) return null
  const cfg = configOf(c)
  const auth = str(cfg, 'auth_type')
  const provenance = catalogProvenanceFor(endpointOf(c))
  return {
    serverId: c.id.slice(MCP_PREFIX.length),
    name: displayName(c),
    transport: str(cfg, 'transport') === 'stdio' ? 'local' : 'remote',
    url: str(cfg, 'url'),
    command: str(cfg, 'command'),
    args: strings(cfg.args).join(' '),
    envKeys: strings(cfg.env_keys),
    authType: auth === 'bearer' || auth === 'header' || auth === 'oauth' ? auth : 'none',
    authHeader: str(cfg, 'auth_header') || 'Authorization',
    hasToken: c.secrets_public?.has_token === true,
    oauth: mcpOAuthSummary(c),
    perms: permsOf(c),
    publisher: provenance?.publisher ?? undefined,
    verification: provenance?.verification ?? 'unknown',
    curated: provenance !== null,
  }
}

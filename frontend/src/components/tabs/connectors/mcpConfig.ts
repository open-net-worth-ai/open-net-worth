// The MCP connection form: config-snippet parsing, id derivation, and the prefill an EDIT
// reopens the flow on.
//
// Ported from design_handoffs/connectors_handoff/prototype/redesign/connectors.jsx
// (`parsePasted`, `slugify`). Pasting a README's Quick Start block is NOT a separate
// mode — it fills the very same fields the user would have typed, so the parser's whole
// job is to produce a partial `McpConnectionForm` that gets spread over the current one.

import type { OAuthSummary } from './mcpApi'
import type { PermissionMap, VerificationState } from './model'

/** Which transport the step-1 form is currently collecting. */
export type McpTransport = 'remote' | 'local'

/** Remote-server authentication choice. */
export type McpAuth = 'none' | 'key' | 'oauth'

/** Step 1's connection fields, plus the step-3 identity fields derived after a successful probe. */
export interface McpConnectionForm {
  transport: McpTransport
  url: string
  command: string
  args: string
  env: string
  auth: McpAuth
  key: string
  paste: string
  name: string
  serverId: string
  serverIdCustom: boolean
}

export const EMPTY_MCP_FORM: McpConnectionForm = {
  transport: 'remote',
  url: '',
  command: '',
  args: '',
  env: '',
  auth: 'none',
  key: '',
  paste: '',
  name: '',
  serverId: '',
  serverIdCustom: false,
}

/**
 * A SAVED server, as the edit flow reopens it (spec §3 — "reopens the setup drawer pre-filled").
 *
 * Everything here comes from the connector row the tab already holds, which carries
 * `McpClientStore.public()` verbatim — so it can state which secrets EXIST (`hasToken`,
 * `envKeys`) but never what they are.
 */
export interface McpEditTarget {
  /** FIXED for the life of the server: it namespaces every tool (`mcp__<id>__<tool>`) and keys
   *  every permission row, so changing it would orphan them all. The store refuses to patch it. */
  serverId: string
  name: string
  /** Also fixed — the store refuses to patch `transport` (switch by re-adding). */
  transport: McpTransport
  url: string
  command: string
  args: string
  /** Key NAMES only. `public()` exposes `env_keys` because a value may be the spawned
   *  server's own secret. */
  envKeys: string[]
  /** How the saved credential is presented, preserved so an edit can't move an
   *  `X-API-Key` server onto `Authorization` behind the user's back — and so switching AWAY
   *  from `oauth` is a deliberate re-pick, never a silent downgrade to no credential at all. */
  authType: 'none' | 'bearer' | 'header' | 'oauth'
  authHeader: string
  /** Whether a token is stored. The value itself is never returned to the browser. */
  hasToken: boolean
  /** Token-free OAuth status (`McpClientStore._oauth_summary`) — `null` for a server that has
   *  never used `auth_type: 'oauth'`. Drives the edit form's connected-account summary instead
   *  of a token field, and `needs_reauth` is what a stale sign-in reads as here. */
  oauth: OAuthSummary | null
  /** The server's CURRENT per-tool levels — step 4 reflects these rather than resetting. */
  perms: PermissionMap
  /** Provenance, joined back to the curated catalog by endpoint (never by name). */
  publisher?: string
  verification: VerificationState
  /** True when that join HIT — the only source that may claim `verified`/`unverified`. */
  curated: boolean
}

/**
 * Step 1, pre-filled from a saved server.
 *
 * Every secret field starts EMPTY and stays empty unless the user retypes it: a saved token
 * is never echoed back into a form, and env VALUES are not ours to echo either. What each
 * blank means on save is `updateMcpServer`'s contract, and the form says so inline.
 */
export function formFromEditTarget(target: McpEditTarget): McpConnectionForm {
  return {
    ...EMPTY_MCP_FORM,
    transport: target.transport,
    url: target.url,
    command: target.command,
    args: target.args,
    auth: target.authType === 'none' ? 'none' : target.authType === 'oauth' ? 'oauth' : 'key',
    name: target.name,
    serverId: target.serverId,
  }
}

/** What a parsed snippet contributes — spread over the live form, never replacing it. */
export type ParsedSnippet =
  | { transport: 'remote'; url: string; auth: McpAuth }
  | { transport: 'local'; command: string; args: string; env: string }

/** A server entry as it appears inside a config snippet. Every field is untrusted input. */
interface SnippetServer {
  url?: unknown
  headers?: unknown
  command?: unknown
  args?: unknown
  env?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * Best-effort parse of a SINGLE-server config block, as pasted from a README.
 *
 * Accepts the three shapes real docs use — `{"mcpServers": {…}}`, `{"servers": {…}}`,
 * or a bare `{"<name>": {…}}` map — and takes the FIRST entry. Multi-server snippets are
 * deliberately out of scope for this flow (bulk import stays on `/api/mcp-clients/import`).
 *
 * Returns `null` when the text isn't JSON yet, or holds nothing that looks like a server —
 * the caller renders that as "couldn't parse that yet", never as an error.
 */
export function parsePastedConfig(text: string): ParsedSnippet | null {
  let obj: unknown
  try {
    obj = JSON.parse(text)
  } catch {
    return null
  }
  if (!isRecord(obj)) return null

  const wrapped = obj['mcpServers'] ?? obj['servers']
  const servers = isRecord(wrapped) ? wrapped : obj
  const first = Object.values(servers)[0] as SnippetServer | undefined
  if (!isRecord(first)) return null

  if (typeof first.url === 'string' && first.url) {
    return { transport: 'remote', url: first.url, auth: isRecord(first.headers) ? 'key' : 'none' }
  }
  if (typeof first.command === 'string' && first.command) {
    return {
      transport: 'local',
      command: first.command,
      args: Array.isArray(first.args) ? first.args.map(String).join(' ') : '',
      env: isRecord(first.env)
        ? Object.entries(first.env)
            .map(([k, v]) => `${k}=${String(v)}`)
            .join('\n')
        : '',
    }
  }
  return null
}

/** A remote server's URL must actually be one — http(s) plus SOME host. Deliberately does not
 *  require a dotted domain: `http://localhost:8787` and a bare LAN hostname are both real,
 *  common MCP endpoints — this only catches "forgot the scheme" / "not a URL at all", the
 *  cases a probe attempt can't already explain better than this can. */
const URL_RE = /^https?:\/\/\S+/i
export function isValidMcpUrl(value: string): boolean {
  return URL_RE.test(value.trim())
}

/** Server ids are slugs of the NAME — never asked for up front, never on equal footing with it. */
export function slugify(value: string): string {
  return (
    (value || '')
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '') || 'server'
  )
}

/**
 * A slug that doesn't collide with an already-installed server.
 * `foo` taken -> `foo-2` -> `foo-3`. Keeps step 3's id automatic even on a name clash.
 */
export function uniqueServerId(name: string, taken: Iterable<string>): string {
  const base = slugify(name)
  const used = new Set(taken)
  if (!used.has(base)) return base
  let n = 2
  while (used.has(`${base}-${n}`)) n += 1
  return `${base}-${n}`
}

/**
 * The fallback display name when the server doesn't report one in `serverInfo.name`.
 * Host for a URL, last path segment for a command — materially worse than the server's
 * own name, which is exactly why the probe endpoint keeps `serverInfo.name`.
 */
export function guessServerName(
  form: Pick<McpConnectionForm, 'transport' | 'url' | 'command'>,
): string {
  const raw =
    form.transport === 'remote'
      ? form.url.replace(/^https?:\/\//, '').split(/[/.]/)[0] || 'MCP server'
      : form.command.trim().split(/\s+/).pop() || 'MCP server'
  return raw.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/** A server's self-reported name as a display name: an all-lowercase slug (`coinbase`) gets a
 *  capital first letter; anything already mixed-case (`iCloud`, `Kite Connect`) is the
 *  author's own styling and is left alone. */
export function displayName(name: string): string {
  return name && name === name.toLowerCase() ? name[0]!.toUpperCase() + name.slice(1) : name
}

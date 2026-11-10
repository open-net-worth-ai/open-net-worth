// Wire calls for the add-an-MCP-server flow, plus the form→wire translation.
//
// The flow's rule is that an UNREACHED SERVER IS NEVER SAVED (spec §7). That is only possible
// with an endpoint that tests a candidate config WITHOUT persisting it — the pre-existing
// `POST /api/mcp-clients/{id}/test` cannot serve this flow, because it looks the server up by id
// and therefore requires the row to already exist. So step 2 calls the probe, and `POST
// /api/mcp-clients` runs only once the user commits on step 4.
//
// The form speaks the DESIGN's vocabulary (`remote`/`local`, `none`/`key`/`oauth`); the wire
// speaks the backend's (`http`/`stdio`, `none`/`bearer`/`header`). `toWireConfig` is the single
// place that translates, so no component has to know both.

import { authFetch } from '../../../api/client'
import type { McpConnectionForm } from './mcpConfig'
import type { McpTrust } from './model'

/** Backend transport vocabulary (`backend/models/mcp_client.py`). */
export type WireTransport = 'http' | 'stdio'
/**
 * Backend auth vocabulary (`backend/models/mcp_client.py::McpServerConfig.auth_type`).
 *
 * `oauth` rides no `token` of its own — the credential is the server-held `McpOAuth` block
 * (DCR registration + PKCE token set), obtained via the sign-in flow below
 * (`beginOAuth`/`pollOAuth`) and never handled by the browser directly (RFC
 * `2026-08-14-mcp-oauth` §4.4).
 */
export type WireAuthType = 'none' | 'bearer' | 'header' | 'oauth'

/** The connection config both the probe and the create call take. */
export interface McpWireConfig {
  transport: WireTransport
  url: string
  command: string
  args: string[]
  env: Record<string, string>
  auth_type: WireAuthType
  auth_header: string
  token: string
}

/** One tool as the server advertises it in `tools/list`, plus the backend's computed
 *  `read_only` grouping flag (display grouping only — recomputed on every read). */
export interface DiscoveredTool {
  name: string
  description?: string
  read_only?: boolean
}

/**
 * The probe's ONE shape — `ok` discriminates. A connect failure is a 200 with `ok: false`,
 * because the request succeeded and the server is what didn't answer.
 */
export interface McpProbeResult {
  ok: boolean
  /** The server's self-report from the `initialize` handshake; `null` when it reported none. */
  server_info: { name: string | null; version: string | null } | null
  tools: DiscoveredTool[]
  /** `serverInfo.name`, or an endpoint-derived fallback. Pre-fills step 3's Name. */
  suggested_name: string
  /** A slug of the name, already collision-suffixed against the installed servers. */
  suggested_id: string
  /** Who published the thing you are about to trust — `null` on failure, and on a response
   *  from a backend that predates the block. */
  trust: McpTrust | null
  /** The REAL connect error, shown inline — never a generic string. */
  error: string | null
  /** What a 401 asked for — `oauth` (sign in) or `key` (paste a token) — so the form can offer
   *  the fix instead of leaving a bare HTTP 401. Absent from an older backend. */
  needs_auth?: 'oauth' | 'key' | null
}

function probeFailure(error: string): McpProbeResult {
  return {
    ok: false,
    server_info: null,
    tools: [],
    suggested_name: '',
    suggested_id: '',
    trust: null,
    error,
  }
}

/** Split the single Arguments field on whitespace, as the prototype's one-line input implies. */
export function parseArgs(raw: string): string[] {
  return raw.trim() ? raw.trim().split(/\s+/) : []
}

const PEM_BLOCK = /^-----BEGIN ([A-Z0-9 ]+)-----([\s\S]*?)-----END \1-----$/

/**
 * A PEM private key rebuilt into the one shape OpenSSL reads: header, base64 body in 64-char
 * lines, footer. Whatever way it was pasted — real line breaks, literal `\n` escapes (a JSON
 * key file's `privateKey`, quotes included), or all on one line — the child gets a key that
 * parses; Coinbase's CLI, for one, passes the variable to Node's crypto as-is and both the
 * escaped and the one-line forms fail there with ERR_OSSL_UNSUPPORTED. Anything that is not a
 * whole PEM block is returned untouched.
 */
export function normalizePem(value: string): string {
  const unquoted = /^"[\s\S]*"$/.test(value) ? value.slice(1, -1) : value
  const m = PEM_BLOCK.exec(unquoted.replace(/\\n/g, '\n').trim())
  const label = m?.[1]
  const rawBody = m?.[2]
  if (label === undefined || rawBody === undefined) return value
  const body = rawBody.replace(/\s+/g, '')
  const lines = body.match(/.{1,64}/g) ?? []
  return [`-----BEGIN ${label}-----`, ...lines, `-----END ${label}-----`, ''].join('\n')
}

/**
 * `KEY=value` per line → a map. Blank lines and keyless lines are dropped, never sent empty.
 * A value that opens a PEM block (`-----BEGIN …-----`) runs on to its `-----END …-----` line,
 * so a private key pasted with its own line breaks stays one value — and the saved set,
 * revealed back into the box as `KEY=value` lines, parses to exactly what was saved.
 */
export function parseEnv(raw: string): Record<string, string> {
  const out: Record<string, string> = {}
  let pemKey: string | null = null
  for (const line of raw.split('\n')) {
    const trimmed = line.trim()
    if (pemKey !== null) {
      out[pemKey] += '\n' + trimmed
      if (/^-----END [A-Z0-9 ]+-----"?$/.test(trimmed)) pemKey = null
      continue
    }
    if (!trimmed) continue
    const eq = trimmed.indexOf('=')
    if (eq <= 0) continue
    const key = trimmed.slice(0, eq).trim()
    const value = trimmed.slice(eq + 1).trim()
    out[key] = value
    if (/^"?-----BEGIN [A-Z0-9 ]+-----/.test(value) && !/-----END [A-Z0-9 ]+-----"?$/.test(value)) {
      pemKey = key
    }
  }
  for (const [key, value] of Object.entries(out)) out[key] = normalizePem(value)
  return out
}

/** How a SAVED server presents its credential, so an edit can't silently change the scheme. */
export interface SavedAuth {
  auth_type: WireAuthType
  auth_header: string
}

/**
 * Translate the step-1 form into the wire config the probe and create/update calls share.
 *
 * The form's Authentication picker is three-valued (`none`/`key`/`oauth`) while the wire is
 * four-valued (`none`/`bearer`/`header`), so "API key" maps to `bearer` — EXCEPT on an edit of a
 * server already using a custom `header`, which `savedAuth` preserves. Without that, editing a
 * server whose credential rides in `X-API-Key` would silently move it to `Authorization` and
 * break it, since the picker has no way to say which header it meant.
 */
export function toWireConfig(form: McpConnectionForm, savedAuth?: SavedAuth): McpWireConfig {
  const remote = form.transport === 'remote'
  const credentialed = remote && form.auth === 'key'
  const custom = credentialed && savedAuth?.auth_type === 'header'
  const oauth = remote && form.auth === 'oauth'
  return {
    transport: remote ? 'http' : 'stdio',
    url: remote ? form.url.trim() : '',
    command: remote ? '' : form.command.trim(),
    args: remote ? [] : parseArgs(form.args),
    env: remote ? {} : parseEnv(form.env),
    auth_type: oauth ? 'oauth' : credentialed ? (custom ? 'header' : 'bearer') : 'none',
    auth_header: custom ? savedAuth?.auth_header || 'Authorization' : 'Authorization',
    // `oauth` carries no browser-held token — the credential lives server-side in the
    // `McpOAuth` block, minted by the sign-in flow, never by this form.
    token: credentialed ? form.key : '',
  }
}

async function errorDetail(res: Response, fallback: string): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
  const detail = body?.detail
  return typeof detail === 'string' && detail ? detail : `${fallback} (${res.status})`
}

// ── OAuth sign-in (RFC `2026-08-14-mcp-oauth` §4.2) ─────────────────────────────
//
// `POST /api/mcp-clients/oauth/begin` runs discovery + DCR + PKCE and hands back an
// `authorize_url` to open in a popup, or an honest `{ok: false, reason, error}` — always a 200,
// matching the probe's own "the request succeeded, the server is what didn't answer" convention
// (`backend/mcp/oauth_flow.py::begin`). `GET /api/mcp-clients/oauth/{flow_id}` polls that flow's
// outcome and NEVER returns a token. The popup+poll lifecycle itself lives in `oauthSignIn.ts`;
// these two are the thin wire calls it (and anything else that needs a sign-in) drives.

/** Token-free OAuth status for one server — `McpClientStore._oauth_summary`'s exact shape,
 *  echoed under the `oauth` key of every server `public()`/`public_one()` returns. */
export interface OAuthSummary {
  connected: boolean
  issuer_host: string
  scope: string
  expires_at: number | null
  needs_reauth: boolean
}

/** `{url}` for the add flow (no server exists yet) or `{server_id}` to reconnect/re-authorize an
 *  existing one — exactly one of the two is meaningful per call. `client_id` is the manual
 *  escape hatch (RFC §4.6's "prompt the user" row), sent only on a retry after a prior `begin`
 *  answered `reason: 'no_registration'`. */
export type OAuthBeginBody =
  | { url: string; client_id?: string }
  | { server_id: string; client_id?: string }

/**
 * `begin()`'s ONE response shape — `ok` discriminates, matching the probe convention.
 *
 * On failure, `error` is already the exact user-facing sentence (RFC §4.6's degradation table —
 * `oauth_flow.py`'s `_fail()` calls pass that text verbatim), so nothing here re-hardcodes a
 * second copy of that table: this module renders `error` as-is and branches on `reason` only for
 * the two rows that need a DIFFERENT affordance, not different words —
 * `no_registration` (`needs_client_id`, the manual Client ID field) and `dcr_rejected`
 * (`redirect_uris`, so the user can register them by hand at the vendor).
 */
export interface OAuthBeginResult {
  ok: boolean
  authorize_url?: string
  flow_id?: string
  expires_in?: number
  reason?: string
  error?: string
  needs_client_id?: boolean
  redirect_uris?: string[]
}

export async function beginOAuth(body: OAuthBeginBody): Promise<OAuthBeginResult> {
  try {
    const res = await authFetch('/api/mcp-clients/oauth/begin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      return { ok: false, error: await errorDetail(res, 'Could not start the sign-in') }
    }
    return (await res.json()) as OAuthBeginResult
  } catch {
    return { ok: false, error: 'Could not start the sign-in — the request failed.' }
  }
}

/** `{status: pending|ok|error, error}` — NEVER a token (RFC §4.2 "Learning it finished"). */
export interface OAuthPollResult {
  status: 'pending' | 'ok' | 'error'
  error: string | null
}

export async function pollOAuth(flowId: string): Promise<OAuthPollResult> {
  try {
    const res = await authFetch(`/api/mcp-clients/oauth/${encodeURIComponent(flowId)}`, {
      cache: 'no-store',
    })
    if (!res.ok) return { status: 'error', error: 'Could not check the sign-in status.' }
    return (await res.json()) as OAuthPollResult
  } catch {
    return { status: 'error', error: 'Could not check the sign-in status — the request failed.' }
  }
}

/** One server, secret-stripped — `McpClientStore.public_one()`'s shape (the fields this module
 *  actually reads; the wire carries more). Returned by both `listMcpServers` and
 *  `testMcpServer`. */
export interface PublicMcpServer {
  id: string
  name: string
  url: string
  auth_type: WireAuthType
  last_status: string | null
  last_error: string | null
  discovered_tools: DiscoveredTool[]
  publisher: string | null
  verification: 'verified' | 'unverified' | 'unknown'
  homepage_url: string | null
  repo_url: string | null
  endpoint: string
  oauth: OAuthSummary
}

/** Every configured server, secret-stripped. Used, post sign-in, to find the dormant row the
 *  OAuth callback minted for a brand-new add (see `oauthSignIn.ts`'s doc comment) — there is no
 *  narrower read, since the flow that created it never learns its id any other way. */
export async function listMcpServers(): Promise<PublicMcpServer[]> {
  const res = await authFetch('/api/mcp-clients')
  if (!res.ok) return []
  const body = (await res.json().catch(() => null)) as { servers?: PublicMcpServer[] } | null
  return body?.servers ?? []
}

/**
 * Connect + discover tools against an EXISTING saved server (`POST /api/mcp-clients/{id}/test`).
 *
 * This is the OAuth add/edit flow's "Test connect": the id-less probe (`probeMcpServer`) has no
 * field to carry a server-held OAuth token, so an OAuth server can only be reached once its row
 * exists — which, for the add flow, is already true the instant sign-in completes (the callback
 * persists it dormant; see `oauthSignIn.ts`). Never throws on a connect failure — the result
 * carries `last_status`/`last_error` with an empty `discovered_tools`, same as the probe.
 */
export async function testMcpServer(serverId: string): Promise<PublicMcpServer | null> {
  try {
    const res = await authFetch(`/api/mcp-clients/${encodeURIComponent(serverId)}/test`, {
      method: 'POST',
    })
    if (!res.ok) return null
    return (await res.json()) as PublicMcpServer
  } catch {
    return null
  }
}

/**
 * Step 2. Reach the server — `initialize` + `tools/list` — and persist NOTHING.
 *
 * A connect failure is a RESULT, not an exception: the step-2 error state renders the server's
 * real error inline with Back and Retry, so a refused connection must not read the same as the
 * probe endpoint itself being unavailable.
 */
export async function probeMcpServer(
  config: McpWireConfig,
  probeId?: string,
): Promise<McpProbeResult> {
  try {
    const res = await authFetch('/api/mcp-clients/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(probeId ? { ...config, probe_id: probeId } : config),
    })
    if (!res.ok) return probeFailure(await errorDetail(res, 'Could not reach the server'))
    return (await res.json()) as McpProbeResult
  } catch {
    return probeFailure('Could not reach the server — the request failed.')
  }
}

/**
 * What the running probe has printed so far — the live half of Test connect.
 *
 * A first `npx -y <package>` install downloads the package BEFORE the MCP server it should
 * spawn exists (one real vendor's package: 264 MB, 61s of download; 3.6s warm), so the call
 * above can legitimately run for minutes. The backend drains the child's stderr into a
 * bounded tail; polling it here is what turns an opaque spinner into "npm is still fetching".
 *
 * Polled, not streamed, and the POST above stays synchronous: it owns the spawned child, and
 * the child's process group is reaped when it returns, on every path. A background job with a
 * stream attached would put an orphaned `npx` tree at stake to deliver the same few lines.
 *
 * Never throws and never blocks the flow — an unknown token, a swept channel and a network
 * blip are all just "no new lines", and the POST carries the real answer regardless.
 */
export interface McpProbeProgress {
  running: boolean
  /** Seconds the probe has been going, by the SERVER's clock. */
  elapsed: number
  /** The tail of the child's output — already sanitized and capped by the backend. */
  lines: string[]
}

export async function fetchProbeProgress(probeId: string): Promise<McpProbeProgress | null> {
  try {
    const res = await authFetch(
      `/api/mcp-clients/probe/${encodeURIComponent(probeId)}/progress`,
      // Same URL every second, by design — never let the HTTP cache answer one of them with
      // an earlier tail. (`authFetch`'s de-dup is in-flight only, so a poll that outruns its
      // predecessor safely shares that response instead of piling up.)
      { cache: 'no-store' },
    )
    if (!res.ok) return null
    const body = (await res.json()) as Partial<McpProbeProgress> | null
    if (!body || typeof body !== 'object') return null
    return {
      running: !!body.running,
      elapsed: typeof body.elapsed === 'number' ? body.elapsed : 0,
      lines: Array.isArray(body.lines) ? body.lines.filter((l) => typeof l === 'string') : [],
    }
  } catch {
    return null
  }
}

/**
 * Mint the opaque token naming one probe's progress channel.
 *
 * Client-minted because the POST that starts the probe has not returned yet — there is no
 * earlier moment for the server to hand one out (the idempotency-key shape). The backend
 * treats it as an opaque `[A-Za-z0-9_-]{8,64}` key and answers "no channel" for anything
 * else, so a bad one costs the progress display, never the connection test.
 *
 * `randomUUID()` requires a secure context (HTTPS or localhost), but this app explicitly
 * supports plain-HTTP LAN access (`AUTH_TRUST_LOOPBACK`, QR-scan-from-phone setup) — exactly
 * when its fallback fires. `getRandomValues()`, unlike `randomUUID()`, is NOT gated behind a
 * secure context, so it covers that case with a real CSPRNG instead of falling all the way to
 * `Math.random()` — which matters most under `AUTH_TRUST_LOOPBACK=true`, where any local
 * process can poll the progress endpoint with no login and an unguessable token is the only
 * thing standing between two probes. `Math.random()` is kept as the last resort for a runtime
 * with no `crypto` at all.
 */
export function newProbeToken(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID().replace(/-/g, '')
  if (c && typeof c.getRandomValues === 'function') {
    const bytes = c.getRandomValues(new Uint8Array(16))
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  }
  return `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`
}

/** An MCP server's id in the CONNECTOR namespace (`mcp:<server_id>`), which is how the
 *  family-agnostic permissions routes address it — never the bare server id. */
export function mcpConnectorId(serverId: string): string {
  return `mcp:${serverId}`
}

/**
 * Write ONE tool's level. Returns whether it landed.
 *
 * There is deliberately no MCP-only permissions endpoint: the ladder is ONE ladder for every
 * connector family, so an MCP server is addressed on the shared route as `mcp:<server_id>`.
 */
export async function setToolPermission(
  connectorId: string,
  tool: string,
  level: string,
): Promise<boolean> {
  const res = await authFetch(
    `/api/connectors/${encodeURIComponent(connectorId)}/permissions/${encodeURIComponent(tool)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level }),
    },
  )
  return res.ok
}

/**
 * Step 4's commit. Only ever called after a successful probe.
 *
 * Permissions are NOT part of the create body — they live in `tool_permissions`, where an
 * ABSENT row already reads as `needs_approval`. So only the tools the user moved OFF the
 * cautious default are written, each through the permissions choke.
 *
 * A FAILED PERMISSION WRITE FAILS THE ADD, loudly. The original version fired these and
 * ignored the result entirely, which meant a `blocked` chosen on step 4 could silently land
 * as `needs_approval` — a tool the user had just blocked would be offered to Lucius, one
 * confirm away from running, with the flow reporting success. The server does exist by
 * then, so the message says so rather than implying nothing happened; the levels are the
 * part that needs another go.
 */
export async function createMcpServer(input: {
  id: string
  name: string
  config: McpWireConfig
  /** Per-tool levels chosen on step 4. Every tool defaults to `needs_approval` (spec §4). */
  tool_permissions: Record<string, string>
  /** False saves it switched off and untested (the add form's Save); the server is not reached. */
  enabled?: boolean
}): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    const res = await authFetch('/api/mcp-clients', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: input.id,
        name: input.name,
        ...input.config,
        enabled: input.enabled ?? true,
      }),
    })
    if (!res.ok) return { ok: false, error: await errorDetail(res, 'Could not add the server') }

    const connectorId = mcpConnectorId(input.id)
    let failed = 0
    for (const [tool, level] of Object.entries(input.tool_permissions)) {
      if (level === 'needs_approval') continue
      if (!(await setToolPermission(connectorId, tool, level))) failed += 1
    }
    if (failed > 0) return { ok: false, error: permissionWriteError(failed) }
    return { ok: true }
  } catch {
    return { ok: false, error: 'Could not add the server — the request failed.' }
  }
}

/** Said the same way on both commit paths: the connection landed, the levels did not, and
 *  the levels are what matters for what Lucius may fire. */
function permissionWriteError(failed: number): string {
  const changes = `${failed} permission ${failed === 1 ? 'level' : 'levels'}`
  return `The server was saved, but ${changes} could not be written — those tools are still at Needs approval. Try again.`
}

/**
 * The edit flow's commit (spec §3, "Edit connection … reopens the setup drawer pre-filled").
 *
 * Two fields are deliberately NOT sent, because the store refuses to patch either: `id` (it
 * namespaces every tool as `mcp__<id>__<tool>` and keys every permission row) and `transport`
 * (switch by re-adding). Both are therefore fixed in the form as well, rather than editable
 * here and silently ignored there.
 *
 * SECRETS. Neither a token nor an env VALUE is ever returned to the browser, so neither can be
 * echoed back into the form and neither can round-trip untouched:
 *   * `token` — blank means "keep the saved one", which is the store's own rule, so a blank
 *     field is simply omitted;
 *   * `env` — has NO such rule and is a whole-map REPLACE, so an untouched box (`replaceEnv`
 *     false) omits the key entirely rather than sending `KEY=` pairs that would silently wipe
 *     every saved value. That is exactly the bug the outgoing form shipped.
 *
 * Unlike the create path, a failed permission write DOES fail the result: on an edit the change
 * can be a move DOWN (allow → block), so silently leaving the old level in place would be the
 * unsafe direction. The connection change has already landed at that point, and the message
 * says so — a retry is idempotent.
 */
export async function updateMcpServer(input: {
  id: string
  name: string
  config: McpWireConfig
  /** True only when the user actually rewrote the environment box. */
  replaceEnv: boolean
  /** Only the levels that CHANGED — including a move back to `needs_approval`, which on an
   *  edit is a real downgrade from a stored `always_allow`, not the absent-row default. */
  tool_permissions: Record<string, string>
  /** True ONLY for the OAuth add flow's finish — the row this PATCHes already exists, created
   *  DORMANT by the callback the instant sign-in completed (`oauth_flow.complete`, RFC
   *  `2026-08-14-mcp-oauth` §4.2's "brand-new server" branch). Completing the add flow is what
   *  arms it, mirroring `create_mcp_client`'s "a server starts dormant until the user enables
   *  it" rule for every other transport. Omitted (the ordinary edit path) leaves `enabled`
   *  untouched. */
  enable?: boolean
}): Promise<{ ok: true } | { ok: false; error: string }> {
  const { config } = input
  const remote = config.transport === 'http'
  const body: Record<string, unknown> = { name: input.name }
  if (input.enable) body.enabled = true
  if (remote) {
    body.url = config.url
    body.auth_type = config.auth_type
    body.auth_header = config.auth_header
    if (config.token.trim()) body.token = config.token
  } else {
    body.command = config.command
    body.args = config.args
    if (input.replaceEnv) body.env = config.env
  }

  try {
    const res = await authFetch(`/api/mcp-clients/${encodeURIComponent(input.id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) return { ok: false, error: await errorDetail(res, 'Could not save the server') }

    const connectorId = mcpConnectorId(input.id)
    let failed = 0
    for (const [tool, level] of Object.entries(input.tool_permissions)) {
      if (!(await setToolPermission(connectorId, tool, level))) failed += 1
    }
    if (failed > 0) return { ok: false, error: permissionWriteError(failed) }
    return { ok: true }
  } catch {
    return { ok: false, error: 'Could not save the server — the request failed.' }
  }
}

/**
 * Remove a server. The backend also forgets every permission granted to it, so a server
 * re-added under the same id starts cautious instead of inheriting its predecessor's trust.
 */
export async function deleteMcpServer(
  serverId: string,
): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    const res = await authFetch(`/api/mcp-clients/${encodeURIComponent(serverId)}`, {
      method: 'DELETE',
    })
    if (!res.ok) return { ok: false, error: await errorDetail(res, 'Could not remove the server') }
    return { ok: true }
  } catch {
    return { ok: false, error: 'Could not remove the server — the request failed.' }
  }
}

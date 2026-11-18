// The OAuth path of Settings → MCP (flag mcp_oauth): claude.ai, Claude on the phone, Claude
// Desktop's custom connectors and ChatGPT. The status call answers 404 while the flag is off.
//
// The pieces live here — the status, the public address every OAuth URL is built from (prefilled
// from the page's own https origin, saved only on Save), the address to paste, the apps connected
// with Revoke, and app credentials for an app that can't use the built-in identity. The pane that
// lays them out as a checklist is McpOAuthPane.tsx.
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'
import { Badge, Btn, Field, row } from '../ds'
import type { Toast } from '../ds'

export interface OAuthApp {
  grant_id: string
  name: string
  host: string
  scopes: string[]
  connected_at: string
  last_used_at: string | null
}

export interface OAuthClient {
  client_id: string
  name: string
  redirect_uris: string[]
  created_at: string
}

export interface OAuthStatus {
  available: boolean
  reasons: string[]
  public_url: string
  public_url_source: 'env' | 'stored'
  mcp_url: string
  apps: OAuthApp[]
  clients: OAuthClient[]
}

interface Created {
  client_id: string
  client_secret: string
}

export const KEY = '/api/mcp/oauth'

export const REASONS: Record<string, string> = {
  flag_off: 'The feature is off.',
  no_public_url: 'Set the public HTTPS address below.',
  invalid_public_url: 'The public address must be https:// and reachable from the internet.',
  no_password: 'Set a password in Settings → Security — the consent page asks for it.',
  auth_disabled: 'Sign-in is turned off on this instance.',
  loopback_trusted:
    'In Settings → Security, turn off "Don\'t ask for the password on this computer": behind a proxy or tunnel every visitor looks like this computer.',
  demo: 'Not available on the public demo.',
  gateway: 'Not available behind the Open Net Worth gateway yet.',
  desktop: 'The desktop app only accepts connections from this computer.',
}

async function loadStatus(): Promise<OAuthStatus | null> {
  const r = await authFetch(KEY)
  if (r.status === 404) return null
  if (!r.ok) throw new Error(String(r.status))
  return (await r.json()) as OAuthStatus
}

async function send(path: string, method: string, body?: unknown): Promise<Response> {
  return authFetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

async function detail(r: Response): Promise<string> {
  try {
    const body = (await r.json()) as { detail?: unknown }
    return typeof body.detail === 'string' ? body.detail : `Failed (${r.status})`
  } catch {
    return `Failed (${r.status})`
  }
}

function copy(text: string, what: string, toast: Toast): void {
  void navigator.clipboard
    .writeText(text)
    .then(() => toast(`${what} copied`))
    .catch(() => toast('Could not copy — select it manually'))
}

function when(iso: string | null): string {
  if (!iso) return 'never'
  const d = new Date(iso.replace(/\.\d+Z$/, 'Z'))
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

/** The OAuth server's status; ``null`` while the flag is off (the call answers 404). */
export function useOAuthStatus(): { status: OAuthStatus | null; refresh: () => void } {
  const { data, refresh } = useCachedFetchState<OAuthStatus | null>(KEY, loadStatus, {
    staleTime: 0,
  })
  return { status: data ?? null, refresh: () => void refresh() }
}

/** Reasons nothing on this instance's settings can fix: the path just isn't offered here. */
export const BLOCKERS = new Set(['demo', 'gateway', 'desktop'])

/** What the OAuth card says about its path. */
export function oauthState(status: OAuthStatus | null): { label: string; tone: 'good' | 'warn' } {
  if (!status) return { label: 'Not set up', tone: 'warn' }
  if (status.available) return { label: 'Ready', tone: 'good' }
  if (status.reasons.some((r) => BLOCKERS.has(r)))
    return { label: 'Not available here', tone: 'warn' }
  const steps = new Set(
    status.reasons.map((r) => (r === 'invalid_public_url' ? 'no_public_url' : r)),
  )
  const n = steps.size
  return { label: n === 1 ? '1 step left' : `${n} steps left`, tone: 'warn' }
}

export function ConnectorAddress({ mcpUrl, toast }: { mcpUrl: string; toast: Toast }) {
  return (
    <div className="mcp-hosted-status">
      <Field
        span="full"
        label="Connector address"
        help="Each app gets read access unless you allow writes when you approve it."
      >
        <div className="mcp-hosted-url">
          <input
            className="rd-input"
            aria-label="Connector address"
            readOnly
            value={mcpUrl}
            data-ctx-secret=""
            onFocus={(e) => e.target.select()}
          />
          <Btn size="xs" onClick={() => copy(mcpUrl, 'Address', toast)}>
            Copy
          </Btn>
        </div>
      </Field>
      <ul className="mcp-hosted-list">
        <li>
          <strong>claude.ai</strong> Customize → Connectors → Add custom connector. Paste the
          address and leave the advanced fields empty.
        </li>
        <li>
          <strong>ChatGPT</strong> Settings → Connectors, with Developer mode on. Add a custom
          connector with the address and choose OAuth.
        </li>
      </ul>
    </div>
  )
}

export function PublicUrl({
  status,
  refresh,
  toast,
}: {
  status: OAuthStatus
  refresh: () => void
  toast: Toast
}) {
  const [value, setValue] = useState(status.public_url || suggestedPublicUrl())
  const fromEnv = status.public_url_source === 'env'
  async function save() {
    if (
      status.apps.length &&
      value !== status.public_url &&
      !window.confirm('Changing the address disconnects every connected app. Continue?')
    )
      return
    const r = await send(`${KEY}/public-url`, 'PUT', { public_url: value })
    if (!r.ok) return toast(await detail(r))
    toast('Public address saved')
    refresh()
  }
  return (
    <Field
      label="Public HTTPS address"
      help={
        fromEnv
          ? 'Set by ONW_PUBLIC_URL on the server.'
          : !status.public_url && value && value === suggestedPublicUrl()
            ? "Filled in from the address you're using now. It's saved only when you click Save."
            : 'The address the internet reaches this instance at, like https://wealth.example.com.'
      }
    >
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          className="rd-input"
          aria-label="Public HTTPS address"
          value={value}
          disabled={fromEnv}
          placeholder="https://wealth.example.com"
          onChange={(e) => setValue(e.target.value)}
        />
        {fromEnv ? null : (
          <Btn size="xs" onClick={() => void save()} disabled={value === status.public_url}>
            Save
          </Btn>
        )}
      </div>
    </Field>
  )
}

export function ConnectedApps({
  apps,
  refresh,
  toast,
}: {
  apps: OAuthApp[]
  refresh: () => void
  toast: Toast
}) {
  async function revoke(app: OAuthApp) {
    if (!window.confirm(`Disconnect ${app.name} (${app.host})?`)) return
    const r = await send(`${KEY}/apps/${encodeURIComponent(app.grant_id)}`, 'DELETE')
    if (!r.ok) return toast(await detail(r))
    toast(`${app.name} disconnected`)
    refresh()
  }
  return (
    <div>
      {apps.length === 0 ? (
        <span className="rd-help">No app is connected.</span>
      ) : (
        apps.map((app) => (
          <div key={app.grant_id} style={{ marginBottom: 8 }}>
            {row(
              <>
                {app.name}{' '}
                <Badge tone={app.scopes.includes('write') ? 'warn' : ''}>
                  {app.scopes.includes('write') ? 'Read and write' : 'Read'}
                </Badge>
              </>,
              `${app.host} · connected ${when(app.connected_at)} · last used ${when(app.last_used_at)}`,
              <Btn size="xs" variant="danger" onClick={() => void revoke(app)}>
                Revoke
              </Btn>,
            )}
          </div>
        ))
      )}
    </div>
  )
}

export function Credentials({
  clients,
  refresh,
  toast,
}: {
  clients: OAuthClient[]
  refresh: () => void
  toast: Toast
}) {
  const [name, setName] = useState('')
  const [redirect, setRedirect] = useState('')
  const [created, setCreated] = useState<Created | null>(null)
  async function create() {
    const r = await send(`${KEY}/clients`, 'POST', { name, redirect_uris: [redirect] })
    if (!r.ok) return toast(await detail(r))
    const body = (await r.json()) as { created: Created }
    setCreated(body.created)
    setName('')
    setRedirect('')
    refresh()
  }
  async function remove(c: OAuthClient) {
    if (!window.confirm(`Delete the credentials for ${c.name}? Apps using them are disconnected.`))
      return
    const r = await send(`${KEY}/clients/${encodeURIComponent(c.client_id)}`, 'DELETE')
    if (!r.ok) return toast(await detail(r))
    refresh()
  }
  return (
    <div>
      {clients.map((c) => (
        <div key={c.client_id} style={{ marginBottom: 8 }}>
          {row(
            c.name,
            c.redirect_uris.join(', '),
            <Btn size="xs" variant="ghost" onClick={() => void remove(c)}>
              Delete
            </Btn>,
          )}
        </div>
      ))}
      {created ? (
        <div style={{ marginBottom: 8 }}>
          <div className="rd-code" data-ctx-secret="">
            {`Client ID: ${created.client_id}\nClient secret: ${created.client_secret}\n\nCopy the secret now — it is not shown again.`}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
            <Btn size="xs" onClick={() => copy(created.client_id, 'Client ID', toast)}>
              Copy client ID
            </Btn>
            <Btn size="xs" onClick={() => copy(created.client_secret, 'Client secret', toast)}>
              Copy secret
            </Btn>
          </div>
        </div>
      ) : null}
      <Field span="full" label="App name" help="Shown in Connected apps, so you can tell it apart.">
        <input
          className="rd-input"
          aria-label="App name"
          placeholder="My MCP client"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Field
        span="full"
        label="Callback URL"
        help="Where the app receives your approval. Find it in the app's OAuth settings, usually called redirect URI or callback URL. Sign-ins go only there."
      >
        <input
          className="rd-input"
          aria-label="Callback URL"
          placeholder="https://app.example.com/oauth/callback"
          value={redirect}
          onChange={(e) => setRedirect(e.target.value)}
        />
      </Field>
      <div>
        <Btn size="xs" onClick={() => void create()} disabled={!name.trim() || !redirect.trim()}>
          Create client ID and secret
        </Btn>
      </div>
    </div>
  )
}

const LOOPBACK = /^(127\.0\.0\.1|localhost|\[::1\])$/

/** The page's own address, when it is one the internet could reach: the owner is looking at it
 * in their address bar, so it is a safe suggestion. It is only ever saved by the owner's Save;
 * the server never takes its address from a request. */
export function suggestedPublicUrl(
  loc: Pick<Location, 'protocol' | 'hostname' | 'origin'> = window.location,
): string {
  return loc.protocol === 'https:' && !LOOPBACK.test(loc.hostname) ? loc.origin : ''
}

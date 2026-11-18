// The OAuth path of Settings → MCP (flag mcp_oauth): claude.ai, Claude on the phone, Claude
// Desktop's custom connectors and ChatGPT. The status call answers 404 while the flag is off.
//
// The pieces live here — the status, the public address every OAuth URL is built from (prefilled
// from the page's own https origin, saved only on Save), the address to paste, the apps connected
// with Revoke, and app credentials for an app that can't use the built-in identity. The pane that
// lays them out as a checklist is McpOAuthPane.tsx.
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { Badge, Btn, Field, row } from '../ds'
import type { Toast } from '../ds'
import { ConfirmDialog } from '../../../ui/ConfirmDialog'
import type { ConfirmDialogData } from '../../../ui/ConfirmDialog'
import { KEY, suggestedPublicUrl } from './mcpOAuth'
import type { OAuthApp, OAuthClient, OAuthStatus } from './mcpOAuth'

/** A pending confirmation: what the app's dialog shows, and what runs on Confirm. */
type Ask = { data: ConfirmDialogData; run: () => void } | null

function AskDialog({ ask, setAsk }: { ask: Ask; setAsk: (a: Ask) => void }) {
  return (
    <ConfirmDialog
      data={ask?.data ?? null}
      onConfirm={() => {
        const run = ask?.run
        setAsk(null)
        run?.()
      }}
      onCancel={() => setAsk(null)}
    />
  )
}

interface Created {
  client_id: string
  client_secret: string
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
  const [ask, setAsk] = useState<Ask>(null)
  function save() {
    if (status.apps.length && value !== status.public_url) {
      setAsk({
        data: {
          title: 'Change the public address?',
          body: 'Every connected app is disconnected and has to be approved again.',
          confirmLabel: 'Change address',
          danger: true,
        },
        run: () => void put(),
      })
      return
    }
    void put()
  }
  async function put() {
    const r = await send(`${KEY}/public-url`, 'PUT', { public_url: value })
    if (!r.ok) return toast(await detail(r))
    toast('Public address saved')
    refresh()
  }
  return (
    <>
      <AskDialog ask={ask} setAsk={setAsk} />
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
            <Btn size="xs" onClick={save} disabled={value === status.public_url}>
              Save
            </Btn>
          )}
        </div>
      </Field>
    </>
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
  const [ask, setAsk] = useState<Ask>(null)
  function revoke(app: OAuthApp) {
    setAsk({
      data: {
        title: `Disconnect ${app.name}?`,
        body: `${app.host} loses access to your book. It can connect again only if you approve it again.`,
        confirmLabel: 'Disconnect',
        danger: true,
      },
      run: () => void disconnect(app),
    })
  }
  async function disconnect(app: OAuthApp) {
    const r = await send(`${KEY}/apps/${encodeURIComponent(app.grant_id)}`, 'DELETE')
    if (!r.ok) return toast(await detail(r))
    toast(`${app.name} disconnected`)
    refresh()
  }
  return (
    <div>
      <AskDialog ask={ask} setAsk={setAsk} />
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
              <Btn size="xs" variant="danger" onClick={() => revoke(app)}>
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
  formOpen,
  setFormOpen,
}: {
  clients: OAuthClient[]
  refresh: () => void
  toast: Toast
  formOpen: boolean
  setFormOpen: (open: boolean) => void
}) {
  const [name, setName] = useState('')
  const [redirect, setRedirect] = useState('')
  const [created, setCreated] = useState<Created | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [ask, setAsk] = useState<Ask>(null)
  async function create() {
    setError(null)
    const r = await send(`${KEY}/clients`, 'POST', { name, redirect_uris: [redirect.trim()] })
    if (!r.ok) return setError(await detail(r))
    const body = (await r.json()) as { created: Created }
    setCreated(body.created)
    setName('')
    setRedirect('')
    setFormOpen(false)
    refresh()
  }
  function remove(c: OAuthClient) {
    setAsk({
      data: {
        title: `Delete the client ID for ${c.name}?`,
        body: 'Apps using it are disconnected and can no longer sign in with it.',
        confirmLabel: 'Delete',
        danger: true,
      },
      run: () => void destroy(c),
    })
  }
  async function destroy(c: OAuthClient) {
    const r = await send(`${KEY}/clients/${encodeURIComponent(c.client_id)}`, 'DELETE')
    if (!r.ok) return toast(await detail(r))
    refresh()
  }
  return (
    <div className="mcp-cred">
      <AskDialog ask={ask} setAsk={setAsk} />
      {clients.map((c) => (
        <div key={c.client_id} style={{ marginBottom: 8 }}>
          {row(
            c.name,
            c.redirect_uris.join(', '),
            <Btn size="xs" variant="ghost" onClick={() => remove(c)}>
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
      {formOpen ? (
        <>
          <Field
            span="full"
            label="App name"
            help="Shown in Connected apps, so you can tell it apart."
          >
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
              onChange={(e) => {
                setRedirect(e.target.value)
                setError(null)
              }}
            />
            {error ? <span className="mcp-cred__error">{error}</span> : null}
          </Field>
          <div style={{ display: 'flex', gap: 8 }}>
            <Btn
              size="xs"
              onClick={() => void create()}
              disabled={!name.trim() || !redirect.trim()}
            >
              Create
            </Btn>
            <Btn size="xs" variant="ghost" onClick={() => setFormOpen(false)}>
              Cancel
            </Btn>
          </div>
        </>
      ) : (
        <div className="mcp-other">
          <span className="rd-help">
            {clients.length ? '' : 'If an app asks for a client ID and secret, create them here.'}
          </span>
          <Btn size="xs" onClick={() => setFormOpen(true)}>
            {clients.length ? 'New client ID and secret' : 'Create client ID and secret'}
          </Btn>
        </div>
      )}
    </div>
  )
}

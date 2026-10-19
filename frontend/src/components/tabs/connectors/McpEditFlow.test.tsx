// @vitest-environment jsdom
//
// Editing and REMOVING a saved MCP server (spec §3's kebab) — driven through `ConnectorsTab`
// rather than the flow in isolation, because the defects these cover were all in the WIRING:
// `Edit configuration` opened the directory (a dead end — it browses what you could add, not
// what you have), and `Remove connector` was a toast explaining why nothing happened.
//
// The contracts worth breaking a build over:
//   * an edit opens PRE-FILLED, a saved secret is never in the LIST payload, and the eye is
//     what fetches it — one control, on demand, from the operator-only reveal endpoint;
//   * Server ID is not editable — it namespaces the tools and keys the permission rows;
//   * Test connect still gates the save, so an edit cannot break a working server;
//   * step 4 shows the levels the server ACTUALLY has, not a reset to `ask`;
//   * remove asks first, calls DELETE, and does not claim success when it failed.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import { ConnectorsTab } from './ConnectorsTab'
import { clearQueryCache } from '../../../state/queryCache'
import type { Connector } from './types'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => undefined,
}))

/** A label matched from its start — a required field's asterisk is appended inside the label. */
function labelStartingWith(label: string): RegExp {
  return new RegExp('^' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
}

/** A saved remote server, shaped as the connector view emits it: the secret-stripped
 *  `McpClientStore.public()` in `config` — so `env_keys` is present and no secret value is, and
 *  `has_token` rides in `secrets_public`. */
const FIRECRAWL: Connector = {
  id: 'mcp:firecrawl',
  name: 'Firecrawl',
  kind: 'mcp',
  enabled: true,
  config: {
    transport: 'http',
    url: 'https://mcp.firecrawl.dev',
    command: '',
    args: [],
    auth_type: 'bearer',
    auth_header: 'Authorization',
    env_keys: [],
    verification: 'unknown',
    last_status: 'ok',
    last_error: null,
    endpoint: 'https://mcp.firecrawl.dev',
  },
  secrets_public: { has_token: true },
  // The SHARED four-value vocabulary — the view never passes `last_status` through as `status`.
  status: 'active',
  pausable: true,
  short_description: 'Crawl and scrape sites into clean markdown',
  tools: [
    { name: 'firecrawl_scrape', description: '', source: 'mcp', level: 'blocked' },
    { name: 'firecrawl_search', description: '', source: 'mcp', level: 'needs_approval' },
  ],
}

/** A saved LOCAL server holding env secrets — `env_keys` names them, values are never sent. */
const LOCAL_SERVER: Connector = {
  ...FIRECRAWL,
  id: 'mcp:local-tools',
  name: 'Local tools',
  config: {
    transport: 'stdio',
    url: '',
    command: 'npx',
    args: ['-y', '@vendor/tools'],
    env_keys: ['VENDOR_API_KEY', 'PORT'],
    auth_type: 'none',
    auth_header: 'Authorization',
    endpoint: 'npx -y @vendor/tools',
  },
  secrets_public: { has_token: false },
}

/** A saved OAuth-authed server (RFC `2026-08-14-mcp-oauth`) — `auth_type: 'oauth'` plus the
 *  token-free `oauth` summary `McpClientStore._oauth_summary` echoes into every server's
 *  public view. */
const OAUTH_SERVER: Connector = {
  ...FIRECRAWL,
  id: 'mcp:mercury',
  name: 'Mercury',
  config: {
    transport: 'http',
    url: 'https://mcp.mercury.com/mcp',
    command: '',
    args: [],
    auth_type: 'oauth',
    auth_header: 'Authorization',
    env_keys: [],
    verification: 'unknown',
    last_status: 'ok',
    last_error: null,
    endpoint: 'https://mcp.mercury.com/mcp',
    oauth: {
      connected: true,
      issuer_host: 'mcp.mercury.com',
      scope: 'mcp:tools',
      expires_at: null,
      needs_reauth: false,
    },
  },
  secrets_public: { has_token: false },
}

const PROBE_OK = {
  ok: true,
  server_info: { name: 'Firecrawl', version: '2.0' },
  suggested_name: 'Firecrawl v2',
  suggested_id: 'firecrawl-v2',
  tools: [{ name: 'firecrawl_scrape' }, { name: 'firecrawl_search' }, { name: 'firecrawl_map' }],
  trust: {
    publisher: 'Firecrawl',
    verification: 'unknown',
    homepage_url: null,
    repo_url: null,
    endpoint: 'https://mcp.firecrawl.dev',
  },
  error: null,
}

function mockApi(overrides: Record<string, unknown> = {}, connectors: Connector[] = [FIRECRAWL]) {
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url}`
    if (key in overrides) return Promise.resolve(overrides[key])
    const body = url === '/api/connectors' ? { connectors } : {}
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
  })
}

beforeEach(() => {
  clearQueryCache()
  authFetch.mockReset()
  mockApi()
})
afterEach(cleanup)

async function openDetail(name = 'Firecrawl') {
  render(<ConnectorsTab />)
  fireEvent.click(await screen.findByText(name))
  return await screen.findByRole('button', { name: 'More actions' })
}

async function openKebabItem(label: string, name = 'Firecrawl') {
  fireEvent.click(await openDetail(name))
  fireEvent.click(screen.getByText(label))
}

const paths = () => authFetch.mock.calls.map((c: unknown[]) => String(c[0]))

// ── G9: Edit configuration reopens the add flow, pre-filled ─────────────────────

describe('Edit configuration reopens the setup drawer PRE-FILLED', () => {
  it('opens the flow on the saved connection instead of the directory', async () => {
    await openKebabItem('Edit configuration')

    // The dead end this replaced: `Browse & add`, filtered to MCP.
    expect(screen.queryByLabelText('Search directory')).toBeNull()
    expect((screen.getByLabelText(labelStartingWith('Server URL')) as HTMLInputElement).value).toBe(
      'https://mcp.firecrawl.dev',
    )
    expect((screen.getByLabelText('Authentication') as HTMLSelectElement).value).toBe('key')
  })

  it('never echoes the saved token into the form payload — the box opens empty', async () => {
    await openKebabItem('Edit configuration')

    const key = screen.getByLabelText(labelStartingWith('API key')) as HTMLInputElement
    expect(key.value).toBe('')
    expect(key.placeholder).toBe('••••••••')
    // `GET /api/connectors` is a `public()` view: nothing in the DOM can hold the secret,
    // because the list that built this screen never carried one.
    expect(document.body.innerHTML).not.toContain('sk-')
    expect(paths()).not.toContain('/api/connectors/mcp%3Afirecrawl/secret/token')
  })

  it('the EYE reveals the saved token — one control, fetched on demand', async () => {
    mockApi({
      'GET /api/connectors/mcp%3Afirecrawl/secret/token': {
        ok: true,
        json: async () => ({ connector_id: 'mcp:firecrawl', field: 'token', value: 'sk-live-42' }),
      },
    })
    await openKebabItem('Edit configuration')

    // There is no second control: the eye IS the reveal (the old "Reveal saved …" button and
    // its read-only echo row are gone).
    expect(screen.queryByText(/Reveal saved/)).toBeNull()
    fireEvent.click(screen.getByLabelText('Show secret'))

    const key = screen.getByLabelText(labelStartingWith('API key')) as HTMLInputElement
    await waitFor(() => expect(key.value).toBe('sk-live-42'))
    expect(key.type).toBe('text')
    expect(paths()).toContain('/api/connectors/mcp%3Afirecrawl/secret/token')

    // Hiding an UNEDITED reveal returns the box to blank — which is what "keep the saved
    // token" means on save, so merely looking never rewrites the credential.
    fireEvent.click(screen.getByLabelText('Hide secret'))
    await waitFor(() => expect(key.value).toBe(''))
  })

  it('names the saved env keys without their values, for a local server', async () => {
    mockApi({}, [LOCAL_SERVER])
    await openKebabItem('Edit configuration', 'Local tools')

    expect((screen.getByLabelText(labelStartingWith('Command')) as HTMLInputElement).value).toBe(
      'npx',
    )
    expect((screen.getByLabelText('Arguments') as HTMLInputElement).value).toBe('-y @vendor/tools')
    expect((screen.getByLabelText(/^Environment variables/) as HTMLTextAreaElement).value).toBe('')
    expect(screen.getByText(/Already set: VENDOR_API_KEY, PORT/)).toBeTruthy()
    expect(document.body.innerHTML).not.toContain('vendor-secret')
  })

  it('the eye loads the saved env set, and hiding it again wipes nothing', async () => {
    mockApi(
      {
        'GET /api/connectors/mcp%3Alocal-tools/secret/env': {
          ok: true,
          json: async () => ({
            connector_id: 'mcp:local-tools',
            field: 'env',
            value: 'PORT=8080\nVENDOR_API_KEY=vendor-secret',
          }),
        },
      },
      [LOCAL_SERVER],
    )
    await openKebabItem('Edit configuration', 'Local tools')

    const box = screen.getByLabelText(/^Environment variables/) as HTMLTextAreaElement
    fireEvent.click(screen.getByLabelText('Show secret'))
    await waitFor(() => expect(box.value).toBe('PORT=8080\nVENDOR_API_KEY=vendor-secret'))

    // `env` is a whole-map REPLACE with no keep-rule, so this is the dangerous direction:
    // an untouched box must go back to EMPTY, which is the only thing `updateMcpServer`
    // reads as "send no env at all and keep every saved value".
    fireEvent.click(screen.getByLabelText('Hide secret'))
    await waitFor(() => expect(box.value).toBe(''))
  })

  it('typing over a saved env set without revealing it first warns it will be REPLACED', async () => {
    mockApi({}, [LOCAL_SERVER])
    await openKebabItem('Edit configuration', 'Local tools')

    const box = screen.getByLabelText(/^Environment variables/) as HTMLTextAreaElement
    // Untouched: the soft, default caption — and the eye is still offered.
    expect(screen.getByText(/Already set: VENDOR_API_KEY, PORT/)).toBeTruthy()
    expect(screen.getByLabelText('Show secret')).toBeTruthy()

    // Typing without revealing first: useSecretReveal drops the eye immediately (the box no
    // longer holds "the saved set", it holds a brand-new one), so the louder warning must
    // replace the soft caption, and the eye must be gone — there is no partial state where a
    // typed value coexists with "click here to load the rest".
    fireEvent.change(box, { target: { value: 'MY_NEW_VAR=1' } })
    expect(screen.queryByLabelText('Show secret')).toBeNull()
    expect(screen.queryByText(/^Already set:/)).toBeNull()
    const warning = screen.getByText(/replaces the saved set/)
    expect(warning.textContent).toContain('VENDOR_API_KEY, PORT')
    expect(warning.textContent).toContain('Clear the box first')

    // Clearing the box back to empty restores the eye and the soft caption.
    fireEvent.change(box, { target: { value: '' } })
    expect(screen.getByText(/Already set: VENDOR_API_KEY, PORT/)).toBeTruthy()
    expect(screen.getByLabelText('Show secret')).toBeTruthy()
  })

  it('fixes the transport and hides the paste pill — the store refuses to patch either', async () => {
    await openKebabItem('Edit configuration')

    expect(
      (screen.getByRole('button', { name: 'Remote server (URL)' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    expect(
      (screen.getByRole('button', { name: 'Local command (stdio)' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    expect(screen.queryByText('Paste from a config snippet')).toBeNull()
  })

  it('still REQUIRES Test connect — there is no Save on step 1', async () => {
    await openKebabItem('Edit configuration')

    expect(screen.getByText('Connect')).toBeTruthy()
    expect(screen.queryByText('Save changes')).toBeNull()
    // And nothing has been written by merely opening the editor.
    expect(paths().filter((p) => p.startsWith('/api/mcp-clients'))).toEqual([])
  })

  it('refuses to save an edit that cannot reach the server', async () => {
    mockApi({
      'POST /api/mcp-clients/probe': {
        ok: true,
        json: async () => ({ ...PROBE_OK, ok: false, error: 'Connection refused', tools: [] }),
      },
    })
    await openKebabItem('Edit configuration')
    fireEvent.change(screen.getByLabelText(labelStartingWith('Server URL')), {
      target: { value: 'https://gone.test' },
    })
    fireEvent.click(screen.getByText('Connect'))

    await waitFor(() => expect(screen.getByText(/Connection refused/)).toBeTruthy())
    expect(screen.getByText('Retry')).toBeTruthy()
    expect(screen.queryByText('Save changes')).toBeNull()
    expect(paths().some((p) => p === '/api/mcp-clients/mcp:firecrawl')).toBe(false)
  })
})

// ── OAuth (RFC 2026-08-14-mcp-oauth) — editing a server that signs in rather than keys in ────

describe('editing an OAuth server', () => {
  it('round-trips oauth as the picker value — never silently downgraded to API key', async () => {
    mockApi({}, [OAUTH_SERVER])
    await openKebabItem('Edit configuration', 'Mercury')

    expect((screen.getByLabelText('Authentication') as HTMLSelectElement).value).toBe('oauth')
  })

  it('shows the connected-account summary, never a raw token field', async () => {
    mockApi({}, [OAUTH_SERVER])
    await openKebabItem('Edit configuration', 'Mercury')

    // The API-key box is the wrong control for a credential nobody types — it must not exist
    // on this screen at all, not just be empty.
    expect(screen.queryByLabelText('API key')).toBeNull()
    expect(screen.getByText(/Signed in/)).toBeTruthy()
    expect(screen.getByText(/mcp\.mercury\.com/)).toBeTruthy()
    expect(screen.getByText(/mcp:tools/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign in again' })).toBeTruthy()
  })

  it('Test connect is already available — a valid saved sign-in needs no re-auth to re-test', async () => {
    mockApi({}, [OAUTH_SERVER])
    await openKebabItem('Edit configuration', 'Mercury')

    expect((screen.getByText('Connect') as HTMLButtonElement).closest('button')?.disabled).toBe(
      false,
    )
  })

  it('a dead sign-in (needs_reauth) is ready to reconnect, not a false "signed in"', async () => {
    mockApi({}, [
      {
        ...OAUTH_SERVER,
        config: {
          ...OAUTH_SERVER.config,
          last_status: 'auth_error',
          last_error: 'Sign-in expired — reconnect Mercury in Connectors.',
          oauth: {
            ...(OAUTH_SERVER.config.oauth as object),
            needs_reauth: true,
          },
        },
      },
    ])
    await openKebabItem('Edit configuration', 'Mercury')

    expect(screen.queryByRole('button', { name: 'Sign in again' })).toBeNull()
    expect(screen.getByText(/Was connected to mcp\.mercury\.com/)).toBeTruthy()
    // A stale sign-in is not a dead end — the one Connect button re-authenticates and
    // re-tests in a single click, so it stays enabled rather than waiting on a separate
    // sign-in first.
    expect((screen.getByText('Connect').closest('button') as HTMLButtonElement).disabled).toBe(
      false,
    )
  })

  // Regression (final review gate): the row a fresh OAuth sign-in mints server-side is deleted
  // on Cancel/Discard for a NEW add — but `oauthServerIdRef` is set to `editing.serverId` on an
  // edit's "Sign in again" too (`McpAddFlow.tsx::signIn`), so the cleanup MUST tell the two
  // apart. This drives the dangerous case — the ref is actually populated by a real sign-in —
  // rather than trusting an untouched ref to hide the bug.
  describe('Discard after "Sign in again" never deletes the saved server — it predates this session', () => {
    let popup: { location: { href: string }; closed: boolean; close: ReturnType<typeof vi.fn> }
    beforeEach(() => {
      popup = { location: { href: '' }, closed: false, close: vi.fn() }
      popup.close.mockImplementation(() => {
        popup.closed = true
      })
      vi.stubGlobal(
        'open',
        vi.fn(() => popup),
      )
    })
    afterEach(() => vi.unstubAllGlobals())

    it('leaves the row alone on Discard', async () => {
      mockApi(
        {
          'POST /api/mcp-clients/oauth/begin': {
            ok: true,
            json: async () => ({
              ok: true,
              authorize_url: 'https://as.example/authorize',
              flow_id: 'f1',
              expires_in: 600,
            }),
          },
          'GET /api/mcp-clients/oauth/f1': {
            ok: true,
            json: async () => ({ status: 'ok', error: null }),
          },
          // Sign in again now chains straight into a re-test — it needs a real answer to land
          // on review rather than the error step.
          'POST /api/mcp-clients/mercury/test': {
            ok: true,
            json: async () => ({
              id: 'mercury',
              name: 'Mercury',
              url: 'https://mcp.mercury.com/mcp',
              auth_type: 'oauth',
              last_status: 'ok',
              last_error: null,
              discovered_tools: [{ name: 'list_accounts' }],
              publisher: 'Mercury',
              verification: 'unknown',
              homepage_url: null,
              repo_url: null,
              endpoint: 'https://mcp.mercury.com/mcp',
              oauth: {
                connected: true,
                issuer_host: 'mcp.mercury.com',
                scope: 'mcp:tools',
                expires_at: null,
                needs_reauth: false,
              },
            }),
          },
        },
        [OAUTH_SERVER],
      )
      await openKebabItem('Edit configuration', 'Mercury')

      fireEvent.click(screen.getByRole('button', { name: 'Sign in again' }))
      await waitFor(() => expect(popup.location.href).toBe('https://as.example/authorize'))
      await waitFor(() => expect(screen.getByText(/Reachable — 1 tool found/)).toBeTruthy())

      fireEvent.click(screen.getByText('Discard'))

      expect(
        authFetch.mock.calls.some(
          (c: unknown[]) => (c[1] as RequestInit | undefined)?.method === 'DELETE',
        ),
      ).toBe(false)
      // Discard still works — it just skips the delete — so the edit drawer actually closed.
      await waitFor(() => expect(screen.queryByText('Discard')).toBeNull())
    })
  })
})

// ── Reconnect (RFC 2026-08-14-mcp-oauth §4.7 — "Recovery is one button") ─────────────────────

describe('Reconnect', () => {
  let popup: { location: { href: string }; closed: boolean; close: ReturnType<typeof vi.fn> }
  beforeEach(() => {
    popup = { location: { href: '' }, closed: false, close: vi.fn() }
    popup.close.mockImplementation(() => {
      popup.closed = true
    })
    vi.stubGlobal(
      'open',
      vi.fn(() => popup),
    )
  })
  afterEach(() => vi.unstubAllGlobals())

  const ERRORING_FIRECRAWL: Connector = {
    ...FIRECRAWL,
    status: 'error',
    config: {
      ...FIRECRAWL.config,
      last_status: 'connect_error',
      last_error: 'Connection timed out',
    },
  }

  const DEAD_OAUTH_SERVER: Connector = {
    ...OAUTH_SERVER,
    status: 'error',
    config: {
      ...OAUTH_SERVER.config,
      last_status: 'auth_error',
      last_error: 'Sign-in expired — reconnect Mercury in Connectors.',
      oauth: {
        ...(OAUTH_SERVER.config.oauth as object),
        needs_reauth: true,
      },
    },
  }

  it('a plain MCP server still reconnects by re-testing — unchanged', async () => {
    mockApi({ 'POST /api/mcp-clients/firecrawl/test': { ok: true, json: async () => ({}) } }, [
      ERRORING_FIRECRAWL,
    ])
    render(<ConnectorsTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'Reconnect' }))

    await waitFor(() => expect(screen.getByText('Firecrawl reconnected')).toBeTruthy())
    expect(paths()).toContain('/api/mcp-clients/firecrawl/test')
    expect(paths()).not.toContain('/api/mcp-clients/oauth/begin')
  })

  it('an OAuth server reconnects with a fresh popup + poll sign-in, not a bare retest', async () => {
    mockApi(
      {
        'POST /api/mcp-clients/oauth/begin': {
          ok: true,
          json: async () => ({
            ok: true,
            authorize_url: 'https://as.example/authorize',
            flow_id: 'f1',
            expires_in: 600,
          }),
        },
        'GET /api/mcp-clients/oauth/f1': {
          ok: true,
          json: async () => ({ status: 'ok', error: null }),
        },
      },
      [DEAD_OAUTH_SERVER],
    )
    render(<ConnectorsTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'Reconnect' }))

    // Registration is reused; only consent is re-obtained — `begin({server_id})`, not `{url}`.
    await waitFor(() => expect(popup.location.href).toBe('https://as.example/authorize'))
    await waitFor(() => expect(screen.getByText('Mercury reconnected')).toBeTruthy())

    const beginCall = authFetch.mock.calls.find(
      (c: unknown[]) => c[0] === '/api/mcp-clients/oauth/begin',
    )
    expect(beginCall).toBeTruthy()
    expect(JSON.parse(String((beginCall![1] as RequestInit).body))).toEqual({
      server_id: 'mercury',
    })
    expect(paths()).not.toContain('/api/mcp-clients/mercury/test')
    expect(popup.close).toHaveBeenCalled()
  })

  it('a second click while a popup is already open does not fire a duplicate sign-in', async () => {
    // The poll answers `ok` immediately (rather than staying `pending`) so this flow resolves
    // and its poll interval clears itself before the test ends — a `pending` mock left standing
    // would leak a REAL `setInterval` past this test into whichever one runs next in this file.
    let beginCalls = 0
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/mcp-clients/oauth/begin') {
        beginCalls += 1
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            authorize_url: 'https://as.example/authorize',
            flow_id: 'f1',
            expires_in: 600,
          }),
        })
      }
      if (url === '/api/mcp-clients/oauth/f1') {
        return Promise.resolve({ ok: true, json: async () => ({ status: 'ok', error: null }) })
      }
      const body = url === '/api/connectors' ? { connectors: [DEAD_OAUTH_SERVER] } : {}
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
    })
    render(<ConnectorsTab />)
    const button = await screen.findByRole('button', { name: 'Reconnect' })
    fireEvent.click(button)
    fireEvent.click(button)

    await waitFor(() => expect(screen.getByText('Mercury reconnected')).toBeTruthy())
    expect(beginCalls).toBe(1)
  })
})

describe('the edit review step', () => {
  async function reachReview() {
    mockApi({ 'POST /api/mcp-clients/probe': { ok: true, json: async () => PROBE_OK } })
    await openKebabItem('Edit configuration')
    fireEvent.click(screen.getByText('Connect'))
    await screen.findByText(/Reachable — 3 tools found/)
  }

  it('keeps the SAVED name and shows the Server ID as fixed, with no customize link', async () => {
    await reachReview()

    // The server re-reported itself as "Firecrawl v2"; the name is the user's, not the
    // server's to re-assert on every re-test.
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Firecrawl')
    expect(screen.getByText('firecrawl')).toBeTruthy()
    expect(screen.queryByText('customize')).toBeNull()
    expect(screen.getByText(/namespaces this server/)).toBeTruthy()
    // ...and the probe's suggested id is NOT adopted.
    expect(screen.queryByText('firecrawl-v2')).toBeNull()
  })

  it('reflects the levels the server ACTUALLY has, and defaults only what is new', async () => {
    await reachReview()

    expect(
      screen.getByRole('button', { name: 'firecrawl_scrape: Block' }).getAttribute('aria-pressed'),
    ).toBe('true')
    expect(
      screen.getByRole('button', { name: 'firecrawl_search: Ask' }).getAttribute('aria-pressed'),
    ).toBe('true')
    // A newly discovered tool is cautious, with no tiering (spec §4).
    expect(
      screen.getByRole('button', { name: 'firecrawl_map: Ask' }).getAttribute('aria-pressed'),
    ).toBe('true')
  })

  it('PATCHes the connection and writes only the levels that moved', async () => {
    await reachReview()
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Firecrawl EU' } })
    fireEvent.click(screen.getByRole('button', { name: 'firecrawl_scrape: Always allow' }))
    fireEvent.click(screen.getByText('Save changes'))

    await waitFor(() => expect(screen.getByText('Firecrawl EU updated')).toBeTruthy())

    const patch = authFetch.mock.calls.find(
      (c: unknown[]) => (c[1] as RequestInit)?.method === 'PATCH',
    )!
    expect(patch[0]).toBe('/api/mcp-clients/firecrawl')
    const body = JSON.parse(String((patch[1] as RequestInit).body))
    expect(body.name).toBe('Firecrawl EU')
    expect(body).not.toHaveProperty('token') // untouched → the saved one survives
    expect(body).not.toHaveProperty('id')

    // Only `firecrawl_scrape` moved; the two tools left alone are not rewritten.
    const permWrites = paths().filter((p) => p.includes('/permissions/'))
    expect(permWrites).toEqual(['/api/connectors/mcp%3Afirecrawl/permissions/firecrawl_scrape'])
  })

  it('reports a failed save instead of claiming the edit landed', async () => {
    mockApi({
      'POST /api/mcp-clients/probe': { ok: true, json: async () => PROBE_OK },
      'PATCH /api/mcp-clients/firecrawl': {
        ok: false,
        status: 409,
        json: async () => ({ detail: 'server is locked' }),
      },
    })
    await openKebabItem('Edit configuration')
    fireEvent.click(screen.getByText('Connect'))
    await screen.findByText(/Reachable — 3 tools found/)
    fireEvent.click(screen.getByText('Save changes'))

    await waitFor(() => expect(screen.getByText('server is locked')).toBeTruthy())
    expect(screen.queryByText(/updated/)).toBeNull()
  })
})

// ── G10: Remove connector actually removes ───────────────────────────────────

describe('Remove connector', () => {
  it('asks first — removal is destructive and irreversible', async () => {
    await openKebabItem('Remove connector')

    const remove = screen.getByRole('button', { name: 'Remove' })
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy()
    // One paragraph, several elements deep (the name is emphasised), so read the whole thing.
    const body = remove.closest('.rd-modal')!.textContent ?? ''
    expect(body).toMatch(/can't be undone/)
    expect(body).toMatch(/every permission level you set/)
    // It NAMES what is lost. The vague fallback ("access to it") is for a connector with no
    // discovered tools — which a just-added MCP server used to be, because create never ran
    // a handshake (QA D1). It now does, so this reads the count on the very first render.
    expect(body).toMatch(/the 2 tools it offers/)
    // Asking is not doing.
    expect(paths().some((p) => p.startsWith('/api/mcp-clients/'))).toBe(false)
  })

  it('cancels back to the detail page, having changed nothing', async () => {
    await openKebabItem('Remove connector')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(await screen.findByRole('button', { name: 'More actions' })).toBeTruthy()
    expect(paths().some((p) => p.startsWith('/api/mcp-clients/'))).toBe(false)
  })

  it('DELETEs the server, closes the page and refreshes the list', async () => {
    mockApi({
      'DELETE /api/mcp-clients/firecrawl': {
        ok: true,
        json: async () => ({ deleted: 'firecrawl' }),
      },
    })
    await openKebabItem('Remove connector')
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))

    await waitFor(() => expect(screen.getByText('Firecrawl removed')).toBeTruthy())
    const del = authFetch.mock.calls.find(
      (c: unknown[]) => (c[1] as RequestInit)?.method === 'DELETE',
    )!
    expect(del[0]).toBe('/api/mcp-clients/firecrawl')
    // the detail page is gone, and the list was refetched
    expect(screen.queryByRole('button', { name: 'More actions' })).toBeNull()
    expect(paths().filter((p) => p === '/api/connectors').length).toBeGreaterThan(1)
  })

  it('does NOT say "removed" when the request failed', async () => {
    mockApi({
      'DELETE /api/mcp-clients/firecrawl': {
        ok: false,
        status: 500,
        json: async () => ({ detail: 'store is read-only' }),
      },
    })
    await openKebabItem('Remove connector')
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))

    await waitFor(() => expect(screen.getByText('store is read-only')).toBeTruthy())
    expect(screen.queryByText('Firecrawl removed')).toBeNull()
    // and the connector is still there to try again on
    expect(screen.getByRole('button', { name: 'More actions' })).toBeTruthy()
  })

  it('offers a real remove for a native connector too — not the old dead-end toast', async () => {
    mockApi(
      {
        'DELETE /api/connectors/telegram': {
          ok: true,
          json: async () => ({ deleted: 'telegram' }),
        },
      },
      [
        {
          ...FIRECRAWL,
          id: 'telegram',
          name: 'Telegram',
          kind: 'telegram',
          config: {},
          tools: [],
        },
      ],
    )
    await openKebabItem('Remove connector', 'Telegram')

    // Native copy — never the MCP-specific "Lucius loses N tools" wording, and it still
    // cross-references Pause as the non-destructive alternative.
    expect(
      screen.getByText(/This clears your saved credentials and resets its settings/),
    ).toBeTruthy()
    expect(screen.getByText(/use Pause instead/)).toBeTruthy()
    expect(paths().some((p) => p.startsWith('/api/mcp-clients/'))).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))

    await waitFor(() => expect(screen.getByText('Telegram removed')).toBeTruthy())
    const del = authFetch.mock.calls.find(
      (c: unknown[]) => (c[1] as RequestInit)?.method === 'DELETE',
    )!
    expect(del[0]).toBe('/api/connectors/telegram')
    expect(screen.queryByRole('button', { name: 'More actions' })).toBeNull()
  })
})

// ── H2: the ladder must not show a level the backend refused ─────────────────

describe('the permission ladder is not allowed to lie', () => {
  it('rolls back the optimistic level when the write is rejected', async () => {
    mockApi({
      'PUT /api/connectors/mcp%3Afirecrawl/permissions/firecrawl_search': {
        ok: false,
        status: 500,
        json: async () => ({ detail: 'permission store unavailable' }),
      },
    })
    const kebab = await openDetail()
    const detail = kebab.closest('.rd-modal') as HTMLElement

    fireEvent.click(within(detail).getByRole('button', { name: 'firecrawl_search: Block' }))

    // The fail-open scar: the grid kept rendering "Blocked" while the backend held no row,
    // so the operator read a tool as off while Lucius was still being offered it.
    await waitFor(() =>
      expect(
        within(detail)
          .getByRole('button', { name: 'firecrawl_search: Block' })
          .getAttribute('aria-pressed'),
      ).toBe('false'),
    )
    expect(
      within(detail)
        .getByRole('button', { name: 'firecrawl_search: Ask' })
        .getAttribute('aria-pressed'),
    ).toBe('true')
    expect(screen.getByText('permission store unavailable')).toBeTruthy()
  })

  it('keeps the level, on the real route, when the write lands', async () => {
    const kebab = await openDetail()
    const detail = kebab.closest('.rd-modal') as HTMLElement
    fireEvent.click(within(detail).getByRole('button', { name: 'firecrawl_search: Always allow' }))

    await waitFor(() =>
      expect(paths().includes('/api/connectors/mcp%3Afirecrawl/permissions/firecrawl_search')).toBe(
        true,
      ),
    )
    expect(
      within(detail)
        .getByRole('button', { name: 'firecrawl_search: Always allow' })
        .getAttribute('aria-pressed'),
    ).toBe('true')
  })
})

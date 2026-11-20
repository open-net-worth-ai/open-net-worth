// @vitest-environment jsdom
//
// Security review 2026-08-17, finding 9: every per-provider config form
// (`*Section.tsx`) used to hardcode `enabled: true` in its PUT/PATCH body — so
// saving a credential edit while a connector was PAUSED silently RESUMED it, an
// unannounced side effect nowhere near the intentional Pause/Resume control. The
// fix makes every form pass through whatever `enabled` the server last reported
// (`enabled: cfg.enabled`, never a literal `true`) and guards `save()` on `cfg`
// being loaded, plus shows an explicit "Currently paused" note so an edit-while-
// paused isn't silently inert either.
//
// All ten Section components share the exact same `save()` shape (confirmed by
// inspection — `if (!form || !cfg) return false` then `enabled: cfg.enabled` in
// the PUT body). This file exercises three representative shapes rather than all
// ten: SimpleFinSection (the minimal single-field case), TelegramSection (a
// multi-field body plus a nested pending-approvals fetch), and YodleeSection (two
// independent secrets). A regression in the shared pattern would show up in any
// of them.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, act, waitFor } from '@testing-library/react'
import type { ComponentType } from 'react'
import { SimpleFinSection } from './SimpleFinSection'
import { TelegramSection } from './TelegramSection'
import { YodleeSection } from './YodleeSection'
import { useConnectorSaveHost, type ConnectorSaveHost } from './formSave'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

beforeEach(() => {
  authFetch.mockReset()
})
afterEach(cleanup)

/** Hosts a Section exactly like ConnectorDetail's modal does — the footer's Save
 *  calls `host.run()`, which invokes whatever the form last published. Stashing
 *  the host on a plain ref object lets the test drive it directly. */
function Harness({
  Section,
  hostRef,
}: {
  Section: ComponentType
  hostRef: { current: ConnectorSaveHost | null }
}) {
  const host = useConnectorSaveHost()
  hostRef.current = host
  return (
    <host.Provider>
      <Section />
    </host.Provider>
  )
}

function putBodiesTo(url: string): Record<string, unknown>[] {
  return authFetch.mock.calls
    .filter((c) => c[0] === url && (c[1] as RequestInit | undefined)?.method === 'PUT')
    .map((c) => JSON.parse((c[1] as RequestInit).body as string) as Record<string, unknown>)
}

describe('SimpleFinSection — save() never hardcodes enabled', () => {
  const STATUS = {
    configured: true,
    enabled: false,
    has_access_url: true,
    last_synced_at: null,
    last_status: 'never_run',
    last_error: null,
  }

  function mockFetch() {
    authFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/simplefin/status' && !init?.method) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(STATUS) })
      }
      if (url === '/api/simplefin/config' && init?.method === 'PUT') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      if (url === '/api/accounts') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) })
      }
      // ConnectorAccountsList's own self-fetch — the real backend always includes
      // `accounts`, so the component doesn't guard against it being absent here either.
      if (url === '/api/connectors/simplefin/accounts') {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              status: 'not_configured',
              provider_label: 'SimpleFIN',
              accounts: [],
            }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
  }

  it('shows the paused note when configured but not enabled', async () => {
    mockFetch()
    render(<SimpleFinSection />)
    expect(await screen.findByText(/Currently paused/)).toBeTruthy()
  })

  it('PUTs enabled: false — never true — while paused', async () => {
    mockFetch()
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness Section={SimpleFinSection} hostRef={hostRef} />)
    await screen.findByLabelText(/^Setup token/)

    // The token field renders before the config loads; save only once it has loaded.
    await screen.findByText(/Currently paused/)
    await act(async () => {
      await hostRef.current!.run()
    })

    await waitFor(() => expect(putBodiesTo('/api/simplefin/config')).toHaveLength(1))
    const bodies = putBodiesTo('/api/simplefin/config')
    expect(bodies[0]?.enabled).toBe(false)
  })
})

describe('TelegramSection — save() never hardcodes enabled', () => {
  const CONFIG = {
    configured: true,
    enabled: false,
    allowed_chat_ids: ['123'],
    access_policy: 'allowlist' as const,
    has_bot_token: true,
  }

  function mockFetch() {
    authFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/telegram/config' && !init?.method) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(CONFIG) })
      }
      if (url === '/api/telegram/config' && init?.method === 'PUT') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      if (url.startsWith('/api/chat-pairing')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ requests: [] }) })
      }
      if (url === '/api/accounts') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
  }

  it('shows the paused note when configured but not enabled', async () => {
    mockFetch()
    render(<TelegramSection />)
    expect(await screen.findByText(/Currently paused/)).toBeTruthy()
  })

  it('PUTs enabled: false — never true — while paused', async () => {
    mockFetch()
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness Section={TelegramSection} hostRef={hostRef} />)
    await screen.findByLabelText(/^Bot token/)

    // The token field renders before the config loads; save only once it has loaded.
    await screen.findByText(/Currently paused/)
    await act(async () => {
      await hostRef.current!.run()
    })

    await waitFor(() => expect(putBodiesTo('/api/telegram/config')).toHaveLength(1))
    const bodies = putBodiesTo('/api/telegram/config')
    expect(bodies[0]?.enabled).toBe(false)
  })
})

describe('YodleeSection — save() never hardcodes enabled', () => {
  const STATUS = {
    configured: true,
    enabled: false,
    api_endpoint: 'sandbox',
    has_secret: true,
    client_id: 'cid',
    has_user_credentials: true,
    username: 'user1',
    last_synced_at: null,
    last_status: 'never_run',
    last_error: null,
    stats: { created: 0, skipped: 0, accounts_linked: 0 },
    linked_accounts: 0,
  }

  function mockFetch() {
    authFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/yodlee/status' && !init?.method) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(STATUS) })
      }
      if (url === '/api/yodlee/config' && init?.method === 'PUT') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      if (url === '/api/accounts') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
  }

  it('shows the paused note when configured but not enabled', async () => {
    mockFetch()
    render(<YodleeSection />)
    expect(await screen.findByText(/Currently paused/)).toBeTruthy()
  })

  it('PUTs enabled: false — never true — while paused', async () => {
    mockFetch()
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness Section={YodleeSection} hostRef={hostRef} />)
    await screen.findByLabelText(/^Client ID/)

    // The token field renders before the config loads; save only once it has loaded.
    await screen.findByText(/Currently paused/)
    await act(async () => {
      await hostRef.current!.run()
    })

    await waitFor(() => expect(putBodiesTo('/api/yodlee/config')).toHaveLength(1))
    const bodies = putBodiesTo('/api/yodlee/config')
    expect(bodies[0]?.enabled).toBe(false)
  })
})

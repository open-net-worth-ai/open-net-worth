// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../../api/client'
import { clearQueryCache } from '../../../../state/queryCache'
import { McpOAuthPanel } from './McpOAuthPanel'
import type { OAuthStatus } from './McpOAuthPanel'

const mockAuthFetch = vi.mocked(authFetch)
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const ON: OAuthStatus = {
  available: true,
  reasons: [],
  public_url: 'https://wealth.example.com',
  public_url_source: 'stored',
  mcp_url: 'https://wealth.example.com/mcp',
  apps: [
    {
      grant_id: 'g1',
      name: 'Claude',
      host: 'claude.ai',
      scopes: ['read'],
      connected_at: '2026-11-16T10:00:00.000000Z',
      last_used_at: null,
    },
  ],
  clients: [],
}

function serve(
  status: OAuthStatus | null,
  extra?: (url: string, init?: RequestInit) => Response | undefined,
) {
  mockAuthFetch.mockImplementation(async (url, init) => {
    const hit = extra?.(String(url), init as RequestInit)
    if (hit) return hit
    return status === null ? json({ detail: 'Not Found' }, 404) : json(status)
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  clearQueryCache()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(cleanup)

describe('Settings → MCP → Hosted apps', () => {
  it('renders nothing while the flag is off', async () => {
    serve(null)
    const { container } = render(<McpOAuthPanel toast={vi.fn()} />)
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled())
    expect(container.textContent).toBe('')
  })

  it('shows the address to paste and each connected app with its access', async () => {
    serve(ON)
    render(<McpOAuthPanel toast={vi.fn()} />)
    expect(await screen.findByText('https://wealth.example.com/mcp')).toBeTruthy()
    expect(screen.getByText('Read-only')).toBeTruthy()
    expect(screen.getByText(/claude\.ai · connected/)).toBeTruthy()
  })

  it('explains every reason the server is off', async () => {
    serve({ ...ON, available: false, reasons: ['no_password', 'loopback_trusted'], apps: [] })
    render(<McpOAuthPanel toast={vi.fn()} />)
    expect(await screen.findByText(/Set a password in Settings → Security/)).toBeTruthy()
    expect(screen.getByText(/Turn off "Trust this computer"/)).toBeTruthy()
  })

  it('revokes an app', async () => {
    const calls: string[] = []
    serve(ON, (url, init) => {
      if (init?.method === 'DELETE') {
        calls.push(url)
        return json({ ...ON, apps: [] })
      }
      return undefined
    })
    const toast = vi.fn()
    render(<McpOAuthPanel toast={toast} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }))
    await waitFor(() => expect(calls).toEqual(['/api/mcp/oauth/apps/g1']))
    expect(toast).toHaveBeenCalledWith('Claude disconnected')
  })

  it('shows a new client secret once, out of the screen snapshot', async () => {
    serve(ON, (_url, init) =>
      init?.method === 'POST'
        ? json({ created: { client_id: 'onw_cl_x', client_secret: 'onw_cs_secret' }, ...ON })
        : undefined,
    )
    const { container } = render(<McpOAuthPanel toast={vi.fn()} />)
    fireEvent.change(await screen.findByLabelText('App name'), { target: { value: 'My app' } })
    fireEvent.change(screen.getByLabelText('Redirect URL'), {
      target: { value: 'https://app.example/cb' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create credentials' }))
    await waitFor(() => expect(container.textContent).toContain('onw_cs_secret'))
    const secretBox = container.querySelector('[data-ctx-secret]:not(span)')
    expect(secretBox?.textContent).toContain('onw_cs_secret')
    const body = JSON.parse(String((mockAuthFetch.mock.calls.at(-2)![1] as RequestInit).body))
    expect(body).toEqual({ name: 'My app', redirect_uris: ['https://app.example/cb'] })
  })

  it('locks the address field when the server sets it', async () => {
    serve({ ...ON, public_url_source: 'env' })
    render(<McpOAuthPanel toast={vi.fn()} />)
    const input = (await screen.findByLabelText('Public HTTPS address')) as HTMLInputElement
    expect(input.disabled).toBe(true)
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
  })
})

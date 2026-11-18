// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../../api/client'
import { clearQueryCache } from '../../../../state/queryCache'
import { McpPage } from './McpPage'
import { suggestedPublicUrl } from './McpOAuthPanel'
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
    if (String(url) === '/api/auth/api-token') return json({ token: 'TOK' })
    if (String(url) === '/api/mcp/connect-info')
      return json({
        desktop: false,
        command: 'onw',
        args: ['mcp'],
        command_found: true,
        root: null,
        source_root: null,
        container: false,
      })
    return status === null ? json({ detail: 'Not Found' }, 404) : json(status)
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  clearQueryCache()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(cleanup)

describe('Settings → MCP', () => {
  it('offers OAuth even while the flag is off, with the one step that turns it on', async () => {
    serve(null)
    const openSection = vi.fn()
    render(<McpPage toast={vi.fn()} openSection={openSection} />)
    expect(await screen.findByText('Not set up')).toBeTruthy()
    expect(screen.getByRole('radio', { name: /API token/ }).getAttribute('aria-checked')).toBe(
      'true',
    )
    fireEvent.click(screen.getByRole('radio', { name: /OAuth/ }))
    expect(screen.getByText('OAuth is switched on')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Open Developer' }))
    expect(openSection).toHaveBeenCalledWith('developer')
  })

  it('opens on OAuth when it is ready, and the token path no longer lists ChatGPT', async () => {
    serve(ON)
    render(<McpPage toast={vi.fn()} />)
    expect(await screen.findByDisplayValue('https://wealth.example.com/mcp')).toBeTruthy()
    expect(screen.getByRole('radio', { name: /OAuth/ }).getAttribute('aria-checked')).toBe('true')
    fireEvent.click(screen.getByRole('radio', { name: /API token/ }))
    expect(await screen.findByRole('tab', { name: 'Claude Desktop' })).toBeTruthy()
    expect(screen.queryByRole('tab', { name: 'ChatGPT' })).toBeNull()
  })

  it('shows the address to paste and each connected app with its access', async () => {
    serve(ON)
    render(<McpPage toast={vi.fn()} />)
    expect(await screen.findByDisplayValue('https://wealth.example.com/mcp')).toBeTruthy()
    expect(screen.getByText('Read')).toBeTruthy()
    expect(screen.getByText(/claude\.ai · connected/)).toBeTruthy()
  })

  it('lists what is missing as steps, each with a way to fix it', async () => {
    serve({ ...ON, available: false, reasons: ['no_password', 'loopback_trusted'], apps: [] })
    const openSection = vi.fn()
    render(<McpPage toast={vi.fn()} openSection={openSection} />)
    expect(await screen.findByText('2 steps left')).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: /OAuth/ }))
    expect(screen.getByText('A password is set, and sign-in is on')).toBeTruthy()
    expect(screen.getByText(/Don't ask for the password on this computer/)).toBeTruthy()
    expect(screen.queryByDisplayValue('https://wealth.example.com/mcp')).toBeNull()
    fireEvent.click(screen.getAllByRole('button', { name: 'Open Security' })[0]!)
    expect(openSection).toHaveBeenCalledWith('security')
  })

  it('says plainly where OAuth is not offered at all', async () => {
    serve({ ...ON, available: false, reasons: ['desktop'], apps: [] })
    render(<McpPage toast={vi.fn()} />)
    expect(await screen.findByText('Not available here')).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: /OAuth/ }))
    expect(screen.getByText("OAuth isn't available here")).toBeTruthy()
  })

  it('suggests the page address only when it is https and not this machine', () => {
    const at = (protocol: string, hostname: string) => ({
      protocol,
      hostname,
      origin: `${protocol}//${hostname}`,
    })
    expect(suggestedPublicUrl(at('https:', 'wealth.example.com'))).toBe(
      'https://wealth.example.com',
    )
    expect(suggestedPublicUrl(at('http:', 'wealth.example.com'))).toBe('')
    expect(suggestedPublicUrl(at('https:', 'localhost'))).toBe('')
    expect(suggestedPublicUrl(at('https:', '127.0.0.1'))).toBe('')
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
    render(<McpPage toast={toast} />)
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
    const { container } = render(<McpPage toast={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Create client ID and secret' }))
    fireEvent.change(await screen.findByLabelText('App name'), { target: { value: 'My app' } })
    fireEvent.change(screen.getByLabelText('Callback URL'), {
      target: { value: 'https://app.example/cb' },
    })
    fireEvent.click(screen.getAllByRole('button', { name: 'Create client ID and secret' }).at(-1)!)
    await waitFor(() => expect(container.textContent).toContain('onw_cs_secret'))
    const secretBox = container.querySelector('div[data-ctx-secret]')
    expect(secretBox?.textContent).toContain('onw_cs_secret')
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    fireEvent.click(screen.getByRole('button', { name: 'Copy secret' }))
    expect(writeText).toHaveBeenCalledWith('onw_cs_secret')
    const post = mockAuthFetch.mock.calls.find(
      ([, init]) => (init as RequestInit)?.method === 'POST',
    )!
    const body = JSON.parse(String((post[1] as RequestInit).body))
    expect(body).toEqual({ name: 'My app', redirect_uris: ['https://app.example/cb'] })
  })

  it('locks the address field when the server sets it', async () => {
    serve({ ...ON, public_url_source: 'env' })
    render(<McpPage toast={vi.fn()} />)
    const input = (await screen.findByLabelText('Public HTTPS address')) as HTMLInputElement
    expect(input.disabled).toBe(true)
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
  })
})

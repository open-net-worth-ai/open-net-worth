// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../../api/client'
import { clearQueryCache } from '../../../../state/queryCache'
import { McpSection, TOKEN_MASK } from './McpSection'

const mockAuthFetch = vi.mocked(authFetch)
const json = (body: unknown) =>
  new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })

function serve(launch: Record<string, unknown>) {
  mockAuthFetch.mockImplementation(async (url) =>
    String(url) === '/api/auth/api-token' ? json({ token: 'real-secret-token' }) : json(launch),
  )
}

const INSTALLED = {
  desktop: false,
  command: '/opt/onw/bin/onw',
  args: ['mcp'],
  command_found: true,
  root: null,
  source_root: null,
  container: false,
}

beforeEach(() => {
  vi.clearAllMocks()
  clearQueryCache()
})
afterEach(cleanup)

describe('Settings → MCP', () => {
  it('shows a masked onw config and copies the real token', async () => {
    serve(INSTALLED)
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    const { container } = render(<McpSection toast={vi.fn()} />)

    await waitFor(() =>
      expect(container.querySelector('.rd-code')?.textContent).toContain('/opt/onw/bin/onw'),
    )
    const code = container.querySelector('.rd-code')!.textContent!
    expect(code).toContain(TOKEN_MASK)
    expect(code).not.toContain('real-secret-token')
    expect(screen.getByText('Use your book from other AI apps')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Copy config' }))
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    expect(writeText.mock.calls[0]![0]).toContain('real-secret-token')
  })

  it('offers the "Running from source" variant for local clients', async () => {
    serve(INSTALLED)
    const { container } = render(<McpSection toast={vi.fn()} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Running from source' }))
    expect(container.querySelector('.rd-code')!.textContent).toContain('backend.mcp.stdio_bridge')
  })

  it('Docker: picks "Docker or another computer" itself — onw on the AI app\'s computer', async () => {
    serve({ ...INSTALLED, command: 'onw', command_found: false, container: true })
    const { container } = render(<McpSection toast={vi.fn()} />)
    const tab = await screen.findByRole('tab', { name: 'Docker or another computer' })
    await waitFor(() => expect(tab.getAttribute('aria-selected')).toBe('true'))
    const code = container.querySelector('.rd-code')!.textContent!
    expect(code).toContain('"command": "onw"')
    expect(code).toContain('ONW_BASE_URL')
    expect(screen.getByText(/uv tool install open-net-worth/)).toBeTruthy()
  })

  it('a path this server found never reaches the "another computer" config', async () => {
    serve(INSTALLED)
    const { container } = render(<McpSection toast={vi.fn()} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Docker or another computer' }))
    expect(container.querySelector('.rd-code')!.textContent).not.toContain('/opt/onw/bin/onw')
  })

  it('desktop app: no install choice, and the config carries ONW_ROOT', async () => {
    serve({
      ...INSTALLED,
      desktop: true,
      command: '/App/python3',
      args: ['-m', 'backend.cli', 'mcp'],
      root: '/Books/mine',
    })
    const { container } = render(<McpSection toast={vi.fn()} />)
    await waitFor(() =>
      expect(container.querySelector('.rd-code')?.textContent).toContain('/Books/mine'),
    )
    expect(screen.queryByRole('tab', { name: 'Running from source' })).toBeNull()
    expect(container.querySelector('.rd-code')!.textContent).not.toContain('ONW_BASE_URL')
  })

  it('"Manage API token" opens Security', async () => {
    serve(INSTALLED)
    const openSection = vi.fn()
    render(<McpSection toast={vi.fn()} openSection={openSection} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Manage API token' }))
    expect(openSection).toHaveBeenCalledWith('security')
  })
})

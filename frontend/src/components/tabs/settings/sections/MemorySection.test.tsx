// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { clearQueryCache } from '../../../../state/queryCache'

const authFetch = vi.fn()
vi.mock('../../../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))

import { MemorySection } from './MemorySection'

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }))

const SETTINGS = { enabled: true, search_chats: true, sensitive_topics: false }

function serve(putStatus = 200) {
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/memory/settings' && init?.method === 'PUT') return json({}, putStatus)
    if (url === '/api/memory/settings') return json(SETTINGS)
    return json({ facts: [], total: 0 })
  })
}

const puts = () =>
  authFetch.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')

describe('MemorySection', () => {
  beforeEach(() => {
    clearQueryCache()
    authFetch.mockReset()
    serve()
  })
  afterEach(cleanup)

  it('shows the five settings rows in order', async () => {
    render(<MemorySection toast={vi.fn()} />)
    const first = await screen.findByText('Search and reference chats', { selector: 'div' })
    const titles = [
      'Search and reference chats',
      'Generate memory from chats',
      'Include sensitive topics in memory',
      'Import memory from other AI assistants',
      'Export memory',
    ]
    const nodes = titles.map((t) => screen.getByText(t, { selector: 'div' }))
    expect(nodes[0]).toBe(first)
    nodes.slice(1).forEach((n, i) => {
      expect(nodes[i]!.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })
    expect(screen.queryByText(/Review/)).toBeNull()
  })

  it('reflects the stored settings in the switches', async () => {
    render(<MemorySection toast={vi.fn()} />)
    const search = await screen.findByRole('switch', { name: 'Search and reference chats' })
    await waitFor(() => expect(search.getAttribute('aria-checked')).toBe('true'))
    expect(
      screen
        .getByRole('switch', { name: 'Include sensitive topics in memory' })
        .getAttribute('aria-checked'),
    ).toBe('false')
  })

  it.each([
    ['Search and reference chats', { search_chats: false }, 'Lucius won’t search your past chats'],
    ['Generate memory from chats', { enabled: false }, /Memory off/],
    ['Include sensitive topics in memory', { sensitive_topics: true }, /can be remembered/],
  ])('the %s switch saves only its own key', async (label, body, message) => {
    const toast = vi.fn()
    render(<MemorySection toast={toast} />)
    const sw = await screen.findByRole('switch', { name: label })
    await waitFor(() => expect((sw as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(sw)
    await waitFor(() => expect(puts()).toHaveLength(1))
    const [url, init] = puts()[0]!
    expect(url).toBe('/api/memory/settings')
    expect(JSON.parse((init as RequestInit).body as string)).toEqual(body)
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringMatching(message)))
  })

  it('says so when a switch could not be saved', async () => {
    serve(500)
    const toast = vi.fn()
    render(<MemorySection toast={toast} />)
    const sw = await screen.findByRole('switch', { name: 'Include sensitive topics in memory' })
    await waitFor(() => expect((sw as HTMLButtonElement).disabled).toBe(false))
    expect(sw.getAttribute('aria-checked')).toBe('false')
    fireEvent.click(sw)
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Could not save (HTTP 500)'))
    expect(sw.getAttribute('aria-checked')).toBe('false')
  })

  it('leaves import and export as coming soon', async () => {
    const toast = vi.fn()
    render(<MemorySection toast={toast} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Start import' }))
    fireEvent.click(screen.getByRole('button', { name: 'Export' }))
    expect(toast).toHaveBeenCalledWith('Import is coming soon')
    expect(toast).toHaveBeenCalledWith('Export is coming soon')
  })
})

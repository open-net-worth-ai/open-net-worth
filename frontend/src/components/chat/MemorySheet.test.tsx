// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { clearQueryCache } from '../../state/queryCache'

const authFetch = vi.fn()
vi.mock('../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))

import { MemorySheet } from './MemorySheet'

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }))

function serve(enabled: boolean, facts: { id: string; content: string; source?: string }[]) {
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/memory/settings') return json({ enabled })
    if (init?.method === 'DELETE') return json({ ok: true })
    if (url === '/api/memory/instruct') return json({ added: 1, updated: 1, removed: 0 })
    return json({ facts, total: facts.length })
  })
}

describe('MemorySheet', () => {
  beforeEach(() => {
    clearQueryCache()
    authFetch.mockReset()
  })
  afterEach(cleanup)

  it('lists stored facts with where each came from', async () => {
    serve(true, [
      { id: 'f1', content: 'Gets paid on the 25th', source: 'explicit' },
      { id: 'f2', content: 'Saving for a house deposit', source: 'extracted' },
    ])
    render(<MemorySheet onClose={vi.fn()} />)
    expect(await screen.findByText('Gets paid on the 25th')).toBeTruthy()
    expect(screen.getByText('You told Lucius')).toBeTruthy()
    expect(screen.getByText('Learned in chat')).toBeTruthy()
    expect(screen.getByText('Stored memories (2)')).toBeTruthy()
    expect(screen.queryByText(/Memory is off/)).toBeNull()
  })

  it('forgets one fact through the same DELETE Settings uses', async () => {
    serve(true, [{ id: 'f 1', content: 'Rent goes up every March' }])
    render(<MemorySheet onClose={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Forget: Rent goes up every March' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith('/api/memory/facts/f%201', { method: 'DELETE' }),
    )
  })

  it('arms "Forget everything" before deleting anything', async () => {
    serve(true, [{ id: 'f1', content: 'A fact' }])
    render(<MemorySheet onClose={vi.fn()} />)
    await screen.findByText('A fact')
    fireEvent.click(screen.getByRole('button', { name: 'Forget everything' }))
    expect(authFetch).not.toHaveBeenCalledWith('/api/memory/facts', { method: 'DELETE' })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm — forget all 1' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith('/api/memory/facts', { method: 'DELETE' }),
    )
  })

  it('says so when memory is switched off', async () => {
    serve(false, [])
    render(<MemorySheet onClose={vi.fn()} />)
    expect(await screen.findByText(/Memory is off/)).toBeTruthy()
  })

  it('sends a typed change to the instruct route and says what changed', async () => {
    serve(true, [{ id: 'f1', content: 'Rent goes up every March' }])
    render(<MemorySheet onClose={vi.fn()} />)
    await screen.findByText('Rent goes up every March')
    const box = screen.getByLabelText('Tell Lucius what to remember or change')
    fireEvent.change(box, { target: { value: 'my dog is Beans; rent rises in April now' } })
    fireEvent.click(screen.getByRole('button', { name: 'Update' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith(
        '/api/memory/instruct',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ instruction: 'my dog is Beans; rent rises in April now' }),
        }),
      ),
    )
    expect(await screen.findByText('Added 1 · Updated 1')).toBeTruthy()
    expect((box as HTMLInputElement).value).toBe('')
  })

  it('keeps the change box closed while memory is off', async () => {
    serve(false, [])
    render(<MemorySheet onClose={vi.fn()} />)
    await screen.findByText(/Memory is off/)
    expect(
      (screen.getByLabelText('Tell Lucius what to remember or change') as HTMLInputElement)
        .disabled,
    ).toBe(true)
  })
})

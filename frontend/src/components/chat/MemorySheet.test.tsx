// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { clearQueryCache } from '../../state/queryCache'
import type { MemoryFact } from '../../hooks/useMemoryFacts'

const authFetch = vi.fn()
vi.mock('../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))

import { MemorySheet } from './MemorySheet'

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }))

const settings = (enabled: boolean) => ({ enabled, search_chats: true, sensitive_topics: false })

const RENT: MemoryFact = {
  id: 'f 1',
  content: 'Rent goes up every March',
  state: 'live',
  section: 'money',
  topic: 'Housing',
  updated_at: '2026-10-01T00:00:00Z',
}
const DEPOSIT: MemoryFact = {
  id: 'f2',
  content: 'Saving for a house deposit',
  state: 'live',
  section: 'plans',
  topic: 'Home',
  updated_at: '2026-10-02T00:00:00Z',
}

function serve(enabled: boolean, facts: MemoryFact[]) {
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/memory/settings') return json(settings(enabled))
    if (init?.method === 'DELETE') return json({ ok: true })
    if (init?.method === 'PATCH') return json({ id: 'f1', content: 'edited' })
    if (url === '/api/memory/instruct')
      return json({
        added: 1,
        updated: 1,
        removed: 0,
        rejected: [],
        changes: [
          { change: 'added', where: 'You · Profile', text: 'Has a dog named Beans' },
          {
            change: 'updated',
            where: 'Money · Housing',
            text: 'Rent goes up every April',
            was: 'Rent goes up every March',
          },
        ],
      })
    return json({ facts, total: facts.length })
  })
}

async function openRent() {
  fireEvent.click((await screen.findByText('Housing')).closest('button') as HTMLElement)
}

Element.prototype.scrollIntoView = vi.fn()

describe('MemorySheet', () => {
  beforeEach(() => {
    clearQueryCache()
    authFetch.mockReset()
  })
  afterEach(cleanup)

  it('shows the same sections-and-topics browser as Settings', async () => {
    serve(true, [RENT, DEPOSIT])
    render(<MemorySheet onClose={vi.fn()} />)
    const money = await screen.findByRole('region', { name: 'Money' })
    expect(within(money).getByText('Housing')).toBeTruthy()
    expect(within(money).getByText('Rent goes up every March')).toBeTruthy()
    expect(screen.getByRole('region', { name: 'Plans' })).toBeTruthy()
    expect(screen.queryByText(/Memory is off/)).toBeNull()
  })

  it('filters the browser from its search box by fact text or topic', async () => {
    serve(true, [RENT, DEPOSIT])
    render(<MemorySheet onClose={vi.fn()} />)
    await screen.findByText('Rent goes up every March')
    const search = screen.getByLabelText('Search memories')
    fireEvent.change(search, { target: { value: 'RENT' } })
    expect(screen.queryByRole('region', { name: 'Plans' })).toBeNull()
    expect(screen.getByRole('region', { name: 'Money' })).toBeTruthy()
    fireEvent.change(search, { target: { value: 'home' } })
    expect(screen.queryByRole('region', { name: 'Money' })).toBeNull()
    expect(screen.getByRole('region', { name: 'Plans' })).toBeTruthy()
    fireEvent.change(search, { target: { value: 'zzz' } })
    expect(screen.getByText('No memories match “zzz”.')).toBeTruthy()
  })

  it('forgets one fact through the same DELETE Settings uses', async () => {
    serve(true, [RENT])
    render(<MemorySheet onClose={vi.fn()} />)
    await openRent()
    fireEvent.click(screen.getByRole('button', { name: 'Forget: Rent goes up every March' }))
    expect(screen.getByRole('alertdialog', { name: 'Forget this memory?' })).toBeTruthy()
    expect(authFetch).not.toHaveBeenCalledWith(expect.anything(), { method: 'DELETE' })
    fireEvent.click(screen.getByRole('button', { name: 'Forget' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith('/api/memory/facts/f%201', { method: 'DELETE' }),
    )
  })

  it('asks before "Forget everything" deletes anything', async () => {
    serve(true, [RENT])
    render(<MemorySheet onClose={vi.fn()} />)
    await screen.findByText('Rent goes up every March')
    fireEvent.click(screen.getByRole('button', { name: 'Forget everything' }))
    expect(authFetch).not.toHaveBeenCalledWith('/api/memory/facts', { method: 'DELETE' })
    fireEvent.click(screen.getByRole('button', { name: 'Forget all' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith('/api/memory/facts', { method: 'DELETE' }),
    )
  })

  it('says so when memory is switched off, and keeps the change box closed', async () => {
    serve(false, [])
    render(<MemorySheet onClose={vi.fn()} />)
    expect(await screen.findByText(/Memory is off/)).toBeTruthy()
    expect(
      (screen.getByLabelText('Tell Lucius what to remember or change') as HTMLInputElement)
        .disabled,
    ).toBe(true)
  })

  it('sends a typed change to the instruct route and says what changed', async () => {
    serve(true, [RENT])
    render(<MemorySheet onClose={vi.fn()} />)
    await screen.findByText('Rent goes up every March')
    const box = screen.getByLabelText('Tell Lucius what to remember or change')
    fireEvent.change(box, { target: { value: 'my dog is Beans; rent rises in April now' } })
    fireEvent.click(screen.getByRole('button', { name: 'Update memory' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith(
        '/api/memory/instruct',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ instruction: 'my dog is Beans; rent rises in April now' }),
        }),
      ),
    )
    const card = await screen.findByRole('status')
    expect(within(card).getByText('What changed')).toBeTruthy()
    expect(within(card).getByText('Has a dog named Beans')).toBeTruthy()
    expect(within(card).getByText('Money · Housing')).toBeTruthy()
    expect(within(card).getByText('Was: Rent goes up every March')).toBeTruthy()
    expect((box as HTMLInputElement).value).toBe('')
    fireEvent.click(within(card).getByRole('button', { name: 'Dismiss what changed' }))
    expect(screen.queryByText('What changed')).toBeNull()
  })

  it('edits a fact in place and saves exactly what was typed', async () => {
    serve(true, [RENT])
    render(<MemorySheet onClose={vi.fn()} />)
    await openRent()
    fireEvent.click(screen.getByRole('button', { name: 'Edit: Rent goes up every March' }))
    const field = screen.getByRole('textbox', { name: 'Edit: Rent goes up every March' })
    fireEvent.change(field, { target: { value: 'Rent goes up every April' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith('/api/memory/facts/f%201', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: 'Rent goes up every April' }),
      }),
    )
    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: 'Edit: Rent goes up every March' })).toBeNull(),
    )
  })

  it('Escape cancels an edit without closing the dialog or saving', async () => {
    serve(true, [RENT])
    const onClose = vi.fn()
    render(<MemorySheet onClose={onClose} />)
    await openRent()
    fireEvent.click(screen.getByRole('button', { name: 'Edit: Rent goes up every March' }))
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: 'Edit: Rent goes up every March' })).toBeNull(),
    )
    expect(onClose).not.toHaveBeenCalled()
    expect(authFetch).not.toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ method: 'PATCH' }),
    )
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(await screen.findByRole('region', { name: 'Money' })).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('Escape on a topic page goes back to the list, and only then closes', async () => {
    serve(true, [RENT])
    const onClose = vi.fn()
    render(<MemorySheet onClose={onClose} />)
    await openRent()
    expect(screen.queryByRole('region', { name: 'Money' })).toBeNull()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(await screen.findByRole('region', { name: 'Money' })).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('Cancel and Escape back out of a forget without deleting or closing', async () => {
    serve(true, [RENT])
    const onClose = vi.fn()
    render(<MemorySheet onClose={onClose} />)
    await openRent()
    fireEvent.click(screen.getByRole('button', { name: 'Forget: Rent goes up every March' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Forget: Rent goes up every March' }))
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(onClose).not.toHaveBeenCalled()
    expect(authFetch).not.toHaveBeenCalledWith(expect.anything(), { method: 'DELETE' })
  })

  it('shows a loading state, not "Nothing remembered", before the facts arrive', async () => {
    let release: (r: Response) => void = () => {}
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/memory/settings') return json(settings(true))
      return new Promise<Response>((r) => {
        release = r
      })
    })
    render(<MemorySheet onClose={vi.fn()} />)
    expect(screen.getByLabelText('Loading memories')).toBeTruthy()
    expect(screen.queryByText(/Nothing remembered/)).toBeNull()
    release(new Response(JSON.stringify({ facts: [RENT], total: 1 })))
    expect(await screen.findByText('Rent goes up every March')).toBeTruthy()
  })

  it('hides the description and search on a topic page and keeps the query for the way back', async () => {
    serve(true, [RENT, DEPOSIT])
    render(<MemorySheet onClose={vi.fn()} />)
    await screen.findByText('Rent goes up every March')
    fireEvent.change(screen.getByLabelText('Search memories'), { target: { value: 'rent' } })
    await openRent()
    expect(screen.queryByLabelText('Search memories')).toBeNull()
    expect(screen.queryByText(/Durable facts Lucius carries/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Memory/ }))
    const search = screen.getByLabelText('Search memories') as HTMLInputElement
    expect(search.value).toBe('rent')
    expect(screen.queryByRole('region', { name: 'Plans' })).toBeNull()
  })

  it('offers "Forget everything" on the list only, not on a topic page', async () => {
    serve(true, [RENT, DEPOSIT])
    render(<MemorySheet onClose={vi.fn()} />)
    await screen.findByText('Rent goes up every March')
    expect(screen.getByRole('button', { name: 'Forget everything' })).toBeTruthy()
    await openRent()
    expect(screen.queryByRole('button', { name: 'Forget everything' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Memory/ }))
    expect(screen.getByRole('button', { name: 'Forget everything' })).toBeTruthy()
  })
})

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { clearQueryCache } from '../../../state/queryCache'
import { sampleFacts } from './memoryFixtures'

const authFetch = vi.fn()
vi.mock('../../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))

import { MemoryBrowser } from './MemoryBrowser'

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }))

function serve(instruct: unknown = { added: 1, updated: 0, removed: 0, rejected: [] }) {
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/memory/instruct') return json(instruct)
    if (init?.method === 'DELETE') return json({ ok: true, deleted: 4 })
    if (init?.method === 'PATCH') return json({ id: 'f1', content: 'edited' })
    return json({ facts: sampleFacts(), total: 11 })
  })
}

async function renderBrowser(props: Partial<Parameters<typeof MemoryBrowser>[0]> = {}) {
  const toast = vi.fn()
  render(<MemoryBrowser toast={toast} {...props} />)
  await screen.findByText('Rent is paid on the 1st')
  return toast
}

const topicRow = (name: string) => screen.getByText(name).closest('button') as HTMLElement

const sectionLabels = () => screen.getAllByRole('region').map((r) => r.getAttribute('aria-label'))

Element.prototype.scrollIntoView = vi.fn()

describe('MemoryBrowser', () => {
  beforeEach(() => {
    clearQueryCache()
    authFetch.mockReset()
    serve()
  })
  afterEach(cleanup)

  it('lists the sections in order, then older memories, then moved-out memories', async () => {
    await renderBrowser()
    expect(sectionLabels()).toEqual([
      'You',
      'Money',
      'Plans',
      'Open items',
      'Other',
      'Older memories — being sorted',
      'Moved out of memory',
    ])
  })

  it('shows a topic as name, first fact and updated date, with no fact count', async () => {
    await renderBrowser()
    const money = screen.getByRole('region', { name: 'Money' })
    const row = within(money).getByText('Housing').closest('button') as HTMLElement
    expect(within(row).getByText('Housing')).toBeTruthy()
    expect(within(row).getByText('Rent is paid on the 1st')).toBeTruthy()
    expect(within(row).getByText(/^Updated /)).toBeTruthy()
    expect(row.textContent).not.toMatch(/\b4\b/)
  })

  it('lists open items directly, each with its date', async () => {
    await renderBrowser()
    const open = screen.getByRole('region', { name: 'Open items' })
    expect(within(open).getByText(/Call the insurer about the renewal/)).toBeTruthy()
    expect(within(open).getByText(/until /)).toBeTruthy()
    expect(within(open).queryByRole('button', { name: /^Delete / })).toBeNull()
  })

  it('opens a topic to its facts, and the change box sends the topic', async () => {
    const toast = await renderBrowser()
    fireEvent.click(topicRow('Housing'))
    expect(screen.getByRole('button', { name: /Memory/ })).toBeTruthy()
    expect(screen.getByText('Last updated')).toBeTruthy()
    expect(screen.getByText('Lease ends in March')).toBeTruthy()
    expect(screen.queryByText('Lives with two cats')).toBeNull()

    const box = screen.getByLabelText('Tell Lucius what to remember or change')
    fireEvent.change(box, { target: { value: 'the lease now ends in May' } })
    fireEvent.click(screen.getByRole('button', { name: 'Update memory' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith(
        '/api/memory/instruct',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            instruction: 'the lease now ends in May',
            section: 'money',
            topic: 'Housing',
          }),
        }),
      ),
    )
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Added 1'))
  })

  it('the list-level change box sends no topic', async () => {
    await renderBrowser()
    fireEvent.change(screen.getByLabelText('Tell Lucius what to remember or change'), {
      target: { value: 'my dog is Beans' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Update memory' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith(
        '/api/memory/instruct',
        expect.objectContaining({ body: JSON.stringify({ instruction: 'my dog is Beans' }) }),
      ),
    )
  })

  it('says what Lucius refused to remember', async () => {
    serve({
      added: 0,
      updated: 0,
      removed: 0,
      rejected: [{ reason: 'identifier', content: 'x', section: 'you', topic: null }],
    })
    const toast = await renderBrowser()
    fireEvent.change(screen.getByLabelText('Tell Lucius what to remember or change'), {
      target: { value: 'my card number is on file' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Update memory' }))
    await waitFor(() => expect(toast).toHaveBeenCalled())
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/never remembered/))
  })

  it('offers no organising controls on a topic page', async () => {
    await renderBrowser()
    fireEvent.click(topicRow('Housing'))
    expect(screen.queryByRole('button', { name: /rename|merge|move to|section/i })).toBeNull()
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('deleting a topic asks first and names how many facts go', async () => {
    await renderBrowser()
    fireEvent.click(screen.getByRole('button', { name: 'Delete Housing' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Forget Housing?' })
    expect(dialog.textContent).toContain('Lucius will forget its 4 facts.')
    expect(authFetch).not.toHaveBeenCalledWith(expect.anything(), { method: 'DELETE' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Forget' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith('/api/memory/topics?section=money&topic=Housing', {
        method: 'DELETE',
      }),
    )
  })

  it('cancelling the topic confirm deletes nothing', async () => {
    await renderBrowser()
    fireEvent.click(screen.getByRole('button', { name: 'Delete Housing' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(authFetch).not.toHaveBeenCalledWith(expect.anything(), { method: 'DELETE' })
  })

  it('keeps moved-out memories collapsed, then shows reason and days left', async () => {
    await renderBrowser()
    expect(screen.queryByText('Works as a nurse')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Moved out of memory \(2\)/ }))
    expect(screen.getByText('Works as a nurse')).toBeTruthy()
    expect(screen.getByText('Moved to your profile')).toBeTruthy()
    expect(screen.getByText('Forgotten in 20 days')).toBeTruthy()
    expect(screen.getByText('Not used in 90 days')).toBeTruthy()
    expect(screen.getByText('Forgotten in 0 days')).toBeTruthy()
  })

  it('a moved-out memory has Forget only, and it asks first', async () => {
    await renderBrowser()
    fireEvent.click(screen.getByRole('button', { name: /Moved out of memory/ }))
    const group = screen.getByRole('region', { name: 'Moved out of memory' })
    expect(within(group).queryByRole('button', { name: /^Edit/ })).toBeNull()
    fireEvent.click(within(group).getByRole('button', { name: 'Forget: Works as a nurse' }))
    fireEvent.click(screen.getByRole('button', { name: 'Forget' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith('/api/memory/facts/f10', { method: 'DELETE' }),
    )
  })

  it('a nameless group sends its section and no topic, apart from a real "General"', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/memory/instruct')
        return json({ added: 1, updated: 0, removed: 0, rejected: [] })
      return json({
        facts: [
          { id: 'g1', content: 'Has a library card', state: 'live', section: 'other', topic: null },
          { id: 'g2', content: 'Plays chess', state: 'live', section: 'other', topic: 'General' },
        ],
        total: 2,
      })
    })
    render(<MemoryBrowser toast={vi.fn()} />)
    await screen.findByText('Has a library card')
    const other = screen.getByRole('region', { name: 'Other' })
    const rows = within(other).getAllByText('General')
    expect(rows).toHaveLength(2)

    fireEvent.click(within(other).getByText('Has a library card').closest('button') as HTMLElement)
    expect(screen.queryByText('Plays chess')).toBeNull()
    fireEvent.change(screen.getByLabelText('Tell Lucius what to remember or change'), {
      target: { value: 'it expires in May' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Update memory' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith(
        '/api/memory/instruct',
        expect.objectContaining({
          body: JSON.stringify({ instruction: 'it expires in May', section: 'other' }),
        }),
      ),
    )
  })

  it('names a memory moved out for holding a card, account or ID number', async () => {
    authFetch.mockImplementation(() =>
      json({
        facts: [
          {
            id: 's1',
            content: 'A health detail',
            state: 'retired',
            retired_reason: 'identifier',
            retired_at: new Date().toISOString(),
          },
        ],
        total: 1,
      }),
    )
    render(<MemoryBrowser toast={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /Moved out of memory \(1\)/ }))
    expect(screen.getByText('Not kept — had a card, account or ID number')).toBeTruthy()
  })

  it('files older memories under their own heading', async () => {
    await renderBrowser()
    const old = screen.getByRole('region', { name: 'Older memories — being sorted' })
    expect(within(old).getByText('Prefers metric units')).toBeTruthy()
  })

  it('filters by fact text or topic name, case-insensitively', async () => {
    const { rerender } = render(<MemoryBrowser toast={vi.fn()} filter="LEASE" />)
    await screen.findByText('Rent is paid on the 1st')
    expect(screen.queryByText('Lives with two cats')).toBeNull()
    expect(sectionLabels()).toEqual(['Money'])
    rerender(<MemoryBrowser toast={vi.fn()} filter="reading" />)
    expect(sectionLabels()).toEqual(['Other'])
    rerender(<MemoryBrowser toast={vi.fn()} filter="zzz" />)
    expect(screen.getByText('No memories match “zzz”.')).toBeTruthy()
  })

  it('counts the whole topic when deleting under a filter', async () => {
    render(<MemoryBrowser toast={vi.fn()} filter="lease" />)
    await screen.findByText('Rent is paid on the 1st')
    fireEvent.click(screen.getByRole('button', { name: 'Delete Housing' }))
    expect(screen.getByRole('alertdialog', { name: 'Forget Housing?' }).textContent).toContain(
      'its 4 facts',
    )
  })

  it('keeps a filtered row’s preview and date from the whole topic', async () => {
    render(<MemoryBrowser toast={vi.fn()} filter="lease" />)
    await screen.findByText('Rent is paid on the 1st')
    expect(screen.queryByText('Lease ends in March')).toBeNull()
  })

  it('files topics spelled in different cases under one row', async () => {
    authFetch.mockImplementation(() =>
      json({
        facts: [
          { id: 'a', content: 'One', state: 'live', section: 'money', topic: 'Housing' },
          { id: 'b', content: 'Two', state: 'live', section: 'money', topic: ' housing ' },
        ],
        total: 2,
      }),
    )
    render(<MemoryBrowser toast={vi.fn()} />)
    await screen.findByText('One')
    expect(screen.getAllByText(/^housing$/i)).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: /^Delete / }))
    expect(screen.getByRole('alertdialog').textContent).toContain('its 2 facts')
  })

  describe('topic summary', () => {
    const summary = 'Where they live — rent, lease, landlord'
    function serveSummaries(topics: unknown[]) {
      authFetch.mockImplementation(() => json({ facts: sampleFacts(), total: 11, topics }))
    }
    const labels = () =>
      Array.from(document.querySelectorAll('.rd-mem-field-label')).map((n) => n.textContent)

    it('shows the summary between Last updated and Details', async () => {
      serveSummaries([
        { section: 'money', topic: 'Housing', summary, updated_at: '2026-10-27T00:00:00+00:00' },
      ])
      await renderBrowser()
      fireEvent.click(topicRow('Housing'))
      expect(labels()).toEqual(['Last updated', 'Summary', 'Details'])
      expect(screen.getByText(summary)).toBeTruthy()
    })

    it('hides the field when the topic has no summary yet', async () => {
      serveSummaries([
        { section: 'plans', topic: 'Housing', summary, updated_at: '2026-10-27T00:00:00+00:00' },
      ])
      await renderBrowser()
      fireEvent.click(topicRow('Housing'))
      expect(labels()).toEqual(['Last updated', 'Details'])
      expect(screen.queryByText(summary)).toBeNull()
    })

    it('matches the topic case-insensitively', async () => {
      serveSummaries([
        { section: 'money', topic: ' HOUSING ', summary, updated_at: '2026-10-27T00:00:00+00:00' },
      ])
      await renderBrowser()
      fireEvent.click(topicRow('Housing'))
      expect(screen.getByText(summary)).toBeTruthy()
    })
  })
})

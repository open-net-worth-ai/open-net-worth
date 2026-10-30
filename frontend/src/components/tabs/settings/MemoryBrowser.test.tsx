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

function serve(
  instruct: unknown = {
    added: 1,
    updated: 0,
    removed: 0,
    rejected: [],
    changes: [{ change: 'added', where: 'Money · Housing', text: 'Lease now ends in May' }],
  },
) {
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

  it('lists the sections in order, then older memories, and never memories moved out', async () => {
    await renderBrowser()
    expect(sectionLabels()).toEqual(['You', 'Money', 'Plans', 'Recent', 'Other', 'Older memories'])
    expect(screen.queryByText('Works as a nurse')).toBeNull()
    expect(screen.queryByText(/Moved out of memory/)).toBeNull()
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
    const open = screen.getByRole('region', { name: 'Recent' })
    expect(within(open).getByText(/Call the insurer about the renewal/)).toBeTruthy()
    expect(within(open).getByText(/Until /)).toBeTruthy()
    expect(within(open).queryByRole('button', { name: /^Delete / })).toBeNull()
  })

  it('lays open items and older memories out as rows, an end date in the date column', async () => {
    await renderBrowser()
    const open = screen.getByRole('region', { name: 'Recent' })
    const old = screen.getByRole('region', { name: 'Older memories' })
    expect(open.querySelector('.ui-list')).toBeNull()
    expect(old.querySelector('.ui-list')).toBeNull()
    const item = within(open)
      .getByText('Call the insurer about the renewal')
      .closest('.rd-mem-topic')
    expect(item?.querySelector('.rd-mem-topic-date')?.textContent).toMatch(/^Until /)
    expect(
      within(item as HTMLElement)
        .getAllByRole('button')
        .map((b) => b.title),
    ).toEqual(['Edit', 'Forget'])
    expect(within(old).getByText('Prefers metric units').closest('.rd-mem-topic')).toBeTruthy()
  })

  it('edits an open item in place from its row', async () => {
    await renderBrowser()
    fireEvent.click(
      screen.getByRole('button', { name: 'Edit: Call the insurer about the renewal' }),
    )
    const box = screen.getByRole('textbox', { name: 'Edit: Call the insurer about the renewal' })
    fireEvent.change(box, { target: { value: 'Call the insurer on Monday' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/memory/'),
        expect.objectContaining({ method: 'PATCH' }),
      ),
    )
  })

  it('going back from a topic puts the list where it was scrolled to', async () => {
    serve()
    const toast = vi.fn()
    const { container } = render(
      <div style={{ overflowY: 'auto' }}>
        <MemoryBrowser toast={toast} />
      </div>,
    )
    await screen.findByText('Rent is paid on the 1st')
    const pane = container.firstElementChild as HTMLElement
    Object.defineProperty(pane, 'scrollHeight', { configurable: true, value: 2000 })
    Object.defineProperty(pane, 'clientHeight', { configurable: true, value: 500 })
    pane.scrollTop = 640
    fireEvent.click(topicRow('Housing'))
    pane.scrollTop = 0
    fireEvent.click(screen.getByRole('button', { name: /Memory/ }))
    await screen.findByText('Lives with two cats')
    expect(pane.scrollTop).toBe(640)
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
    expect(await screen.findByText('Lease now ends in May')).toBeTruthy()
    expect(toast).not.toHaveBeenCalled()
  })

  it('one change reads as one sentence, with no restated reply or badge', async () => {
    serve({
      added: 0,
      updated: 1,
      removed: 0,
      rejected: [],
      changes: [
        {
          change: 'updated',
          where: 'Plans · Debt reduction',
          text: 'Pays down debt first, around the bills that cannot be cut',
          was: 'Puts day-to-day needs first',
        },
      ],
      reply: 'Updated: paying down debt is now your priority.',
    })
    await renderBrowser()
    fireEvent.change(screen.getByLabelText('Tell Lucius what to remember or change'), {
      target: { value: 'debt first, but some bills stay' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Update memory' }))
    const line = (await screen.findByText('Updated a memory in Debt reduction')).closest(
      '[role="status"]',
    )!
    expect(within(line).getByText(/Pays down debt first/)).toBeTruthy()
    expect(within(line).getByText('Before: Puts day-to-day needs first')).toBeTruthy()
    expect(within(line).queryByText(/paying down debt is now your priority/)).toBeNull()
    expect(line.querySelector('.rd-mem-change-kind')).toBeNull()
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
      changes: [],
      reply: "Card numbers aren't kept in memory, so nothing was saved.",
    })
    const toast = await renderBrowser()
    fireEvent.change(screen.getByLabelText('Tell Lucius what to remember or change'), {
      target: { value: 'my card number is on file' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Update memory' }))
    const line = (await screen.findByText('No changes were made.')).closest('[role="status"]')!
    expect(within(line).queryByText(/Card numbers/)).toBeNull()
    fireEvent.click(within(line).getByRole('button', { name: 'See why' }))
    expect(
      within(line).getByText("Card numbers aren't kept in memory, so nothing was saved."),
    ).toBeTruthy()
    expect(within(line).getByText(/never remembered/)).toBeTruthy()
    expect(toast).not.toHaveBeenCalled()
  })

  it("shows the chat's working line while Lucius works, then the outcome in its place", async () => {
    let finish: () => void = () => {}
    authFetch.mockImplementation((url: string) =>
      url === '/api/memory/instruct'
        ? new Promise((resolve) => {
            finish = () =>
              resolve(
                json({ added: 0, updated: 0, removed: 0, rejected: [], changes: [], reply: '' }),
              )
          })
        : json({ facts: sampleFacts(), total: 11 }),
    )
    await renderBrowser()
    fireEvent.change(screen.getByLabelText('Tell Lucius what to remember or change'), {
      target: { value: 'forget the old rent' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Update memory' }))
    const working = await screen.findByText(/…$/, { selector: '.lx-activity-word' })
    expect(working.closest('.lx-activity')).toBeTruthy()
    finish()
    expect(await screen.findByText('No changes were made.')).toBeTruthy()
    expect(document.querySelector('.lx-activity')).toBeNull()
  })

  it('sends on Enter, and Shift+Enter starts a new line', async () => {
    await renderBrowser()
    const box = screen.getByLabelText('Tell Lucius what to remember or change')
    fireEvent.change(box, { target: { value: 'first line' } })
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true })
    expect(authFetch).not.toHaveBeenCalledWith('/api/memory/instruct', expect.anything())
    fireEvent.change(box, { target: { value: 'first line\nsecond line' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith(
        '/api/memory/instruct',
        expect.objectContaining({
          body: JSON.stringify({ instruction: 'first line\nsecond line' }),
        }),
      ),
    )
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

  it('shows the empty state when every memory has been moved out', async () => {
    authFetch.mockImplementation(() =>
      json({
        facts: [{ id: 's1', content: 'Old note', state: 'retired', retired_reason: 'stale' }],
        total: 1,
      }),
    )
    render(<MemoryBrowser toast={vi.fn()} />)
    expect(await screen.findByText(/Nothing remembered yet/)).toBeTruthy()
    expect(screen.queryByText('Old note')).toBeNull()
  })

  it('files older memories under their own heading', async () => {
    await renderBrowser()
    const old = screen.getByRole('region', { name: 'Older memories' })
    expect(within(old).getByText(/moving them into the new format in the background/)).toBeTruthy()
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
    // The row shows the summary in place of the first fact, so wait for the summary instead.
    async function renderWithSummary() {
      render(<MemoryBrowser toast={vi.fn()} />)
      await screen.findByText(summary)
    }
    const labels = () =>
      Array.from(document.querySelectorAll('.rd-mem-field-label')).map((n) => n.textContent)

    it('shows the summary between Last updated and Details', async () => {
      serveSummaries([
        { section: 'money', topic: 'Housing', summary, updated_at: '2026-10-27T00:00:00+00:00' },
      ])
      await renderWithSummary()
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

    it('shows the summary on the topic row, and the first fact until there is one', async () => {
      serveSummaries([
        { section: 'money', topic: 'Housing', summary, updated_at: '2026-10-27T00:00:00+00:00' },
      ])
      await renderWithSummary()
      expect(within(topicRow('Housing')).getByText(summary)).toBeTruthy()
      expect(within(topicRow('Housing')).queryByText('Rent is paid on the 1st')).toBeNull()
    })

    it('matches the topic case-insensitively', async () => {
      serveSummaries([
        { section: 'money', topic: ' HOUSING ', summary, updated_at: '2026-10-27T00:00:00+00:00' },
      ])
      await renderWithSummary()
      fireEvent.click(topicRow('Housing'))
      expect(screen.getByText(summary)).toBeTruthy()
    })
  })

  describe('on a touch screen', () => {
    const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
    beforeEach(() => {
      Object.defineProperty(window, 'matchMedia', {
        configurable: true,
        writable: true,
        value: (query: string) => ({
          matches: query === '(hover: none) and (pointer: coarse)',
          media: query,
          addEventListener: () => {},
          removeEventListener: () => {},
        }),
      })
    })
    afterEach(() => {
      if (original) Object.defineProperty(window, 'matchMedia', original)
    })

    it('rows swipe to Edit and Delete or Forget instead of showing icons', async () => {
      await renderBrowser()
      expect(document.querySelector('.rd-mem-topic-acts, .rd-mem-bullet-acts')).toBeNull()
      const del = screen.getByRole('button', { name: 'Delete Housing' })
      expect(del.querySelector('svg')).toBeTruthy()
      expect(del.closest('.ui-swipe-acts')).toBeTruthy()
      const forget = screen.getByRole('button', {
        name: 'Forget: Call the insurer about the renewal',
      })
      expect(forget.querySelector('svg')).toBeTruthy()
      fireEvent.click(del)
      expect(screen.getByRole('alertdialog', { name: 'Forget Housing?' })).toBeTruthy()
    })

    it('a topic’s Details bullets swipe to Edit and Forget', async () => {
      await renderBrowser()
      fireEvent.click(topicRow('Housing'))
      const forget = screen.getByRole('button', { name: 'Forget: Lease ends in March' })
      expect(forget.closest('li.rd-mem-bullet--swipe')).toBeTruthy()
      fireEvent.click(forget)
      expect(screen.getByRole('alertdialog', { name: 'Forget this memory?' })).toBeTruthy()
    })
  })
})

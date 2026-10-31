// @vitest-environment jsdom
//
// The connector list (spec §1 + §2), and the three defects the OUTGOING tab shipped —
// each asserted here at the rendered level, not just against the copy helper:
//   1. a family whose rows are all filtered out rendered a blank section;
//   2. the empty-state copy was templated off the raw segment key ("No mcp connectors");
//   3. the empty-state CTA opened the UNFILTERED directory.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react'
import { ConnectorList, visibleSections, type ListFilters } from './ConnectorList'
import type { ConnectorEntry } from './model'

afterEach(cleanup)

function entry(overrides: Partial<ConnectorEntry> & Pick<ConnectorEntry, 'id'>): ConnectorEntry {
  return {
    name: overrides.id,
    kind: 'mcp',
    icon: 'cpu',
    hue: 'var(--apt)',
    families: ['Data source'],
    statusText: 'Connected',
    tone: 'good',
    attention: false,
    enabled: true,
    sub: '',
    ...overrides,
  }
}

const TELEGRAM = entry({ id: 'telegram', name: 'Telegram', families: ['Channel'] })
const PLAID = entry({
  id: 'plaid',
  name: 'Plaid',
  statusText: 'Auth error',
  tone: 'bad',
  attention: true,
})
const DUCK = entry({ id: 'duckduckgo', name: 'DuckDuckGo', families: ['Web'], tag: 'Default' })
const TAX = entry({ id: 'mcp:tax', name: 'IRAS Tax Tables', families: ['MCP'] })
const ALL = [TELEGRAM, PLAID, DUCK, TAX]

const NONE: ListFilters = { query: '', tab: 'all', statusFilter: 'all' }

function renderList(entries: ConnectorEntry[], filters: ListFilters = NONE) {
  const onOpen = vi.fn()
  const onReconnect = vi.fn()
  const onBrowse = vi.fn()
  const onAddBank = vi.fn()
  const { container } = render(
    <ConnectorList
      entries={entries}
      filters={filters}
      onOpen={onOpen}
      onReconnect={onReconnect}
      onBrowse={onBrowse}
      onAddBank={onAddBank}
    />,
  )
  return { container, onOpen, onReconnect, onBrowse, onAddBank }
}

function sectionTitles(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('.cn-colhead')).map(
    (h) => h.querySelector('span')?.textContent ?? '',
  )
}

describe('four family sections', () => {
  it('renders one section per non-empty family, in order, with a count badge', () => {
    const { container } = renderList(ALL)
    expect(sectionTitles(container)).toEqual(['Channels', 'Data sources', 'Web', 'MCP'])
    expect(
      Array.from(container.querySelectorAll('.cn-colhead .rd-badge')).map((b) => b.textContent),
    ).toEqual(['1', '1', '1', '1'])
  })

  it('hides a family with nothing installed', () => {
    const { container } = renderList([TELEGRAM, PLAID])
    expect(sectionTitles(container)).toEqual(['Channels', 'Data sources'])
  })

  it('lists a multi-family connector under every family it holds', () => {
    const both = entry({ id: 'email', name: 'Email', families: ['Channel', 'Data source'] })
    const { container } = renderList([both])
    expect(sectionTitles(container)).toEqual(['Channels', 'Data sources'])
    expect(container.querySelectorAll('.cn-row').length).toBe(2)
  })

  it('ends each section with its own pre-scoped add link', () => {
    const { container, onBrowse } = renderList(ALL)
    const links = Array.from(container.querySelectorAll('.cn-addlink'))
    expect(links.map((l) => l.textContent?.trim())).toEqual([
      'Add a channel',
      'Add a data source',
      // The one section with a second door: which provider covers a given bank is a
      // question the directory's four look-alike aggregator cards cannot answer.
      'Find your bank',
      'Add a web provider',
      'Add an MCP server',
    ])
    fireEvent.click(links[4]!)
    expect(onBrowse).toHaveBeenCalledWith('mcp')
  })

  it('routes the bank front door to the institution search, not the directory', () => {
    const { container, onBrowse, onAddBank } = renderList(ALL)
    const bank = Array.from(container.querySelectorAll('.cn-addlink')).find(
      (l) => l.textContent?.trim() === 'Find your bank',
    )!
    fireEvent.click(bank)
    expect(onAddBank).toHaveBeenCalledTimes(1)
    expect(onBrowse).not.toHaveBeenCalled()
  })

  it('offers it only under Data sources', () => {
    const { container } = renderList(ALL, { query: '', tab: 'channels', statusFilter: 'all' })
    const labels = Array.from(container.querySelectorAll('.cn-addlink')).map((l) =>
      l.textContent?.trim(),
    )
    expect(labels).toEqual(['Add a channel'])
  })
})

describe('filters', () => {
  it('narrows to one section on a family tab', () => {
    const { container } = renderList(ALL, { ...NONE, tab: 'web' })
    expect(sectionTitles(container)).toEqual(['Web'])
    expect(container.querySelectorAll('.cn-row').length).toBe(1)
  })

  it('Attention shows only actual errors', () => {
    const { container } = renderList(ALL, { ...NONE, statusFilter: 'attention' })
    expect(sectionTitles(container)).toEqual(['Data sources'])
    expect(within(container).getByText('Plaid')).toBeTruthy()
  })

  it('never treats a deliberately disconnected connector as needing attention', () => {
    const off = entry({
      id: 'yodlee',
      name: 'Yodlee',
      enabled: false,
      tone: '',
      statusText: 'Disconnected',
    })
    const sections = visibleSections([off], { ...NONE, statusFilter: 'attention' })
    expect(sections).toEqual([])
  })

  it('Connected excludes both errors and disconnected connectors', () => {
    const off = entry({
      id: 'yodlee',
      name: 'Yodlee',
      enabled: false,
      tone: '',
      statusText: 'Disconnected',
    })
    const sections = visibleSections([...ALL, off], { ...NONE, statusFilter: 'connected' })
    const names = sections.flatMap((s) => s.entries.map((e) => e.name))
    expect(names).toEqual(['Telegram', 'DuckDuckGo', 'IRAS Tax Tables'])
  })

  it('searches name and sub-line', () => {
    const withSub = entry({ id: 'ibkr', name: 'IBKR Flex', sub: 'Flex query • nightly import' })
    expect(visibleSections([withSub], { ...NONE, query: 'nightly' })).toHaveLength(1)
    expect(visibleSections([withSub], { ...NONE, query: 'IBKR' })).toHaveLength(1)
    expect(visibleSections([withSub], { ...NONE, query: 'zzz' })).toHaveLength(0)
  })
})

describe('bug 1 — an all-filtered-out family never renders a blank section', () => {
  it('renders a real empty state instead of nothing', () => {
    const { container } = renderList(ALL, { ...NONE, tab: 'mcp', statusFilter: 'attention' })
    expect(container.querySelectorAll('.cn-colhead').length).toBe(0)
    expect(container.querySelectorAll('.cn-row').length).toBe(0)
    const empty = container.querySelector('.rd-empty')
    expect(empty).toBeTruthy()
    expect(empty!.querySelector('.rd-empty-title')?.textContent).toBe(
      'Nothing needs attention in MCP',
    )
    expect(empty!.querySelector('.rd-empty-body')?.textContent?.length).toBeGreaterThan(0)
  })

  it('always offers a way out', () => {
    renderList(ALL, { ...NONE, tab: 'channels', statusFilter: 'attention' })
    expect(screen.getByRole('button', { name: 'Browse & add' })).toBeTruthy()
  })

  it('explains an entirely empty instance', () => {
    const { container } = renderList([])
    expect(container.querySelector('.rd-empty-title')?.textContent).toBe('No connectors')
  })
})

describe('bug 2 — empty copy uses the human-readable title, not the raw key', () => {
  it('reads "Nothing in MCP yet", never "No mcp connectors"', () => {
    const { container } = renderList([TELEGRAM], { ...NONE, tab: 'mcp' })
    const title = container.querySelector('.rd-empty-title')?.textContent ?? ''
    expect(title).toBe('Nothing in MCP yet')
    expect(title).not.toContain('mcp')
  })

  it('reads "Nothing in Data sources yet", never "No data connectors"', () => {
    const { container } = renderList([TELEGRAM], { ...NONE, tab: 'data' })
    expect(container.querySelector('.rd-empty-title')?.textContent).toBe(
      'Nothing in Data sources yet',
    )
  })
})

describe('bug 3 — the empty-state CTA stays scoped to the family the user filtered to', () => {
  it('opens the directory on that family, not the full catalog', () => {
    const { onBrowse } = renderList([TELEGRAM], { ...NONE, tab: 'mcp' })
    fireEvent.click(screen.getByRole('button', { name: 'Browse & add' }))
    expect(onBrowse).toHaveBeenCalledWith('mcp')
  })

  it('keeps the scope when the emptiness came from the status filter', () => {
    const { onBrowse } = renderList(ALL, { ...NONE, tab: 'web', statusFilter: 'attention' })
    fireEvent.click(screen.getByRole('button', { name: 'Browse & add' }))
    expect(onBrowse).toHaveBeenCalledWith('web')
  })

  it('widens to the full catalog only from the All tab', () => {
    const { onBrowse } = renderList([])
    fireEvent.click(screen.getByRole('button', { name: 'Browse & add' }))
    expect(onBrowse).toHaveBeenCalledWith('all')
  })
})

describe('rows', () => {
  it('opens the detail page when a row is clicked', () => {
    const { container, onOpen } = renderList([PLAID])
    fireEvent.click(container.querySelector('.cn-row')!)
    expect(onOpen).toHaveBeenCalledWith(PLAID)
  })

  it('reconnects the erroring connector only', () => {
    const { onReconnect } = renderList(ALL)
    expect(screen.getAllByRole('button', { name: 'Reconnect' })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(onReconnect).toHaveBeenCalledWith(PLAID)
  })
})

describe('the @ mention a row offers', () => {
  it('says the status in the card\'s own words, with no trailing "• on"', () => {
    const { container } = renderList([TELEGRAM, PLAID])
    const subs = Array.from(container.querySelectorAll('[data-entity-sublabel]')).map((n) =>
      n.getAttribute('data-entity-sublabel'),
    )
    expect(subs).toEqual(['Connected', 'Auth error'])
  })
})

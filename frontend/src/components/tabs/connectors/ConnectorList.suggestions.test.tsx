// @vitest-environment jsdom
//
// The list's per-section suggestions (the Connectors twin of the Assets screen's "Add more")
// and the category chips that say what each connector is FOR.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { ConnectorList, visibleSections } from './ConnectorList'
import { ConnectorRow } from './ConnectorRow'
import { SECTION_SUGGESTION_COUNT, suggestionsBySection } from './directoryRows'
import type { ConnectorEntry } from './model'
import type { Connector } from './types'

afterEach(cleanup)

const IBKR_MCP: Connector = {
  id: 'mcp:api-ibkr-com',
  kind: 'mcp',
  name: 'Interactive Brokers',
  enabled: true,
  status: 'active',
  pausable: true,
  config: { transport: 'http', url: 'https://api.ibkr.com/v1/api/mcp-public' },
  secrets_public: {},
  tools: [],
}

function entry(overrides: Partial<ConnectorEntry> & Pick<ConnectorEntry, 'id'>): ConnectorEntry {
  return {
    name: overrides.id,
    kind: 'mcp',
    icon: 'cpu',
    hue: 'var(--apt)',
    families: ['MCP'],
    statusText: 'Connected',
    tone: 'good',
    attention: false,
    enabled: true,
    sub: '',
    ...overrides,
  }
}

describe('suggestionsBySection', () => {
  it('offers at most three per section, never the generic custom-server slot', () => {
    const bySection = suggestionsBySection([])
    for (const rows of Object.values(bySection)) {
      expect(rows!.length).toBeLessThanOrEqual(SECTION_SUGGESTION_COUNT)
      expect(rows!.some((d) => d.generic)).toBe(false)
    }
    expect(bySection.mcp!.length).toBe(SECTION_SUGGESTION_COUNT)
  })

  it('puts popular entries first and drops one already added', () => {
    const fresh = suggestionsBySection([]).mcp!
    expect(fresh.every((d) => d.popular)).toBe(true)
    expect(fresh.map((d) => d.id)).toContain('mcp-ibkr')

    const withIbkr = suggestionsBySection([IBKR_MCP]).mcp!
    expect(withIbkr.map((d) => d.id)).not.toContain('mcp-ibkr')
    expect(withIbkr.length).toBe(SECTION_SUGGESTION_COUNT)
  })
})

describe('the list', () => {
  it('shows a section’s suggestions under its rows, and opens one on click', () => {
    const onSuggest = vi.fn()
    const suggestions = suggestionsBySection([IBKR_MCP])
    render(
      <ConnectorList
        entries={[entry({ id: 'mcp:api-ibkr-com', name: 'Interactive Brokers' })]}
        filters={{ query: '', tab: 'all', statusFilter: 'all' }}
        onOpen={vi.fn()}
        onReconnect={vi.fn()}
        onBrowse={vi.fn()}
        onAddBank={vi.fn()}
        onSuggest={onSuggest}
        sectionSuggestions={suggestions}
      />,
    )
    const group = screen.getByLabelText('Suggested mcp')
    const first = suggestions.mcp![0]
    fireEvent.click(group.querySelector('.cn-dircard.suggested') as HTMLElement)
    expect(onSuggest).toHaveBeenCalledWith(first)
  })

  it('finds a connector by its category', () => {
    const broker = entry({ id: 'mcp:broker', name: 'Acme', categories: ['Brokerage'] })
    const other = entry({ id: 'mcp:other', name: 'Other', categories: ['Search'] })
    const found = visibleSections([broker, other], {
      query: 'brokerage',
      tab: 'all',
      statusFilter: 'all',
    })
    expect(found.flatMap((s) => s.entries.map((e) => e.id))).toEqual(['mcp:broker'])
  })

  it('labels a row with what it is for', () => {
    render(
      <ConnectorRow
        entry={entry({ id: 'mcp:broker', name: 'Acme', categories: ['Brokerage', 'Market data'] })}
        onOpen={vi.fn()}
        onReconnect={vi.fn()}
      />,
    )
    expect(screen.getByText('Brokerage')).toBeTruthy()
    expect(screen.getByText('Market data')).toBeTruthy()
  })
})

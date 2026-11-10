// Regression tests for the three empty-state defects found in the OUTGOING Connectors tab.
// Each `describe` below is one of them; none may come back.

import { describe, it, expect } from 'vitest'
import { emptyStateFor, familyTitle } from './emptyState'
import { FAMILY_SECTIONS, type FamilyTab, type StatusFilter } from './model'

const TABS: FamilyTab[] = ['all', 'channels', 'data', 'web', 'mcp', 'apis']
const FILTERS: StatusFilter[] = ['all', 'connected', 'attention']

describe('bug 1 — an all-filtered-out family never renders a bare page', () => {
  it('returns real copy for every tab × status × search combination', () => {
    for (const tab of TABS) {
      for (const statusFilter of FILTERS) {
        for (const query of ['', 'zzz']) {
          const copy = emptyStateFor({ query, tab, statusFilter })
          expect(copy.title.length, `${tab}/${statusFilter}/"${query}"`).toBeGreaterThan(0)
          expect(copy.body.length, `${tab}/${statusFilter}/"${query}"`).toBeGreaterThan(0)
          expect(copy.actionLabel).toBe('Browse & add')
        }
      }
    }
  })

  it('explains a family emptied by the status filter rather than showing nothing', () => {
    const copy = emptyStateFor({ query: '', tab: 'mcp', statusFilter: 'attention' })
    expect(copy.title).toBe('Nothing needs attention in MCP servers')
    expect(copy.body).toMatch(/working/i)
  })

  it('explains a family that is genuinely empty', () => {
    const copy = emptyStateFor({ query: '', tab: 'channels', statusFilter: 'all' })
    expect(copy.title).toBe('Nothing in Channels yet')
    expect(copy.body).toMatch(/Browse the directory/)
  })
})

describe('bug 2 — copy is built from the human-readable title, never the raw segment key', () => {
  it('never leaks a lowercase key into the copy', () => {
    for (const section of FAMILY_SECTIONS) {
      for (const statusFilter of FILTERS) {
        const copy = emptyStateFor({ query: '', tab: section.key, statusFilter })
        expect(copy.title).toContain(section.title)
        // the literal defect: "No mcp connectors" / "Nothing in data yet"
        expect(copy.title).not.toContain(section.key)
      }
    }
  })

  it('reads "MCP", not "mcp"', () => {
    expect(emptyStateFor({ query: '', tab: 'mcp', statusFilter: 'all' }).title).toBe(
      'Nothing in MCP servers yet',
    )
  })

  it('reads "Data sources", not "data"', () => {
    expect(emptyStateFor({ query: '', tab: 'data', statusFilter: 'all' }).title).toBe(
      'Nothing in Data sources yet',
    )
  })

  it('exposes the title for a family and null for All', () => {
    expect(familyTitle('web')).toBe('Web')
    expect(familyTitle('all')).toBeNull()
  })

  it('drops the family clause entirely on the All tab', () => {
    expect(emptyStateFor({ query: '', tab: 'all', statusFilter: 'all' }).title).toBe(
      'No connectors',
    )
    expect(emptyStateFor({ query: '', tab: 'all', statusFilter: 'attention' }).title).toBe(
      'Nothing needs attention',
    )
  })
})

describe('bug 3 — the CTA opens the directory scoped to the family the user is in', () => {
  it('carries the selected family through to the action', () => {
    for (const tab of TABS) {
      expect(emptyStateFor({ query: '', tab, statusFilter: 'all' }).actionFamily).toBe(tab)
    }
  })

  it('keeps the scope even when the emptiness came from search or a status filter', () => {
    expect(emptyStateFor({ query: 'zzz', tab: 'mcp', statusFilter: 'all' }).actionFamily).toBe(
      'mcp',
    )
    expect(emptyStateFor({ query: '', tab: 'web', statusFilter: 'connected' }).actionFamily).toBe(
      'web',
    )
  })

  it('only widens to the full catalog when the user is actually on All', () => {
    expect(emptyStateFor({ query: '', tab: 'all', statusFilter: 'all' }).actionFamily).toBe('all')
  })
})

describe('search takes precedence over the family/status copy', () => {
  it('quotes the trimmed query', () => {
    const copy = emptyStateFor({ query: '  plaid  ', tab: 'data', statusFilter: 'all' })
    expect(copy.title).toBe('No connectors match “plaid”')
  })
})

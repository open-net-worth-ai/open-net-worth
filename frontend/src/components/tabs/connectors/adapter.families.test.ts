// A connected connector sits under the same family tab as its Browse card. YNAB, SimpleFIN and
// Lunch Money were missing from the tab's own table and fell through to MCP (owner report,
// 2026-11-03).

import { describe, it, expect } from 'vitest'
import { tileInitial, tileTint, toEntry } from './adapter'
import { CATALOG } from './catalog'
import type { Connector } from './types'

function native(id: string): Connector {
  return {
    id,
    kind: id,
    name: id,
    enabled: true,
    status: 'active',
    pausable: true,
    config: {},
    secrets_public: {},
    tools: [],
  }
}

describe('a connected connector’s family', () => {
  it.each(['ynab', 'simplefin', 'lunch_money'])(
    '%s is a data source, as on its Browse card',
    (id) => {
      expect(toEntry(native(id)).families).toEqual(['Data source'])
    },
  )

  it('matches its Browse card for every built-in connector', () => {
    for (const e of CATALOG.filter((x) => !x.mcp && !x.id.includes(':'))) {
      expect([e.id, toEntry(native(e.id)).families]).toEqual([e.id, e.families])
    }
  })

  it('puts an API the owner added under APIs, not Data sources', () => {
    // It is theirs, reads AND sends, and is nothing we built (owner, 2026-11-10).
    const api = toEntry({ ...native('rest:acme_bank'), kind: 'rest', name: 'Acme Bank' })
    expect(api.families).toEqual(['API'])
  })

  it('gives each API its own tint, from its id, so a rename does not recolour it', () => {
    const tint = (id: string, name: string) => toEntry({ ...native(id), kind: 'rest', name }).hue
    expect(tint('rest:acme_bank', 'Acme Bank')).toBe(tint('rest:acme_bank', 'Renamed'))
    expect(tint('rest:acme_bank', 'Acme Bank')).toBe(tileTint('rest:acme_bank'))
    const ids = ['acme_bank', 'frankfurter', 'petstore', 'invoices', 'ledger', 'payroll']
    // Not all the same colour: the point of the tint.
    expect(new Set(ids.map((id) => tileTint(`rest:${id}`))).size).toBeGreaterThan(3)
    // The entry that ADDS an API keeps the kind's own tint.
    expect(toEntry({ ...native('rest'), kind: 'rest' }).hue).toBe('var(--cash-c)')
  })

  it('shows an added API by its initial, since they all share one glyph', () => {
    const api = (name: string) => toEntry({ ...native('rest:acme_bank'), kind: 'rest', name })
    expect(api('Acme Bank').letter).toBe('A')
    expect(api('frankfurter').letter).toBe('F')
    expect(tileInitial('  — 3rd-party ledger')).toBe('3')
    expect(tileInitial('Élan')).toBe('É')
    // The entry that adds an API, and every other connector, keep their glyph.
    expect(toEntry({ ...native('rest'), kind: 'rest' }).letter).toBeUndefined()
    expect(toEntry(native('plaid')).letter).toBeUndefined()
  })

  it('does the same for an MCP server the catalog does not know', () => {
    const custom = toEntry({ ...native('mcp:gemini-mcp'), kind: 'mcp', name: 'Gemini-mcp' })
    expect(custom.letter).toBe('G')
    expect(custom.hue).toBe(tileTint('mcp:gemini-mcp'))
  })

  it('carries what each REST tool calls, for a line under its name', () => {
    const api = toEntry({
      ...native('rest:acme_bank'),
      kind: 'rest',
      tools: [
        {
          name: 'rest__acme_bank__get_accounts',
          description: 'GET /accounts — Accounts',
          source: 'native',
          level: 'needs_approval',
          read_only: true,
        },
      ],
    })
    expect(api.toolSubs).toEqual({ rest__acme_bank__get_accounts: 'GET /accounts — Accounts' })
    // Every other connector's tools are listed by name alone, as before.
    expect(toEntry(native('plaid')).toolSubs).toBeUndefined()
  })

  it('is never MCP for a connector that is not an MCP server', () => {
    expect(toEntry(native('some_future_kind')).families).not.toContain('MCP')
  })
})

describe('why a connector needs attention', () => {
  it('carries the error the wire reports', () => {
    const entry = toEntry({ ...native('plaid'), status: 'error', error: 'Bad key' })
    expect(entry.attention).toBe(true)
    expect(entry.lastError).toBe('Bad key')
  })

  it('reads an MCP failure from its config', () => {
    const entry = toEntry({
      ...native('mcp:acme'),
      kind: 'mcp',
      config: { last_status: 'connect_error', last_error: 'Unreachable' },
    })
    expect(entry.lastError).toBe('Unreachable')
  })

  it('carries none while healthy', () => {
    const entry = toEntry({ ...native('plaid'), config: { last_error: 'An old failure' } })
    expect(entry.attention).toBe(false)
    expect(entry.lastError).toBeUndefined()
  })
})

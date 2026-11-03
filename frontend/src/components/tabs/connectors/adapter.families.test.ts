// A connected connector sits under the same family tab as its Browse card. YNAB, SimpleFIN and
// Lunch Money were missing from the tab's own table and fell through to MCP (owner report,
// 2026-11-03).

import { describe, it, expect } from 'vitest'
import { toEntry } from './adapter'
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

  it('is never MCP for a connector that is not an MCP server', () => {
    expect(toEntry(native('some_future_kind')).families).not.toContain('MCP')
  })
})

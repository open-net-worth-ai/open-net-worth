// A user-added MCP server's tile shows the icon IT or its website declared, served from our
// own origin — and nothing at all when none was found, never another connector's mark.

import { describe, it, expect } from 'vitest'
import { toEntry } from './adapter'
import type { Connector } from './types'

function mcp(config: Record<string, unknown>): Connector {
  return {
    id: 'mcp:api-broker-example',
    kind: 'mcp',
    name: 'Example Brokers',
    enabled: true,
    status: 'active',
    pausable: true,
    config: { transport: 'http', url: 'https://api.broker.example/mcp', ...config },
    secrets_public: {},
    tools: [],
  }
}

describe('an MCP server identity', () => {
  it('uses the cached icon route, versioned', () => {
    const entry = toEntry(mcp({ icon_version: 1793269380, site_source: 'www.broker.example' }))
    expect(entry.logo).toBe('/api/mcp-clients/api-broker-example/icon?v=1793269380')
    expect(entry.verification).toBe('unknown')
  })

  it('falls back to the glyph when no icon was found', () => {
    const entry = toEntry(mcp({ icon_version: null }))
    expect(entry.logo).toBeUndefined()
  })

  // The connected detail page said "Developed by Coinbase" with no link while the directory
  // card for the same server linked it (owner report, 2026-10-31).
  it('links a recognised publisher the way its directory card does', () => {
    const entry = toEntry(mcp({ url: 'https://mcp.moomoo.com/mcp' }))
    expect(entry.developer).toBe('moomoo')
    expect(entry.developerUrl).toBe('https://open.moomoo.com/mcp-docs/overview')
  })

  it('links nothing for a server it does not recognise', () => {
    expect(toEntry(mcp({})).developerUrl).toBeUndefined()
  })

  // A listed server's page read the generic hand-added text under its name (owner report,
  // 2026-11-01): it reads what its directory card reads.
  it('describes a listed server with its directory text', () => {
    const entry = toEntry({
      ...mcp({ url: 'https://api.ibkr.com/v1/api/mcp-public' }),
      short_description: 'A third-party tool server you connected.',
    })
    expect(entry.sub).toMatch(/stocks, options & futures/)
    expect(entry.desc).toMatch(/hosted server/)
  })

  it('gives a hand-added server no paragraph under its subtitle', () => {
    const entry = toEntry({ ...mcp({}), short_description: 'Trades at Example Brokers.' })
    expect(entry.sub).toBe('Trades at Example Brokers.')
    expect(entry.desc).toBe('')
  })

  it("believes a listed server's own description over the directory's", () => {
    const entry = toEntry({
      ...mcp({
        url: 'https://api.ibkr.com/v1/api/mcp-public',
        server_info_description: 'Trade at Interactive Brokers.',
      }),
      short_description: 'Trade at Interactive Brokers.',
    })
    expect(entry.sub).toBe('Trade at Interactive Brokers.')
    expect(entry.desc).toBe('')
  })
})

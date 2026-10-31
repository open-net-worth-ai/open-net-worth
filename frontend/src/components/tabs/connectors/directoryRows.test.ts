/**
 * Catalog ↔ live-state resolution, and the provenance join.
 *
 * The join exists because provenance was answering two different ways on two screens: an
 * installed Playwright MCP read "Verified" on its directory card and "Unknown publisher" on its
 * own detail page one click later. Same server, two answers — exactly what the three-state
 * field was introduced to prevent.
 */

import { describe, it, expect } from 'vitest'
import { resolveDirectory } from './directoryRows'
import { catalogProvenanceFor, CATALOG, MCP_INSTALL_PRESETS, endpointKey } from './catalog'
import { toEntry } from './adapter'
import type { Connector } from './types'

function mcpConnector(id: string, cfg: Record<string, unknown>): Connector {
  return {
    id: `mcp:${id}`,
    name: id,
    kind: 'mcp',
    enabled: true,
    config: cfg,
    secrets_public: {},
    status: 'active',
    pausable: true,
    tools: [],
  }
}

describe('catalogProvenanceFor — matched by endpoint, never by name', () => {
  it('attributes a server launched with the catalog command', () => {
    const preset = MCP_INSTALL_PRESETS['mcp-playwright']!
    const endpoint = [preset.command, preset.args].filter(Boolean).join(' ')
    expect(catalogProvenanceFor(endpoint)).toMatchObject({
      publisher: 'Microsoft',
      verification: 'verified',
      repoUrl: 'https://github.com/microsoft/playwright-mcp',
      homepageUrl: undefined,
      logo: '/connector-logos/playwright.svg',
      categories: ['Browsing'],
    })
  })

  it('still knows a server saved without the @latest the catalog now launches', () => {
    // Coinbase was added as `npx -y @coinbase/coinbase-cli mcp`; the preset gained `@latest`.
    expect(catalogProvenanceFor('npx -y @coinbase/coinbase-cli mcp')?.logo).toBe(
      catalogProvenanceFor('npx -y @coinbase/coinbase-cli@latest mcp')?.logo,
    )
    expect(catalogProvenanceFor('npx -y @coinbase/coinbase-cli@1.2.0 mcp')).not.toBeNull()
    expect(endpointKey('npx -y @scope/pkg@latest --demo')).toBe('npx -y @scope/pkg --demo')
    expect(endpointKey('uvx  pkg@2.0.1')).toBe('uvx pkg')
  })

  it('attributes nothing to an unrecognised endpoint', () => {
    expect(catalogProvenanceFor('npx -y something-else')).toBeNull()
    expect(catalogProvenanceFor('https://mcp.evil.test')).toBeNull()
    expect(catalogProvenanceFor(undefined)).toBeNull()
    expect(catalogProvenanceFor('   ')).toBeNull()
  })

  it('does NOT let a look-alike NAME inherit a verified publisher', () => {
    const impostor = mcpConnector('playwright-mcp', {
      command: 'npx',
      args: ['-y', '@attacker/playwright-mcp'],
    })
    expect(toEntry(impostor).verification).toBe('unknown')
    expect(toEntry(impostor).developer).toBeUndefined()
  })
})

describe('the detail page and the directory card agree', () => {
  it('keeps a catalog server verified on its DETAIL entry once installed', () => {
    const preset = MCP_INSTALL_PRESETS['mcp-firecrawl']!
    const installed = mcpConnector('firecrawl', {
      command: preset.command,
      args: preset.args!.split(' '),
    })

    const detail = toEntry(installed)
    expect(detail.verification).toBe('verified')
    expect(detail.developer).toBe('Firecrawl')

    // …and the directory shows the very same server as ONE card — the catalog entry, marked
    // installed and pointing at the live server — saying the same thing.
    const rows = resolveDirectory([installed])
    expect(rows.find((r) => r.id === 'mcp:firecrawl')).toBeUndefined()
    const row = rows.find((r) => r.id === 'mcp-firecrawl')!
    expect(row.installed).toBe(true)
    expect(row.installedId).toBe('mcp:firecrawl')
    expect(row.verification).toBe('verified')
    expect(row.publisher).toBe('Firecrawl')
  })

  it('leaves a user-added server unknown on both screens', () => {
    const custom = mcpConnector('mine', { url: 'https://mcp.mine.test' })
    expect(toEntry(custom).verification).toBe('unknown')
    const row = resolveDirectory([custom]).find((r) => r.id === 'mcp:mine')!
    expect(row.verification).toBe('unknown')
    expect(row.publisher).toBeNull()
  })
})

describe('resolveDirectory', () => {
  it('marks a catalog entry installed without removing it from the list', () => {
    const rows = resolveDirectory([
      {
        id: 'telegram',
        name: 'Telegram',
        kind: 'telegram',
        enabled: true,
        config: {},
        secrets_public: {},
        status: 'active',
        pausable: true,
      },
    ])
    const telegram = rows.find((r) => r.id === 'telegram')!
    expect(telegram.installed).toBe(true)
    expect(rows.length).toBe(CATALOG.length)
  })

  it('appends an installed MCP server the catalog has never heard of', () => {
    const rows = resolveDirectory([mcpConnector('mine', { url: 'https://mcp.mine.test' })])
    expect(rows.length).toBe(CATALOG.length + 1)
    expect(rows.find((r) => r.id === 'mcp:mine')?.installed).toBe(true)
  })

  it('projects `developer` from `publisher` so the two can never disagree', () => {
    for (const row of resolveDirectory([])) {
      expect(row.developer).toBe(row.publisher ?? undefined)
    }
  })
})

describe('resolveDirectory reads a native connector’s sub/desc off the live ConnectorView', () => {
  // 2026-08-14 fix: a native catalog entry no longer authors `sub`/`desc` at all (see
  // `CatalogEntry.sub`'s doc comment in `catalog.ts`) — this is the wiring that replaces it,
  // and the thing that actually closes the drift bug: the Browse & add card now reads the SAME
  // `short_description`/`long_description` the connector's own installed detail page renders
  // (`adapter.toEntry`), instead of a second hand-authored string that could silently disagree.

  function nativeConnector(
    id: string,
    kind: string,
    overrides: Partial<Connector> = {},
  ): Connector {
    return {
      id,
      name: id,
      kind: kind,
      enabled: false,
      config: {},
      secrets_public: {},
      status: 'not_configured',
      pausable: true,
      short_description: `${id} short`,
      long_description: `${id} long`,
      ...overrides,
    }
  }

  it('uses the wire description, not a catalog-authored one', () => {
    const email = nativeConnector('email', 'email')
    const row = resolveDirectory([email]).find((r) => r.id === 'email')!
    expect(row.sub).toBe('email short')
    expect(row.desc).toBe('email long')
  })

  it('reaches the text for a connector that has NEVER been configured', () => {
    // The actual Browse & add scenario: `list_connectors()` (backend) synthesizes every native
    // connector's ConnectorView unconditionally, so its description is reachable long before
    // the user has set anything up — `not_configured` must not gate this.
    const plaid = nativeConnector('plaid', 'plaid', { status: 'not_configured' })
    const row = resolveDirectory([plaid]).find((r) => r.id === 'plaid')!
    expect(row.sub).toBe('plaid short')
    expect(row.desc).toBe('plaid long')
  })

  it('leaves an MCP catalog entry on its own authored copy — the backend has no counterpart', () => {
    const row = resolveDirectory([]).find((r) => r.id === 'mcp-playwright')!
    const authored = CATALOG.find((e) => e.id === 'mcp-playwright')!
    expect(row.sub).toBe(authored.sub)
    expect(row.desc).toBe(authored.desc)
  })
})

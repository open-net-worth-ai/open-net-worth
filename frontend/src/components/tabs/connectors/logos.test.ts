// The brand-mark table, checked against what is actually on disk and against the catalog.
//
// A hand-maintained id→file map acquires two silent defects: a file nobody references (sourced,
// committed, invisible) and a reference to a file that is not there (a broken tile that only
// shows up in a browser). Both are walked here.

import { describe, it, expect } from 'vitest'
import { readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { LOGO_FILES, connectorLogo } from './logos'
import { CATALOG, RETIRED_CATALOG } from './catalog'

const LOGO_DIR = fileURLToPath(new URL('../../../../public/connector-logos', import.meta.url))

/** Everything committed to the directory, minus its provenance record. */
const onDisk = readdirSync(LOGO_DIR).filter((f) => f !== 'SOURCES.md')

describe('the map and the directory agree', () => {
  it('references a file that exists, for every entry', () => {
    for (const [id, file] of Object.entries(LOGO_FILES)) {
      expect(existsSync(join(LOGO_DIR, file)), `${id} → ${file}`).toBe(true)
    }
  })

  it('leaves no committed mark unwired — the defect this change exists to fix', () => {
    // Thirteen files were sourced deliberately, with per-file provenance, and rendered nowhere.
    const wired = new Set(Object.values(LOGO_FILES))
    expect([...onDisk].filter((f) => !wired.has(f))).toEqual([])
  })

  it('never points two connectors at one mark', () => {
    const files = Object.values(LOGO_FILES)
    expect(new Set(files).size).toBe(files.length)
  })

  it('serves each from the public path the app actually mounts', () => {
    for (const id of Object.keys(LOGO_FILES)) {
      expect(connectorLogo(id)).toMatch(/^\/connector-logos\/[\w.-]+$/)
    }
  })
})

describe('a mark is attached by ID, never by a name a user chose', () => {
  it('resolves a native connector and a catalog MCP entry', () => {
    expect(connectorLogo('plaid')).toBe('/connector-logos/plaid.png')
    expect(connectorLogo('web_search:duckduckgo')).toBe('/connector-logos/duckduckgo.svg')
    expect(connectorLogo('mcp-playwright')).toBe('/connector-logos/playwright.svg')
  })

  it('gives a user-added MCP server NOTHING, however it names itself', () => {
    // `mcp:<server_id>` is whatever the user typed. A server calling itself `plaid` or
    // `firecrawl` must not be able to borrow that vendor's mark by naming itself after it —
    // an installed catalog server inherits its logo by ENDPOINT instead (D14).
    expect(connectorLogo('mcp:plaid')).toBeUndefined()
    expect(connectorLogo('mcp:firecrawl')).toBeUndefined()
    expect(connectorLogo('mcp:playwright')).toBeUndefined()
  })

  it('returns undefined for an unknown id and for none at all', () => {
    expect(connectorLogo('yodlee')).toBeUndefined()
    expect(connectorLogo(undefined)).toBeUndefined()
    expect(connectorLogo('')).toBeUndefined()
  })
})

describe('every mapped id is a real catalog entry', () => {
  it('has no orphan keys — a typo would silently render nothing', () => {
    // A retired entry keeps its mark: a connector already set up still lists with it.
    const ids = new Set([...CATALOG, ...RETIRED_CATALOG].map((e) => e.id))
    for (const id of Object.keys(LOGO_FILES)) {
      expect(ids.has(id), id).toBe(true)
    }
  })
})

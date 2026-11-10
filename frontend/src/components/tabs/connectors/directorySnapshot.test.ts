// @vitest-environment node
//
// The backend's directory snapshot is DERIVED from the catalog. This fails when the checked-in
// file drifts from what the catalog would produce — fix with `npm run sync:connector-directory`,
// never by hand-editing the JSON.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'
import { CATALOG } from './catalog'
import { buildDirectorySnapshot, serializeDirectorySnapshot } from './directorySnapshot'

const SNAPSHOT = resolve(process.cwd(), '../backend/services/connector_directory.json')

describe('connector directory snapshot', () => {
  it('matches what the catalog produces (run npm run sync:connector-directory)', () => {
    expect(readFileSync(SNAPSHOT, 'utf8')).toBe(
      serializeDirectorySnapshot(buildDirectorySnapshot()),
    )
  })

  it('skips the custom-server launcher and keeps every other entry', () => {
    const ids = buildDirectorySnapshot().entries.map((e) => e.id)
    expect(ids).not.toContain('mcp-custom')
    expect(ids).toHaveLength(CATALOG.filter((e) => !e.generic).length)
  })

  it('carries env variable NAMES only, never a value, args or a url field', () => {
    const coinbase = buildDirectorySnapshot().entries.find((e) => e.id === 'mcp-coinbase')!
    expect(coinbase.connect).toEqual({
      kind: 'local_command',
      runtime: 'Node.js (npx)',
      env_names: ['COINBASE_ENV', 'COINBASE_KEY_ID', 'COINBASE_KEY_SECRET'],
    })
    for (const e of buildDirectorySnapshot().entries) {
      expect(Object.keys(e), e.id).not.toEqual(expect.arrayContaining(['env', 'args', 'url']))
    }
  })

  it('refuses a preset endpoint that could carry a credential', () => {
    const entry = CATALOG.find((e) => e.id === 'mcp-stripe')!
    for (const url of [
      'https://u:pw@host/mcp',
      'https://host/mcp?api_key=abc',
      'wss://host/mcp?k=1',
      'https://host/mcp#token=abc',
    ]) {
      expect(() =>
        buildDirectorySnapshot([entry], { 'mcp-stripe': { transport: 'remote', url } }),
      ).toThrow(/userinfo or a query/)
    }
  })

  it('refuses a local command that carries a DSN, credential flag or non-NAME env line', () => {
    const entry = CATALOG.find((e) => e.id === 'mcp-fred')!
    for (const args of ['-y srv postgres://user:pw@host/db', '-y srv --api-key=sk_live_x']) {
      expect(() =>
        buildDirectorySnapshot([entry], {
          'mcp-fred': { transport: 'local', command: 'npx', args },
        }),
      ).toThrow()
    }
    expect(() =>
      buildDirectorySnapshot([entry], {
        'mcp-fred': { transport: 'local', command: 'npx', env: 'AKIAxxxxxxxx' },
      }),
    ).toThrow(/NAME=value/)
  })

  it('refuses a local command whose runtime Lucius could not name', () => {
    const entry = CATALOG.find((e) => e.id === 'mcp-fred')!
    expect(() =>
      buildDirectorySnapshot([entry], {
        'mcp-fred': { transport: 'local', command: 'mystery' },
      }),
    ).toThrow(/no runtime/)
  })
})

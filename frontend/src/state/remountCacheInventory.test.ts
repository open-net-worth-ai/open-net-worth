import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const INVENTORY: Array<[string, string[]]> = [
  ['components/tabs/connectors/useConnectors.ts', ['/api/connectors']],
  ['components/tabs/settings/sections/McpSection.tsx', ['/api/auth/api-token']],
  ['api/toolPermissions.ts', ['/api/tool-permissions']],
  ['api/matches.ts', ['/api/matches/config', '/api/matches/decisions']],
]

describe('Connectors and Settings remount-cache inventory', () => {
  it.each(INVENTORY)(
    '%s declares every independently mounted read in the cache layer',
    (file, keys) => {
      const source = readFileSync(resolve(__dirname, '..', file), 'utf8')
      expect(source).toMatch(/useCachedFetchState|fetchCachedQuery/)
      for (const key of keys) expect(source).toContain(key)
    },
  )
})

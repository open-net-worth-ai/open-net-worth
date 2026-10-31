import { describe, expect, it } from 'vitest'
import { CATALOG } from './catalog'
import { RECORDED_TOOLS } from './recordedTools'

describe('recorded tool lists', () => {
  const mcpIds = new Set(CATALOG.filter((e) => e.mcp && !e.generic).map((e) => e.id))

  it('belong to a directory MCP server, never a native connector or a stale id', () => {
    for (const id of Object.keys(RECORDED_TOOLS)) expect(mcpIds, id).toContain(id)
  })

  it('carry unique names and say where they came from', () => {
    for (const [id, rec] of Object.entries(RECORDED_TOOLS)) {
      expect(new Set(rec.tools).size, id).toBe(rec.tools.length)
      expect(rec.source, id).toBeTruthy()
      expect(rec.asOf, id).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    }
  })
})

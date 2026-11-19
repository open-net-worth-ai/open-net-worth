// An MCP server that is off and has never listed a tool never connected — an abandoned OAuth
// add, or "Save without connecting" — and says what is left to do (UAT 2026-11-19).

import { describe, it, expect } from 'vitest'
import { statusOf } from './adapter'
import type { Connector } from './types'

function mcp(enabled: boolean, tools: Connector['tools']): Connector {
  return {
    id: 'mcp:api-acme-com',
    kind: 'mcp',
    name: 'api.acme.com',
    enabled,
    status: enabled ? 'active' : 'configured',
    pausable: true,
    config: {},
    secrets_public: {},
    tools,
  }
}

const TOOL = { name: 'get_positions', description: '', source: 'mcp' } as NonNullable<
  Connector['tools']
>[number]

describe('an MCP server that never connected', () => {
  it('says Finish setup when it is off with no tools', () => {
    expect(statusOf(mcp(false, []))).toEqual({
      statusText: 'Finish setup',
      tone: 'warn',
      // Off by the user's own hand is never a problem to solve: no nav dot (spec §1).
      attention: false,
    })
  })

  it('keeps Disconnected for a server that connected once and was switched off', () => {
    expect(statusOf(mcp(false, [TOOL])).statusText).toBe('Disconnected')
  })

  it('says Connected when it is on', () => {
    expect(statusOf(mcp(true, [TOOL])).statusText).toBe('Connected')
  })
})

// A bank connector with one bank waiting for a sign-in says so on the list, whatever its last
// read said: another bank's good read overwrites `last_status` (UAT 2026-11-14).

import { describe, it, expect } from 'vitest'
import { statusOf } from './adapter'
import type { Connector } from './types'

function bank(id: string, banks: Record<string, unknown>[]): Connector {
  return {
    id,
    kind: id,
    name: 'ACME',
    enabled: true,
    status: 'active',
    pausable: true,
    config: { last_status: 'ok', banks },
    secrets_public: {},
    tools: [],
  }
}

describe('a bank waiting for a sign-in', () => {
  it('flags a Plaid bank that needs Reconnect', () => {
    const s = statusOf(bank('plaid', [{ needs_reconnect: false }, { needs_reconnect: true }]))
    expect(s).toEqual({ statusText: 'Sign-in needed', tone: 'warn', attention: true })
  })

  it('flags an Enable Banking bank whose access ended', () => {
    expect(statusOf(bank('enable_banking', [{ access_ended: 'ended' }])).attention).toBe(true)
  })

  it('flags a GoCardless requisition that expired, not one still being linked', () => {
    expect(statusOf(bank('gocardless', [{ status: 'EX' }])).attention).toBe(true)
    expect(statusOf(bank('gocardless', [{ status: 'CR' }])).statusText).toBe('Connected')
  })

  it('says Connected when every bank is fine', () => {
    expect(statusOf(bank('plaid', [{ needs_reconnect: false }])).statusText).toBe('Connected')
  })
})

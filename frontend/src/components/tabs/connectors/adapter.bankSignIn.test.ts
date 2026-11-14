// A bank connector with one bank waiting for a sign-in says so on the list, whatever its last
// read said: another bank's good read overwrites `last_status` (UAT 2026-11-14).

import { describe, it, expect } from 'vitest'
import { statusOf } from './adapter'
import type { Connector } from './types'

function bank(id: string, config: Record<string, unknown>): Connector {
  return {
    id,
    kind: id,
    name: 'ACME',
    enabled: true,
    status: 'active',
    pausable: true,
    config: { last_status: 'ok', ...config },
    secrets_public: {},
    tools: [],
  }
}

describe('a bank waiting for a sign-in', () => {
  it('flags Plaid with a bank in reconnect_needed', () => {
    const s = statusOf(bank('plaid', { reconnect_needed: ['acme-bank-1'] }))
    expect(s).toEqual({ statusText: 'Sign-in needed', tone: 'warn', attention: true })
  })

  it('flags Enable Banking or GoCardless by their count', () => {
    expect(statusOf(bank('enable_banking', { banks_need_sign_in: 1 })).attention).toBe(true)
    expect(statusOf(bank('gocardless', { banks_need_sign_in: 0 })).statusText).toBe('Connected')
  })

  it('says Connected when every bank is fine', () => {
    expect(statusOf(bank('plaid', { reconnect_needed: [] })).statusText).toBe('Connected')
  })
})

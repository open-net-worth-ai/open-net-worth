// PR #93 user-acceptance scenarios, pure logic half: how an investment account's value is
// described (valuation.ts) and when the sidebar flags it for reconciling (reconcileDue.ts).
// Synthetic figures only (ACME book, 123,456.78).
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { fromApiAccount } from './data'
import { needsReconciling } from './reconcileDue'
import {
  cardFigures,
  isSyncing,
  isWaiting,
  shortDate,
  sidebarLine,
  smallRemainder,
  valueDate,
  valuationOf,
} from './valuation'
import type { ApiAccount, ApiValueSync, ValueSyncStatus } from './types'
import { setBaseCurrency } from '../../../utils/format'

const NOW = new Date(2026, 10, 10, 12)
const ORIGINAL_TZ = process.env.TZ

beforeAll(() => setBaseCurrency('USD'))
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
})
afterEach(() => {
  vi.useRealTimers()
  if (ORIGINAL_TZ === undefined) delete process.env.TZ
  else process.env.TZ = ORIGINAL_TZ
})

function api(over: Partial<ApiAccount> = {}): ApiAccount {
  return {
    id: 'acc_1',
    name: 'Old 401(k)',
    kind: 'asset',
    type: 'retirement',
    institution: 'Acme Securities',
    currency: 'USD',
    initial_balance_date: '2026-11-01',
    external_ids: [
      {
        source: 'plaid',
        ref: 'pa-1',
        name: 'Brokerage',
        mask: '4321',
        linked_at: '2026-11-01T00:00:00Z',
        provider_label: 'Plaid',
        status: 'connected',
      },
    ],
    balance: 123456.78,
    balance_base: 123456.78,
    cleared_balance: 123456.78,
    cleared_balance_base: 123456.78,
    value_source: 'total',
    value_sync: {
      status: 'fresh',
      source: 'plaid',
      provider_label: 'Plaid',
      total: 123456.78,
      changed_at: '2026-11-10T10:00:00+00:00',
    },
    value_siblings: [],
    value_breakdown: {
      tracked_holdings: 0,
      untracked: 123456.78,
      total: 123456.78,
      currency: 'USD',
    },
    ...over,
  }
}

function acct(over: Partial<ApiAccount> = {}, holdingsBase = 0) {
  return fromApiAccount(api(over), holdingsBase)
}

function sync(over: Partial<ApiValueSync>): Partial<ApiAccount> {
  return { value_sync: { status: 'fresh', source: 'plaid', provider_label: 'Plaid', ...over } }
}

describe('valuationOf', () => {
  it('reads nothing for an account without a value source (flag off, cash, credit)', () => {
    expect(valuationOf(api({ value_source: undefined }))).toBeNull()
    expect(valuationOf(api({ value_source: undefined, value_sync: undefined }))).toBeNull()
  })

  it('takes the provider label from the reading, then the link, then the source id', () => {
    expect(valuationOf(api())?.provider).toBe('Plaid')
    const noReadingLabel = api(sync({ provider_label: '' }))
    expect(valuationOf(noReadingLabel)?.provider).toBe('Plaid')
    const bare = api({
      ...sync({ provider_label: '' }),
      external_ids: [{ ...api().external_ids![0]!, provider_label: '' }],
    })
    expect(valuationOf(bare)?.provider).toBe('plaid')
  })

  it('carries the siblings, the native total and the link state', () => {
    const v = valuationOf(
      api({
        value_siblings: ['Brokerage Y'],
        external_ids: [{ ...api().external_ids![0]!, status: 'paused' }],
      }),
    )!
    expect(v.siblings).toEqual(['Brokerage Y'])
    expect(v.totalNative).toBe(123456.78)
    expect(v.linkStatus).toBe('paused')
  })

  it('treats a typed total with no connector as not syncing and not waiting', () => {
    const v = valuationOf(api({ external_ids: [], value_sync: null }))!
    expect(v.sync).toBeNull()
    expect(isSyncing(v)).toBe(false)
    expect(isWaiting(v)).toBe(false)
    expect(v.totalNative).toBe(123456.78)
  })

  it('is waiting only while a connected total account has no first reading', () => {
    expect(isWaiting(valuationOf(api(sync({ status: 'waiting' })))!)).toBe(true)
    const paused = api({
      ...sync({ status: 'waiting' }),
      external_ids: [{ ...api().external_ids![0]!, status: 'paused' }],
    })
    expect(isWaiting(valuationOf(paused)!)).toBe(false)
    expect(
      isWaiting(valuationOf(api({ ...sync({ status: 'waiting' }), value_source: 'holdings' }))!),
    ).toBe(false)
  })
})

describe('cardFigures: native with no holdings, base with holdings', () => {
  it('shows the account in its own currency while nothing is tracked (FX-07)', () => {
    const eur = acct({
      currency: 'EUR',
      balance: 10000,
      balance_base: 11000,
      value_breakdown: { tracked_holdings: 0, untracked: 10000, total: 10000, currency: 'EUR' },
    })
    expect(cardFigures(eur)).toEqual({
      ccy: 'EUR',
      tracked: 0,
      untracked: 10000,
      total: 10000,
    })
  })

  it('switches to base once holdings exist: tracked 3,020.00 + untracked 7,980.01', () => {
    const eur = acct({ currency: 'EUR', balance: 7254.55, balance_base: 7980.01 }, 3020)
    const f = cardFigures(eur)
    expect(f.ccy).toBe('USD')
    expect(f.tracked).toBe(3020)
    expect(f.untracked).toBe(7980.01)
    expect(f.total).toBeCloseTo(11000.01, 2)
  })

  it('USD account with holdings: 3,020.00 + 4,980.00 = 8,000.00', () => {
    const f = cardFigures(acct({ balance: 4980, balance_base: 4980 }, 3020))
    expect(f).toEqual({ ccy: 'USD', tracked: 3020, untracked: 4980, total: 8000 })
  })

  it('keeps a negative untracked part negative (holdings beyond the typed total)', () => {
    const f = cardFigures(acct({ balance: -50, balance_base: -50 }, 9800))
    expect(f.untracked).toBe(-50)
    expect(f.total).toBe(9750)
  })
})

describe('smallRemainder: the 2% switch prompt (UI-03 / UI-04)', () => {
  const at = (tracked: number, untracked: number) =>
    smallRemainder(acct({ balance: untracked, balance_base: untracked }, tracked))

  it('offers the switch at exactly 2.00% (200.00 of 10,000.00)', () => {
    expect(at(9800, 200)).toBe(true)
  })
  it('offers it just under 2% (199.99)', () => {
    expect(at(9800, 199.99)).toBe(true)
  })
  it('does not offer it just over 2% (200.01 of 10,000.01)', () => {
    expect(at(9800, 200.01)).toBe(false)
  })
  it('UI-04: never offers it for a negative untracked part', () => {
    expect(at(9800, -50)).toBe(false)
  })
  it('UI-04: never offers it when the total is zero or negative', () => {
    expect(at(100, -100)).toBe(false)
    expect(at(100, -200)).toBe(false)
  })
  it('never offers it with nothing tracked', () => {
    expect(at(0, 0)).toBe(false)
    expect(smallRemainder(acct({ balance: 8000, balance_base: 8000 }, 0))).toBe(false)
  })
})

describe('valueDate and shortDate', () => {
  it('uses the connector change date for a fresh total', () => {
    expect(valueDate(acct())).toBe('2026-11-10T10:00:00+00:00')
  })

  it('UI-18: a kept total dates from the last reconcile, never changed_at', () => {
    const kept = acct({
      ...sync({ status: 'kept', changed_at: '2026-11-10T10:00:00+00:00' }),
      last_reconciled_at: '2026-10-20',
    })
    expect(valueDate(kept)).toBe('2026-10-20')
  })

  it('UI-18: a kept total with no reconcile dates from the starting balance', () => {
    const kept = acct(sync({ status: 'kept', changed_at: '2026-11-10T10:00:00+00:00' }))
    expect(valueDate(kept)).toBe('2026-11-01')
    expect(shortDate(valueDate(kept))).toBe('Nov 1')
  })

  it('has no date when nothing was ever stamped', () => {
    const none = acct({ external_ids: [], value_sync: null, initial_balance_date: null })
    expect(valueDate(none)).toBeNull()
    expect(shortDate(null)).toBe('')
  })

  it('UI-17: a date-only string is never shifted by the timezone', () => {
    for (const tz of ['America/Los_Angeles', 'Asia/Tokyo', 'UTC']) {
      process.env.TZ = tz
      expect(shortDate('2026-01-01')).toBe('Jan 1')
    }
  })

  it('UI-17: 2026-11-10T23:30:00Z reads Nov 10 in UTC-8 and Nov 11 in UTC+9', () => {
    process.env.TZ = 'America/Los_Angeles'
    expect(shortDate('2026-11-10T23:30:00Z')).toBe('Nov 10')
    process.env.TZ = 'Asia/Tokyo'
    expect(shortDate('2026-11-10T23:30:00Z')).toBe('Nov 11')
  })
})

describe('sidebarLine (UI-16 / UI-12)', () => {
  it('an unlinked typed total says "Entered Nov 1" from its starting balance', () => {
    expect(sidebarLine(acct({ external_ids: [], value_sync: null }))).toEqual({
      text: 'Entered Nov 1',
      warn: false,
    })
  })

  it('says bare "Entered" when no date is known', () => {
    const a = acct({ external_ids: [], value_sync: null, initial_balance_date: null })
    expect(sidebarLine(a)).toEqual({ text: 'Entered', warn: false })
  })

  it('a fresh linked total says "Updated Nov 10"', () => {
    expect(sidebarLine(acct())).toEqual({ text: 'Updated Nov 10', warn: false })
  })

  it('a fresh reading with no date says bare "Updated"', () => {
    const a = acct({ ...sync({ status: 'fresh' }), initial_balance_date: null })
    expect(sidebarLine(a)).toEqual({ text: 'Updated', warn: false })
  })

  it('a kept total reads "Updated" with the reconcile or start date, not the reading date', () => {
    const a = acct({ ...sync({ status: 'kept', changed_at: '2026-11-10T10:00:00Z' }) })
    expect(sidebarLine(a)).toEqual({ text: 'Updated Nov 1', warn: false })
  })

  it.each<[ValueSyncStatus, string, boolean]>([
    ['waiting', 'Waiting for total', false],
    ['held_back', 'Needs your OK', true],
    ['reconnect', 'Reconnect', true],
    ['stale', 'Not updating', true],
    ['error', 'Not updating', true],
    ['currency_mismatch', 'Not updating', true],
    ['no_balance', 'Not updating', true],
    ['not_found', 'Not updating', true],
  ])('a %s total reads "%s" (warn=%s)', (status, text, warn) => {
    expect(sidebarLine(acct(sync({ status })))).toEqual({ text, warn })
  })

  it.each(['paused', 'disconnected'] as const)(
    'a %s connector falls back to "Entered": it keeps nothing up to date',
    (status) => {
      const a = acct({ external_ids: [{ ...api().external_ids![0]!, status }] })
      expect(sidebarLine(a)).toEqual({ text: 'Entered Nov 10', warn: false })
    },
  )

  it('says nothing for a Live holdings account and for an account on the old valuation', () => {
    expect(sidebarLine(acct({ value_source: 'holdings' }))).toBeNull()
    expect(sidebarLine(acct({ value_source: undefined }))).toBeNull()
  })
})

describe('needsReconciling for a Brokerage total account (REC-19)', () => {
  const old = { initial_balance_date: '2025-01-01' }

  it('never asks while a connected connector keeps the total, whatever its status', () => {
    for (const status of ['fresh', 'stale', 'held_back', 'error', 'reconnect'] as const)
      expect(needsReconciling(acct({ ...old, ...sync({ status }) }), NOW)).toBe(false)
  })

  it('asks again once the connector is paused or removed', () => {
    for (const status of ['paused', 'disconnected'] as const) {
      const a = acct({ ...old, external_ids: [{ ...api().external_ids![0]!, status }] })
      expect(needsReconciling(a, NOW)).toBe(true)
    }
  })

  it('an unlinked total follows the 30-day cadence from its opening', () => {
    const unlinked = { external_ids: [], value_sync: null }
    expect(needsReconciling(acct({ ...unlinked, initial_balance_date: '2026-10-11' }), NOW)).toBe(
      false,
    )
    expect(needsReconciling(acct({ ...unlinked, initial_balance_date: '2026-10-10' }), NOW)).toBe(
      true,
    )
  })

  it('a recent reconcile quiets an unlinked total; an old one asks', () => {
    const unlinked = { external_ids: [], value_sync: null, ...old }
    expect(needsReconciling(acct({ ...unlinked, last_reconciled_at: '2026-11-01' }), NOW)).toBe(
      false,
    )
    expect(needsReconciling(acct({ ...unlinked, last_reconciled_at: '2026-08-01' }), NOW)).toBe(
      true,
    )
  })

  it('a Live holdings account follows the usual cadence even with a connector', () => {
    expect(needsReconciling(acct({ ...old, value_source: 'holdings' }), NOW)).toBe(true)
  })
})

// @vitest-environment jsdom
//
// PR #93 user-acceptance scenarios for the Accounts sidebar (UI-16, REC-19, FX-08, FLAG-12): the
// one line under a Brokerage total account's name, its precedence against "Needs reconciling",
// and the figure on the row. Synthetic figures only.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'

import { Sidebar, type SidebarTotals } from './Sidebar'
import { fromApiAccount } from './data'
import type { Account, ApiAccount, ApiValueSync } from './types'
import { setBaseCurrency } from '../../../utils/format'

const totals: SidebarTotals = {
  cash: 0,
  fixed_deposit: 0,
  investment: 0,
  retirement: 0,
  receivable: 0,
  credit: 0,
  loan: 0,
  liabilities: 0,
}

const PLAID = {
  source: 'plaid',
  ref: 'pa-1',
  name: 'Brokerage',
  mask: '4321',
  linked_at: '2026-11-01T00:00:00Z',
  provider_label: 'Plaid',
  status: 'connected' as const,
}

beforeAll(() => setBaseCurrency('USD'))
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 10, 12))
})
afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

function api(over: Partial<ApiAccount> = {}): ApiAccount {
  return {
    id: 'acc_1',
    name: 'Old 401(k)',
    kind: 'asset',
    type: 'retirement',
    institution: '',
    currency: 'USD',
    initial_balance_date: '2026-11-01',
    external_ids: [],
    balance: 8000,
    balance_base: 8000,
    cleared_balance: 8000,
    cleared_balance_base: 8000,
    value_source: 'total',
    value_sync: null,
    value_siblings: [],
    ...over,
  } as ApiAccount
}

function linked(sync: Partial<ApiValueSync>, over: Partial<ApiAccount> = {}): ApiAccount {
  return api({
    external_ids: [PLAID],
    value_sync: {
      status: 'fresh',
      source: 'plaid',
      provider_label: 'Plaid',
      total: 8000,
      changed_at: '2026-11-10T10:00:00+00:00',
      ...sync,
    },
    ...over,
  })
}

function rowOf(a: ApiAccount, holdingsBase = 0) {
  const account: Account = fromApiAccount(a, holdingsBase)
  const group = a.type === 'retirement' ? 'retirement' : 'investment'
  const view = render(
    <Sidebar
      accounts={[account]}
      groups={[{ key: group, label: group, color: '#34b08a', accounts: [account], totalBase: 0 }]}
      totals={totals}
      selectedId={null}
      netWorth={0}
      collapsed={false}
      onSelect={vi.fn()}
      onAdd={vi.fn()}
      onManagePayees={vi.fn()}
    />,
  )
  const row = view.container.querySelector('.acct-row--leaf') as HTMLElement
  return {
    view,
    row,
    sub: row.querySelector('.acct-row-sub')?.textContent ?? null,
    src: row.querySelector('.acct-row-src'),
    due: row.querySelector('.acct-due'),
    bal: row.querySelector('.acct-row-bal')?.textContent ?? null,
    cur: row.querySelector('.acct-row-cur')?.textContent ?? null,
  }
}

describe('the line under the name', () => {
  it.each([
    ['unlinked typed total', api(), 'Entered Nov 1', false],
    ['fresh linked total', linked({}), 'Updated Nov 10', false],
    [
      'waiting for the first total',
      linked({ status: 'waiting', total: undefined, changed_at: undefined }),
      'Waiting for total',
      false,
    ],
    ['held back', linked({ status: 'held_back', held_total: 0 }), 'Needs your OK', true],
    ['expired sign-in', linked({ status: 'reconnect' }), 'Reconnect', true],
    ['stale', linked({ status: 'stale' }), 'Not updating', true],
    ['error', linked({ status: 'error' }), 'Not updating', true],
    [
      'currency mismatch',
      linked({ status: 'currency_mismatch', reported_currency: 'EUR' }),
      'Not updating',
      true,
    ],
    ['no balance', linked({ status: 'no_balance' }), 'Not updating', true],
    ['not found', linked({ status: 'not_found' }), 'Not updating', true],
  ])('%s reads "%s"', (_n, a, text, warn) => {
    const r = rowOf(a)
    expect(r.src?.textContent).toBe(text)
    expect(r.src?.className.includes('acct-row-src--warn')).toBe(warn)
    expect(r.due).toBeNull()
  })

  it('a paused connector falls back to "Entered" and the link icon goes grey', () => {
    const r = rowOf(linked({}, { external_ids: [{ ...PLAID, status: 'paused' }] }))
    expect(r.src?.textContent).toBe('Entered Nov 10')
    expect(r.row.querySelector('.acct-row-linked--off')).not.toBeNull()
  })

  it('a healthy linked row shows a live link icon and the line together', () => {
    const r = rowOf(linked({}))
    expect(r.row.querySelector('.acct-row-linked')).not.toBeNull()
    expect(r.row.querySelector('.acct-row-linked--off')).toBeNull()
    expect(r.sub).toBe('Updated Nov 10')
  })

  it('card digits sit before the line, separated by a dot', () => {
    const r = rowOf(api({ last_four: '4321' }))
    expect(r.sub).toBe('•••4321 · Entered Nov 1')
  })

  it('a Live holdings account and an account on the old valuation get no line', () => {
    expect(rowOf(api({ value_source: 'holdings' })).src).toBeNull()
    cleanup()
    expect(rowOf(api({ value_source: undefined })).src).toBeNull()
  })
})

describe('"Needs reconciling" against the line (REC-19)', () => {
  it('an unlinked typed total over a month old says "Needs reconciling" and no "Entered" line', () => {
    const r = rowOf(api({ initial_balance_date: '2025-01-01' }))
    expect(r.due?.textContent).toBe('Needs reconciling')
    expect(r.src).toBeNull()
  })

  it('an unlinked total inside its month grace says "Entered Oct 20", not the marker', () => {
    const r = rowOf(api({ initial_balance_date: '2026-10-20' }))
    expect(r.due).toBeNull()
    expect(r.src?.textContent).toBe('Entered Oct 20')
  })

  it('a connected total is never due, however old its opening is', () => {
    const r = rowOf(linked({}, { initial_balance_date: '2025-01-01' }))
    expect(r.due).toBeNull()
    expect(r.src?.textContent).toBe('Updated Nov 10')
  })

  it('a paused connector with an old opening is due again', () => {
    const r = rowOf(
      linked(
        {},
        { initial_balance_date: '2025-01-01', external_ids: [{ ...PLAID, status: 'paused' }] },
      ),
    )
    expect(r.due?.textContent).toBe('Needs reconciling')
    expect(r.src).toBeNull()
  })

  it('the venue-less "All accounts" counter counts a due unlinked total, not a connected one', () => {
    const { view } = rowOf(api({ initial_balance_date: '2025-01-01' }))
    expect(view.container.querySelector('.acct-all-sub.acct-due')?.textContent).toBe(
      '1 needs reconciling',
    )
    cleanup()
    const connected = rowOf(linked({}, { initial_balance_date: '2025-01-01' }))
    expect(connected.view.container.querySelector('.acct-all-sub.acct-due')).toBeNull()
  })
})

describe('the figure on the row (FX-08)', () => {
  it('a total account with holdings reads one compact base value, $8K from 3,020.00 + 4,980.00', () => {
    const r = rowOf(api({ balance: 4980, balance_base: 4980 }), 3020)
    expect(r.bal).toBe('◈$8K')
    expect(r.cur).toBe('value')
  })

  it('a EUR total account with no holdings shows its own currency, €10,000.00 EUR', () => {
    const r = rowOf(api({ currency: 'EUR', balance: 10000, balance_base: 11000 }))
    expect(r.bal).toBe('€10,000.00')
    expect(r.cur).toBe('EUR')
  })

  it('a EUR total account with holdings reads base: 3,020.00 + 7,980.01 = $11K (compact)', () => {
    const r = rowOf(api({ currency: 'EUR', balance: 7254.55, balance_base: 7980.01 }), 3020)
    expect(r.bal).toBe('◈$11K')
    expect(r.cur).toBe('value')
  })

  it('a negative untracked part with holdings reads the total, $9.75K (compact)', () => {
    const r = rowOf(api({ balance: -50, balance_base: -50 }), 9800)
    expect(r.bal).toBe('◈$9.75K')
    expect(r.row.querySelector('.acct-row-bal.neg')).toBeNull()
  })
})

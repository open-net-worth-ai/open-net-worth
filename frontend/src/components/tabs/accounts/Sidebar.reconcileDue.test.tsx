// @vitest-environment jsdom
//
// The sidebar says which accounts need reconciling, so the owner doesn't open each one to find
// out (owner, 2026-10-26). In words — "Reconcile due", "Never reconciled", "N need reconciling" —
// never a clock or a bare "6d ago", which read as the last transaction's age. Accounts inside the
// month say nothing, and a never-reconciled account gets a month's grace from its opening.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { Sidebar, type SidebarTotals } from './Sidebar'
import { fromApiAccount } from './data'
import { reconcileDue } from './reconcileDue'
import type { ApiAccount } from './types'

const NOW = new Date(2026, 9, 26, 12)
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
const noop = vi.fn()

function cash(id: string, name: string, extra: Partial<ApiAccount> = {}) {
  return fromApiAccount({
    id,
    name,
    kind: 'asset',
    type: 'cash',
    currency: 'SGD',
    balance: 100,
    balance_base: 100,
    cleared_balance: 100,
    cleared_balance_base: 100,
    initial_balance_date: '2026-01-01',
    ...extra,
  } as ApiAccount)
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
})
afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe('reconcileDue', () => {
  it('flags an overdue reconcile and stays quiet inside the month', () => {
    expect(reconcileDue(cash('a', 'A', { last_reconciled_at: '2026-06-08' }), NOW)).toBe('due')
    expect(reconcileDue(cash('a', 'A', { last_reconciled_at: '2026-10-20' }), NOW)).toBeNull()
  })

  it('gives a never-reconciled account a month from its opening', () => {
    expect(reconcileDue(cash('a', 'A'), NOW)).toBe('never')
    expect(reconcileDue(cash('a', 'A', { initial_balance_date: '2026-10-10' }), NOW)).toBeNull()
    expect(reconcileDue(cash('a', 'A', { initial_balance_date: null }), NOW)).toBeNull()
  })
})

describe('Sidebar — reconcile status', () => {
  function renderWith(accounts: ReturnType<typeof cash>[]) {
    return render(
      <Sidebar
        accounts={accounts}
        groups={[{ key: 'cash', label: 'Cash', color: '#34b08a', accounts, totalBase: 0 }]}
        totals={totals}
        selectedId={null}
        netWorth={0}
        collapsed={false}
        onSelect={noop}
        onAdd={noop}
        onManagePayees={noop}
      />,
    )
  }

  it('names what needs doing on the row, the venue and All accounts — and nothing else', () => {
    const { container } = renderWith([
      cash('fresh', 'Harbor Checking', { last_reconciled_at: '2026-10-20' }),
      cash('stale', 'Meridian Wallet', { last_reconciled_at: '2026-06-08' }),
      cash('never', 'Lantern Card'),
      cash('v1', 'Pier Savings', { institution: 'Pier', last_reconciled_at: '2026-01-02' }),
      cash('v2', 'Pier Everyday', { institution: 'Pier', last_reconciled_at: '2026-10-25' }),
    ])

    expect(screen.getByText('Reconcile due')).toBeTruthy()
    expect(screen.getByText('Never reconciled')).toBeTruthy()
    expect(screen.getByText('1 needs reconciling')).toBeTruthy()
    expect(container.querySelector('.acct-all-sub')?.textContent).toBe(
      '5 accounts · 3 need reconciling',
    )
    const fresh = screen.getByText('Harbor Checking').closest('.acct-row')
    expect(fresh?.querySelector('.acct-due')).toBeNull()
    expect(container.textContent).not.toMatch(/days? ago|\dd ago/)
  })

  it('says nothing about reconciling when every account is current', () => {
    const { container } = renderWith([
      cash('fresh', 'Harbor Checking', { last_reconciled_at: '2026-10-20' }),
    ])
    expect(container.querySelector('.acct-due')).toBeNull()
    expect(container.querySelector('.acct-all-sub')?.textContent).toBe('1 account')
  })

  it('has no icon on All accounts, so it lines up with the group headings', () => {
    const { container } = renderWith([cash('fresh', 'Harbor Checking')])
    expect(container.querySelector('.acct-all svg')).toBeNull()
  })
})

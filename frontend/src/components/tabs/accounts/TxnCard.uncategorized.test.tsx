// @vitest-environment jsdom

// "Uncategorized" is an expense/income-only bucket (services.ledger.is_uncategorized, the
// register's Uncategorized filter, desktop TxnRow). The mobile card once labelled every
// non-transfer blank-category row — a trade on an investment account read "Uncategorized"
// on a phone while the filter for it came back empty.

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { TxnCard } from './TxnCard'
import type { Account, Category, Txn } from './types'

afterEach(cleanup)

const account: Account = {
  id: 'a1',
  name: 'Harbor Brokerage',
  kind: 'asset',
  type: 'investment',
  institution: 'Harbor',
  currency: 'USD',
  initialBalance: 0,
  initialBalanceDate: null,
  creditLimit: null,
  lastFour: null,
  note: '',
  statementCloseDay: null,
  paymentDueDay: null,
  reminderDays: null,
  lastReconciled: null,
  archived: false,
  connected: false,
  apr: null,
  principal: null,
  payoffDate: null,
  minPayment: null,
  apy: null,
  balance: 100,
  balanceBase: 100,
  clearedBalance: 100,
  clearedBalanceBase: 100,
  role: 'cash',
  holdingsBase: 0,
}

const base: Txn = {
  id: 't1',
  date: '2026-10-13',
  kind: 'expense',
  category: null,
  payeeId: null,
  payeeName: null,
  description: 'Bought 10 ACME @ 50.00',
  memo: '',
  tags: [],
  splits: [],
  fromAccountId: 'a1',
  fromAmount: 500,
  fromCurrency: 'USD',
  toAccountId: null,
  toAmount: null,
  toCurrency: null,
  origAmount: null,
  origCurrency: null,
  baseAmount: 500,
  baseCurrency: 'USD',
  cleared: false,
  source: 'manual',
  createdAt: '2026-10-13',
  deleted: false,
  needsReview: false,
  externalRefs: [],
}

function renderCard(t: Txn) {
  return render(
    <TxnCard
      t={t}
      accounts={[account]}
      showAccount={false}
      selectedId="a1"
      selected={false}
      onToggle={() => {}}
      onUpdate={() => {}}
      payeeName={null}
      rates={{}}
      splitOpen={false}
      onSplit={() => {}}
    />,
  )
}

describe('TxnCard Uncategorized label', () => {
  it('labels a blank-category expense Uncategorized', () => {
    const { container } = renderCard(base)
    expect(container.querySelector('.acct-unsorted')?.textContent).toBe('Uncategorized')
  })

  it('does not label a blank-category investment trade Uncategorized', () => {
    const { container } = renderCard({ ...base, kind: 'investment' })
    expect(container.querySelector('.acct-unsorted')).toBeNull()
  })
})

describe('TxnCard amount sub-line', () => {
  it('reads a cross-currency transfer as one flow, no bullet before the arrow', () => {
    const { container } = render(
      <TxnCard
        t={{
          ...base,
          kind: 'transfer',
          fromAccountId: 'a2',
          fromAmount: 5000,
          fromCurrency: 'EUR',
          toAccountId: 'a1',
          toAmount: 5610,
          toCurrency: 'USD',
        }}
        accounts={[account, { ...account, id: 'a2', name: 'Harbor EUR', currency: 'EUR' }]}
        showAccount
        selectedId={null}
        selected={false}
        onToggle={() => {}}
        onUpdate={() => {}}
        payeeName={null}
        rates={{}}
        splitOpen={false}
        onSplit={() => {}}
      />,
    )
    const sub = container.querySelector('.acct-amt-orig')?.textContent ?? ''
    expect(sub).toMatch(/5,000\.00 → .*5,610\.00/)
    expect(sub).not.toContain('•')
  })
})

describe('TxnCard split chip', () => {
  // A bare "Split • 6" said nothing about where the money went (owner, 2026-10-29).
  const cats: Record<string, Category> = {
    cat_rent: { id: 'cat_rent', label: 'Rent', color: '#e05050', kind: 'expense' },
    cat_net: { id: 'cat_net', label: 'Phone & Internet', color: '#c050c0', kind: 'expense' },
  }
  const split: Txn = {
    ...base,
    kind: 'income',
    category: 'split',
    fromAccountId: null,
    fromAmount: null,
    fromCurrency: null,
    toAccountId: 'a1',
    toAmount: 607,
    toCurrency: 'USD',
    splits: [
      { category: 'cat_net', amount: 7, note: '' },
      { category: 'cat_rent', amount: 600, note: 'October' },
    ],
  }
  const renderSplit = (open: boolean, onSplit: (id: string) => void = () => {}) =>
    render(
      <TxnCard
        t={split}
        accounts={[account]}
        showAccount={false}
        selectedId="a1"
        selected={false}
        onToggle={() => {}}
        onUpdate={() => {}}
        payeeName={null}
        rates={{}}
        catMap={cats}
        splitOpen={open}
        onSplit={onSplit}
      />,
    )

  it('leads with the largest line and counts the rest', () => {
    const { container } = renderSplit(false)
    expect(container.querySelector('.acct-split-chip')?.textContent).toBe('Rent+1')
    expect(container.querySelector('.acct-card-splits')).toBeNull()
  })

  it("blends every line's colour into the chip background", () => {
    const { container } = renderSplit(false)
    const bg = (container.querySelector('.acct-split-chip') as HTMLElement).style.background
    expect(bg).toContain('linear-gradient')
    // Line order (Phone & Internet, then Rent), not amount order — jsdom serializes hex as rgb().
    expect(bg.indexOf('rgb(192, 80, 192)')).toBeGreaterThan(-1)
    expect(bg.indexOf('rgb(192, 80, 192)')).toBeLessThan(bg.indexOf('rgb(224, 80, 80)'))
  })

  it('toggles without opening the detail', () => {
    let toggled = ''
    const { container } = renderSplit(false, (id) => (toggled = id))
    ;(container.querySelector('.acct-split-chip') as HTMLButtonElement).click()
    expect(toggled).toBe('t1')
  })

  it('expands the lines inside the card, signed like the parent, notes kept', () => {
    const { container } = renderSplit(true)
    const lines = [...container.querySelectorAll('.acct-card-splits .acct-splitline')]
    expect(lines.map((l) => l.textContent)).toEqual([
      'Phone & Internet+$7.00',
      'RentOctober+$600.00',
    ])
    expect(container.querySelector('.acct-card-body .acct-card-splits')).not.toBeNull()
  })
})

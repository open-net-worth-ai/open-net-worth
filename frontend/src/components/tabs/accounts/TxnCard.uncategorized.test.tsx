// @vitest-environment jsdom

// "Uncategorized" is an expense/income-only bucket (services.ledger.is_uncategorized, the
// register's Uncategorized filter, desktop TxnRow). The mobile card once labelled every
// non-transfer blank-category row — a trade on an investment account read "Uncategorized"
// on a phone while the filter for it came back empty.

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { TxnCard } from './TxnCard'
import type { Account, Txn } from './types'

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

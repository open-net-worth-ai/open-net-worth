// @vitest-environment jsdom
//
// `loadingTxns` gates the empty-state branch (sorted.length === 0) so the initial fetch shows a
// spinner instead of "No transactions yet". It must NOT touch the branch that renders actual
// rows — a background refetch (switching accounts, applying a filter, a post-write reload) with
// txns already on screen must keep showing those rows, never swap them for a loading state.

import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import { render, cleanup, screen } from '@testing-library/react'
import { Register } from './Register'
import type { Account, Txn } from './types'

afterEach(cleanup)
beforeEach(() => localStorage.clear())

function makeAccount(over: Partial<Account> = {}): Account {
  return {
    id: 'a1',
    name: 'Meridian Savings',
    kind: 'asset',
    type: 'cash',
    institution: 'Meridian',
    currency: 'SGD',
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
    ...over,
  }
}

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-06-20',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'FAIRPRICE #123',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 42.5,
    fromCurrency: 'SGD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 42.5,
    baseCurrency: 'SGD',
    cleared: true,
    source: 'manual',
    createdAt: '2026-06-20',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

function renderReg(
  over: {
    txns?: Txn[]
    loadingTxns?: boolean
    refiltering?: boolean
    allEmpty?: boolean
    isLoadingMore?: boolean
  } = {},
) {
  return render(
    <Register
      txns={over.txns ?? [makeTxn()]}
      total={over.txns?.length ?? 1}
      accounts={[makeAccount()]}
      showAccount={false}
      selectedId="a1"
      selectedIds={new Set()}
      onToggle={() => {}}
      onToggleAll={() => {}}
      onClearSelection={() => {}}
      onSelectRange={() => {}}
      onUpdate={() => {}}
      onAdd={() => {}}
      adding={false}
      onAdding={() => {}}
      categories={[]}
      allEmpty={over.allEmpty ?? false}
      loadingTxns={over.loadingTxns ?? false}
      refiltering={over.refiltering ?? false}
      isLoadingMore={over.isLoadingMore ?? false}
      payeeOptions={[]}
      payeeNames={{}}
      rates={{}}
      hiddenCols={new Set()}
    />,
  )
}

describe('Register — loadingTxns never overrides rows already on screen', () => {
  it('a background refetch (loadingTxns=true) with existing rows still shows those rows, not a spinner', () => {
    renderReg({ txns: [makeTxn()], loadingTxns: true })
    expect(screen.getAllByText('FAIRPRICE #123').length).toBeGreaterThan(0)
    expect(screen.queryByText(/Loading transactions/i)).toBeNull()
  })

  it('the initial fetch (no rows yet, loadingTxns=true) shows a spinner, not the empty-state CTAs', () => {
    renderReg({ txns: [], loadingTxns: true, allEmpty: true })
    expect(screen.getByText(/Loading transactions/i)).toBeTruthy()
    expect(screen.queryByText(/No transactions yet/i)).toBeNull()
  })

  it('a refetch over existing rows (a filter change) shows the header progress bar and marks the register busy', () => {
    const { container } = renderReg({ txns: [makeTxn()], loadingTxns: true, refiltering: true })
    expect(container.querySelector('.acct-reg-loadbar')).not.toBeNull()
    expect(container.querySelector('.acct-reg.refetching[aria-busy="true"]')).not.toBeNull()
  })

  it('no progress bar for a same-query revalidation (a tab return), nor once settled', () => {
    const revalidating = renderReg({ txns: [makeTxn()], loadingTxns: true, refiltering: false })
    expect(revalidating.container.querySelector('.acct-reg-loadbar')).toBeNull()
    expect(revalidating.container.querySelector('[aria-busy]')).toBeNull()
    revalidating.unmount()
    const settled = renderReg({ txns: [makeTxn()], loadingTxns: false })
    expect(settled.container.querySelector('.acct-reg-loadbar')).toBeNull()
  })

  it('a genuinely empty account (loadingTxns=false, no rows) shows the empty-state CTAs', () => {
    renderReg({ txns: [], loadingTxns: false, allEmpty: true })
    expect(screen.getByText(/No transactions yet/i)).toBeTruthy()
    expect(screen.queryByText(/Loading transactions/i)).toBeNull()
  })
})

describe('Register — isLoadingMore shows a trailing indicator during infinite-scroll append', () => {
  it('an in-flight loadMore (isLoadingMore=true) shows "Loading more…" below the existing rows', () => {
    renderReg({ txns: [makeTxn()], isLoadingMore: true })
    expect(screen.getAllByText('FAIRPRICE #123').length).toBeGreaterThan(0)
    expect(screen.getByText(/Loading more/i)).toBeTruthy()
  })

  it('no in-flight loadMore (isLoadingMore=false) shows no trailing indicator', () => {
    renderReg({ txns: [makeTxn()], isLoadingMore: false })
    expect(screen.queryByText(/Loading more/i)).toBeNull()
  })
})

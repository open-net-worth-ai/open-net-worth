// @vitest-environment jsdom
//
// Hide amounts is a CSS mask: `body.hide-amounts .mask` blanks a figure, so a figure WITHOUT the
// class stays readable. Each surface opts in by hand, which is how a phone came to show every
// transaction amount under Hide amounts: TxnRow (desktop) carried `mask`, TxnCard (phone) never
// did. These walk the rendered text, so a new unmasked figure fails without naming its selector.

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { AccountHeader, type DueInfo } from './AccountHeader'
import { fromApiAccount } from './data'
import TxnDetail from './TxnDetail'
import { TxnCard } from './TxnCard'
import { TxnRow } from './TxnRow'
import type { Account, ApiAccount, Category, Txn } from './types'

afterEach(cleanup)

function unmaskedMoney(root: Element): string[] {
  const money = /[$€£]\s?\d|\d[\d,]*\.\d{2}\b/
  const found: string[] = []
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  while (walker.nextNode()) {
    const node = walker.currentNode
    // A bank's settled rate ("1 EUR ≈ $0.78") is a market fact, not the user's figure.
    if (node.parentElement?.closest('.acct-detail-fxfact')) continue
    if (money.test(node.textContent ?? '') && !node.parentElement?.closest('.mask')) {
      found.push((node.textContent ?? '').trim())
    }
  }
  return found
}

const API_ACCOUNT: ApiAccount = {
  id: 'a1',
  name: 'Harbor Checking',
  kind: 'asset',
  type: 'cash',
  currency: 'USD',
  balance: 100,
  balance_base: 100,
  cleared_balance: 100,
  cleared_balance_base: 100,
}
const account: Account = fromApiAccount(API_ACCOUNT)

const cats: Record<string, Category> = {
  cat_rent: { id: 'cat_rent', label: 'Rent', color: '#e05050', kind: 'expense' },
  cat_net: { id: 'cat_net', label: 'Internet', color: '#c050c0', kind: 'expense' },
}

const base: Txn = {
  id: 't1',
  date: '2026-10-13',
  kind: 'expense',
  category: null,
  payeeId: null,
  payeeName: null,
  description: 'Corner Shop',
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

const foreignCharge: Txn = { ...base, origAmount: 640, origCurrency: 'EUR' }

const split: Txn = {
  ...base,
  category: 'split',
  fromAmount: 607,
  splits: [
    { category: 'cat_net', amount: 7, note: '' },
    { category: 'cat_rent', amount: 600, note: 'October' },
  ],
}

const crossCurrencyTransfer: Txn = {
  ...base,
  kind: 'transfer',
  fromAccountId: 'a2',
  fromAmount: 5000,
  fromCurrency: 'EUR',
  toAccountId: 'a1',
  toAmount: 5610,
  toCurrency: 'USD',
}

const accounts = [account, { ...account, id: 'a2', name: 'Harbor EUR', currency: 'EUR' }]

function renderCard(t: Txn, splitOpen = false) {
  return render(
    <TxnCard
      t={t}
      accounts={accounts}
      showAccount={false}
      selectedId="a1"
      selected={false}
      onToggle={() => {}}
      onUpdate={() => {}}
      payeeName={null}
      rates={{}}
      catMap={cats}
      splitOpen={splitOpen}
      onSplit={() => {}}
    />,
  )
}

function renderDetail(t: Txn) {
  return render(
    <TxnDetail
      txn={t}
      accounts={accounts}
      categories={[]}
      payeeOptions={[]}
      baseCurrency="SGD"
      rates={{ USD: 1, EUR: 1, SGD: 1 }}
      onClose={() => {}}
      onSave={() => {}}
      onDelete={() => {}}
      onMarkReviewed={() => {}}
      onManagePayees={() => {}}
      onCreateCategory={() => {}}
      onLoadActivity={() =>
        Promise.resolve({
          source: 'manual',
          addedBy: 'You',
          createdAt: '2026-10-13',
          updatedAt: '2026-10-13',
          activity: [],
        })
      }
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
}

describe('Hide amounts covers the phone transaction card', () => {
  it('masks the amount', () => {
    const { container } = renderCard(base)
    expect(container.querySelector('.acct-card-amt')?.textContent).toContain('500.00')
    expect(unmaskedMoney(container)).toEqual([])
  })

  it('masks the original-currency line', () => {
    const { container } = renderCard(foreignCharge)
    expect(container.querySelector('.acct-amt-orig')?.textContent).toContain('640')
    expect(unmaskedMoney(container)).toEqual([])
  })

  it('masks the expanded split lines', () => {
    const { container } = renderCard(split, true)
    expect(container.querySelectorAll('.acct-splitline-amt')).toHaveLength(2)
    expect(unmaskedMoney(container)).toEqual([])
  })
})

describe('Hide amounts covers the desktop register', () => {
  it('masks the expanded split lines', () => {
    const { container } = render(
      <TxnRow
        t={split}
        accounts={accounts}
        showAccount={false}
        selectedId="a1"
        selected={false}
        onToggle={() => {}}
        onUpdate={() => {}}
        splitOpen
        onSplit={() => {}}
        payeeOptions={[]}
        payeeName="Corner Shop"
        tmpl="1fr"
        colKeys={['cb', 'clr', 'date', 'payee', 'category', 'description', 'source', 'amount']}
        rates={{}}
        categories={[]}
        catMap={cats}
      />,
    )
    expect(container.querySelectorAll('.acct-split-amt')).toHaveLength(2)
    expect(unmaskedMoney(container)).toEqual([])
  })
})

describe('Hide amounts covers the transaction detail', () => {
  it('masks the headline amount and its base-currency line', () => {
    renderDetail(base)
    expect(document.querySelector('.acct-detail-amt')?.textContent).toContain('500.00')
    expect(document.querySelector('.acct-detail-base')?.textContent).toContain('in SGD')
    expect(unmaskedMoney(document.body)).toEqual([])
  })

  it('masks the original charge', () => {
    renderDetail(foreignCharge)
    expect(document.body.textContent).toContain('Original charge')
    expect(unmaskedMoney(document.body)).toEqual([])
  })

  it('masks what a cross-currency transfer received', () => {
    renderDetail(crossCurrencyTransfer)
    expect(document.body.textContent).toContain('received in USD')
    expect(unmaskedMoney(document.body)).toEqual([])
  })

  it('leaves the words around a masked figure readable', () => {
    renderDetail(base)
    const line = document.querySelector('.acct-detail-base') as HTMLElement
    expect(line.classList.contains('mask')).toBe(false)
    expect(line.querySelector('.mask')?.textContent).toContain('500.00')
  })
})

describe('Hide amounts covers the account header', () => {
  const card = fromApiAccount({
    ...API_ACCOUNT,
    id: 'c1',
    kind: 'liability',
    type: 'credit',
    balance: -45.2,
    balance_base: -45.2,
    cleared_balance: -45.2,
    cleared_balance_base: -45.2,
  })
  const noop = () => {}

  function renderHeader(dueInfo: DueInfo) {
    return render(
      <AccountHeader
        account={card}
        dueInfo={dueInfo}
        stats={null}
        menuOpen={false}
        setMenuOpen={noop}
        collapsed={false}
        onReopen={noop}
        onAddTxn={noop}
        onEdit={noop}
        onArchive={noop}
        onDelete={noop}
        onManagePayees={noop}
        onManageCategories={noop}
        onImport={noop}
        onExport={noop}
        onReconcile={noop}
        onReview={noop}
        onStartReview={noop}
      />,
    )
  }

  const due = {
    amountDue: 120,
    totalDue: 300,
    paidAmount: 180,
    minPayment: 25,
    currency: 'USD',
    days: 5,
    closeDays: 12,
    partial: true,
    source: 'statement',
    missingForCycle: false,
    statementId: null,
  } as DueInfo

  it('masks every figure on a partly paid due banner', () => {
    const { container } = renderHeader(due)
    expect(container.textContent).toContain('still')
    expect(unmaskedMoney(container)).toEqual([])
  })

  it('masks the figure on a full-payment due banner', () => {
    const { container } = renderHeader({ ...due, partial: false, paidAmount: 0 })
    expect(container.textContent).toContain('Payment of')
    expect(unmaskedMoney(container)).toEqual([])
  })

  it('masks the figures on a paid banner', () => {
    const { container } = renderHeader({ ...due, settled: true, partial: false })
    expect(container.textContent).toContain('paid in full')
    expect(unmaskedMoney(container)).toEqual([])
  })

  it('masks the figures on a paid estimate banner', () => {
    const { container } = renderHeader({ ...due, settled: true, partial: false, source: 'config' })
    expect(container.textContent).toContain('paid this cycle')
    expect(unmaskedMoney(container)).toEqual([])
  })

  it('masks the balance the reconcile prompt asks about', () => {
    renderHeader(due)
    fireEvent.click(screen.getByRole('button', { name: 'Reconcile' }))
    expect(screen.getByRole('dialog').textContent).toContain('45.20')
    expect(unmaskedMoney(screen.getByRole('dialog'))).toEqual([])
  })

  it('masks the reconcile adjustment, but not "In sync"', () => {
    renderHeader(due)
    fireEvent.click(screen.getByRole('button', { name: 'Reconcile' }))
    fireEvent.click(screen.getByText('No'))
    const input = screen.getByPlaceholderText('actual')

    fireEvent.change(input, { target: { value: '-50' } })
    expect(screen.getByRole('dialog').textContent).toContain('4.80')
    expect(unmaskedMoney(screen.getByRole('dialog'))).toEqual([])

    fireEvent.change(input, { target: { value: '-45.20' } })
    expect(screen.getByText('In sync').classList.contains('mask')).toBe(false)
  })
})

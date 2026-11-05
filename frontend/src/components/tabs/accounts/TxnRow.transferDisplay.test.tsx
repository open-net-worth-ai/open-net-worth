// @vitest-environment jsdom
//
// A transfer in All accounts read as an expense: it fell through to the outflow sign (−) with the
// outflow colour, and a long source account name clipped the arrow out of the payee cell, leaving
// what looked like a spend at that account. selectionTotal already counted it as net zero.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { TxnRow } from './TxnRow'
import { TxnCard } from './TxnCard'
import type { Account, Txn } from './types'

afterEach(cleanup)

function makeAccount(id: string, name: string): Account {
  return {
    id,
    name,
    kind: 'asset',
    type: 'cash',
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
    balance: 1000,
    balanceBase: 1000,
    clearedBalance: 1000,
    clearedBalanceBase: 1000,
  }
}

const ACCOUNTS = [makeAccount('a1', 'Acme Loan'), makeAccount('a2', 'Harbor Checking')]

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-07-30',
    kind: 'transfer',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'Loan disbursement',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 500,
    fromCurrency: 'USD',
    toAccountId: 'a2',
    toAmount: 500,
    toCurrency: 'USD',
    origAmount: null,
    origCurrency: null,
    baseAmount: 500,
    baseCurrency: 'USD',
    cleared: true,
    source: 'manual',
    createdAt: '2026-07-30',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

const ALL_COLS = ['cb', 'clr', 'account', 'date', 'payee', 'category', 'source', 'amount']
const NO_ACCOUNT_COL = ALL_COLS.filter((k) => k !== 'account')

function renderRow(t: Txn, selectedId: string | null, colKeys = ALL_COLS) {
  return render(
    <TxnRow
      t={t}
      accounts={ACCOUNTS}
      showAccount={selectedId === null}
      selectedId={selectedId}
      selected={false}
      onToggle={() => {}}
      onUpdate={() => {}}
      splitOpen={false}
      onSplit={() => {}}
      payeeOptions={[]}
      payeeName={null}
      tmpl={'1fr'}
      colKeys={colKeys}
      rates={{}}
      categories={[]}
    />,
  )
}

function renderCard(t: Txn, selectedId: string | null) {
  return render(
    <TxnCard
      t={t}
      accounts={ACCOUNTS}
      showAccount={selectedId === null}
      selectedId={selectedId}
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

const text = (root: HTMLElement, selector: string) =>
  root.querySelector<HTMLElement>(selector)!.textContent ?? ''

describe('transfer amount', () => {
  it('has no sign and no outflow colour in All accounts', () => {
    const { container } = renderRow(makeTxn(), null)
    const amt = container.querySelector<HTMLElement>('.acct-td-amt')!
    expect(text(container, '.acct-amt-main')).not.toMatch(/^[+−-]/)
    expect(text(container, '.acct-amt-main')).toContain('500.00')
    expect(amt.classList.contains('transfer')).toBe(true)
    expect(amt.classList.contains('neg')).toBe(false)
  })

  it('keeps − on the account the money left and + on the one it reached', () => {
    const out = renderRow(makeTxn(), 'a1')
    expect(text(out.container, '.acct-amt-main')).toMatch(/^−/)
    expect(out.container.querySelector('.acct-td-amt.neg')).toBeTruthy()
    out.unmount()

    const into = renderRow(makeTxn(), 'a2')
    expect(text(into.container, '.acct-amt-main')).toMatch(/^\+/)
    expect(into.container.querySelector('.acct-td-amt.pos')).toBeTruthy()
  })

  it('leaves every other kind signed in All accounts', () => {
    const oneLeg = { toAccountId: null, toAmount: null, toCurrency: null }
    for (const kind of ['expense', 'adjustment', 'investment'] as const) {
      const { container, unmount } = renderRow(makeTxn({ kind, ...oneLeg }), null)
      expect(text(container, '.acct-amt-main'), kind).toMatch(/^−/)
      unmount()
    }
  })

  it('is unsigned on the mobile card too', () => {
    const { container } = renderCard(makeTxn(), null)
    expect(text(container, '.acct-amt-main')).not.toMatch(/^[+−-]/)
    expect(container.querySelector('.acct-card-amt.transfer')).toBeTruthy()
  })

  it('has a colour rule for both surfaces', () => {
    const css = readFileSync(
      resolve(process.cwd(), 'src/components/tabs/accounts/accounts.css'),
      'utf8',
    )
    expect(css).toMatch(
      /\.acct-td-amt\.transfer,\s*\.acct-card-amt\.transfer\s*\{\s*color: var\(--muted\)/,
    )
  })
})

describe('transfer payee label', () => {
  it('names only the destination where the Account column already shows the source', () => {
    const { container } = renderRow(makeTxn(), null)
    expect(text(container, '.acct-payee-nm')).toBe('Transfer→Harbor Checking')
    expect(container.querySelector('.acct-td-acct')!.textContent).toBe('Acme Loan')
  })

  it('names both accounts when nothing on screen shows the source', () => {
    const hidden = renderRow(makeTxn(), null, NO_ACCOUNT_COL)
    expect(text(hidden.container, '.acct-payee-nm')).toBe('Transfer: Acme Loan→Harbor Checking')
    hidden.unmount()

    const card = renderCard(makeTxn(), null)
    expect(text(card.container, '.acct-payee-nm')).toBe('Transfer: Acme Loan→Harbor Checking')
  })

  it('inside one of its accounts, names only the other account, in the direction of the money', () => {
    const source = renderRow(makeTxn(), 'a1', NO_ACCOUNT_COL)
    expect(text(source.container, '.acct-payee-nm')).toBe('Transfer→Harbor Checking')
    source.unmount()

    const destination = renderRow(makeTxn(), 'a2', NO_ACCOUNT_COL)
    expect(text(destination.container, '.acct-payee-nm')).toBe('Transfer←Acme Loan')
    destination.unmount()

    const sourceCard = renderCard(makeTxn(), 'a1')
    expect(text(sourceCard.container, '.acct-payee-nm')).toBe('Transfer→Harbor Checking')
    sourceCard.unmount()

    const destinationCard = renderCard(makeTxn(), 'a2')
    expect(text(destinationCard.container, '.acct-payee-nm')).toBe('Transfer←Acme Loan')
  })

  it('keeps the full route in a tooltip for when the cell clips', () => {
    const { container } = renderRow(makeTxn(), null)
    expect(container.querySelector<HTMLElement>('.acct-payee-nm')!.title).toBe(
      'Acme Loan → Harbor Checking',
    )
  })

  it('leaves a balance adjustment and a normal payee alone', () => {
    const adj = renderRow(makeTxn({ kind: 'adjustment' }), null)
    expect(text(adj.container, '.acct-payee-nm')).toBe('Balance adjustment')
    adj.unmount()

    const expense = renderRow(makeTxn({ kind: 'expense', toAccountId: null }), null)
    expect(text(expense.container, '.acct-payee-nm')).toBe('Loan disbursement')
  })
})

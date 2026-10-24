// @vitest-environment jsdom
//
// Column-visibility picker + the Memo column (PR #10 memo field). Covers: the memo column
// renders its value and defaults visible; the picker toggles a column off so the header AND
// rows drop that cell and the grid template shrinks; the choice persists across remount
// (localStorage `acctColVis_v1`); the structural cb/clr/amount columns are never offered or
// hideable; an empty memo renders blank; and the inline add row tracks the same visible set.

import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import { render, cleanup, fireEvent, within } from '@testing-library/react'
import { Register } from './Register'
import { ColumnsPicker, useColumnVisibility, REG_VIS_KEY, toggleableCols } from './columns'
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

/** Wires the picker to the register through the real `useColumnVisibility` hook — exactly the
 *  AccountsTab shape (hook owns `hidden`, feeds the register's filter + the picker's toggles). */
function Harness({
  txns,
  showAccount = false,
  adding = false,
}: {
  txns: Txn[]
  showAccount?: boolean
  adding?: boolean
}) {
  const cv = useColumnVisibility()
  return (
    <div>
      <ColumnsPicker
        showAccount={showAccount}
        hidden={cv.hidden}
        onToggle={cv.toggle}
        onReset={cv.reset}
      />
      <Register
        txns={txns}
        total={txns.length}
        accounts={[makeAccount()]}
        showAccount={showAccount}
        selectedId={showAccount ? null : 'a1'}
        selectedIds={new Set()}
        onToggle={() => {}}
        onToggleAll={() => {}}
        onClearSelection={() => {}}
        onSelectRange={() => {}}
        onUpdate={() => {}}
        onAdd={() => {}}
        adding={adding}
        onAdding={() => {}}
        categories={[]}
        allEmpty={false}
        loadingTxns={false}
        isLoadingMore={false}
        payeeOptions={[]}
        payeeNames={{}}
        rates={{}}
        hiddenCols={cv.hidden}
      />
    </div>
  )
}

const head = (c: HTMLElement) => c.querySelector('.acct-reg-head') as HTMLElement
const headCols = (c: HTMLElement) => c.querySelectorAll('.acct-reg-head .acct-h').length
function openPicker(c: HTMLElement): HTMLElement {
  fireEvent.click(within(c).getByTitle('Show or hide columns'))
  return c.querySelector('[role="dialog"]') as HTMLElement
}

describe('Register memo column', () => {
  it('renders the memo value and is visible by default', () => {
    const { container } = render(<Harness txns={[makeTxn({ memo: 'Split with Alex' })]} />)
    expect(within(head(container)).getByText('Memo')).toBeTruthy()
    const cell = container.querySelector('.acct-td-memo2') as HTMLElement
    expect(cell).toBeTruthy()
    expect(cell.textContent).toContain('Split with Alex')
  })

  it('renders an empty memo as a blank em-dash', () => {
    const { container } = render(<Harness txns={[makeTxn({ memo: '' })]} />)
    const cell = container.querySelector('.acct-td-memo2') as HTMLElement
    expect(cell).toBeTruthy()
    expect(cell.textContent).toBe('—')
  })
})

const regTmpl = (c: HTMLElement) =>
  (c.querySelector('.acct-reg') as HTMLElement).style.getPropertyValue('--reg-tmpl')

describe('Register column-visibility picker', () => {
  it('hiding a column drops it from the header AND every row, and shrinks the grid template', () => {
    const { container } = render(
      <Harness txns={[makeTxn({ id: 't1', memo: 'a' }), makeTxn({ id: 't2', memo: 'b' })]} />,
    )
    const tmplBefore = regTmpl(container)
    const colsBefore = headCols(container)
    expect(container.querySelectorAll('.acct-td-memo2').length).toBe(2)

    const dialog = openPicker(container)
    fireEvent.click(within(dialog).getByText('Memo')) // uncheck Memo

    expect(within(head(container)).queryByText('Memo')).toBeNull()
    expect(container.querySelectorAll('.acct-td-memo2').length).toBe(0)
    expect(headCols(container)).toBe(colsBefore - 1)
    expect(regTmpl(container)).not.toBe(tmplBefore)
    // Amount is structural — untouched by the toggle.
    expect(within(head(container)).getByText('Amount')).toBeTruthy()
  })

  it('never offers cb / clr / amount as hideable toggles', () => {
    const { container } = render(<Harness txns={[makeTxn()]} />)
    const dialog = openPicker(container)
    const labels = Array.from(dialog.querySelectorAll('.acct-advcheck')).map((b) => b.textContent)
    expect(labels).toEqual(['Date', 'Payee', 'Category', 'Description', 'Memo', 'Source'])
    expect(labels).not.toContain('Amount')
    expect(labels).not.toContain('C') // the clr column's header label
  })

  it('offers Account only in the all-accounts view', () => {
    const single = render(<Harness txns={[makeTxn()]} showAccount={false} />)
    expect(
      Array.from(openPicker(single.container).querySelectorAll('.acct-advcheck')).map(
        (b) => b.textContent,
      ),
    ).not.toContain('Account')
    cleanup()
    const all = render(<Harness txns={[makeTxn()]} showAccount />)
    expect(
      Array.from(openPicker(all.container).querySelectorAll('.acct-advcheck')).map(
        (b) => b.textContent,
      ),
    ).toContain('Account')
  })

  it('persists the hidden set across a remount (localStorage)', () => {
    const first = render(<Harness txns={[makeTxn({ memo: 'x' })]} />)
    fireEvent.click(within(openPicker(first.container)).getByText('Memo'))
    expect(JSON.parse(localStorage.getItem(REG_VIS_KEY) || '[]')).toContain('memo')
    first.unmount()

    const { container } = render(<Harness txns={[makeTxn({ memo: 'x' })]} />)
    expect(container.querySelectorAll('.acct-td-memo2').length).toBe(0)
    expect(within(head(container)).queryByText('Memo')).toBeNull()
  })

  it('“Show all” restores every hidden column', () => {
    const { container } = render(<Harness txns={[makeTxn({ memo: 'x' })]} />)
    const cols0 = headCols(container)
    let dialog = openPicker(container)
    fireEvent.click(within(dialog).getByText('Memo'))
    fireEvent.click(within(dialog).getByText('Source'))
    expect(headCols(container)).toBe(cols0 - 2)
    dialog = container.querySelector('[role="dialog"]') as HTMLElement
    fireEvent.click(within(dialog).getByText('Show all'))
    expect(headCols(container)).toBe(cols0)
    expect(localStorage.getItem(REG_VIS_KEY)).toBe('[]')
  })
})

describe('AddRow tracks the visible column set', () => {
  it('shows a Memo input by default and drops it when the memo column is hidden', () => {
    const { container } = render(<Harness txns={[makeTxn()]} adding />)
    expect(container.querySelector('input[placeholder="Memo"]')).toBeTruthy()
    fireEvent.click(within(openPicker(container)).getByText('Memo'))
    expect(container.querySelector('input[placeholder="Memo"]')).toBeNull()
    // Description input is a separate cell and stays.
    expect(container.querySelector('input[placeholder="Description"]')).toBeTruthy()
  })
})

describe('toggleableCols helper', () => {
  it('excludes the structural spine and gates account on the view', () => {
    const keys = (sa: boolean) => toggleableCols(sa).map((c) => c.key)
    expect(keys(false)).toEqual(['date', 'payee', 'category', 'description', 'memo', 'source'])
    expect(keys(true)).toEqual([
      'account',
      'date',
      'payee',
      'category',
      'description',
      'memo',
      'source',
    ])
  })
})

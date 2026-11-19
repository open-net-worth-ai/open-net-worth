// @vitest-environment jsdom
//
// Clicking away from an inline cell edit only ends the edit. It used to reach the clicked row's
// onClick too and open that transaction's drawer (owner report 2026-11-19).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { TxnRow } from './TxnRow'
import type { Account, Txn } from './types'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})
beforeEach(() => vi.useFakeTimers())

const ACCOUNT = {
  id: 'a1',
  name: 'ACME Checking',
  kind: 'asset',
  type: 'cash',
  role: 'cash',
  holdingsBase: 0,
  institution: 'ACME',
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
} as Account

function txn(id: string, description: string): Txn {
  return {
    id,
    date: '2026-11-01',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description,
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 5,
    fromCurrency: 'USD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 5,
    baseCurrency: 'USD',
    cleared: false,
    source: 'manual',
    createdAt: '2026-11-01',
    deleted: false,
    needsReview: false,
    externalRefs: [],
  } as Txn
}

function renderRows(onOpenDetail: (t: Txn) => void, onUpdate = vi.fn()) {
  const rows = [txn('t1', 'DISP TEMP CREDIT'), txn('t2', 'Corner Cafe')]
  const view = render(
    <div>
      {rows.map((t) => (
        <TxnRow
          key={t.id}
          t={t}
          accounts={[ACCOUNT]}
          showAccount={false}
          selectedId="a1"
          selected={false}
          onToggle={() => {}}
          onUpdate={onUpdate}
          onOpenDetail={onOpenDetail}
          splitOpen={false}
          onSplit={() => {}}
          payeeOptions={[]}
          payeeName={null}
          tmpl="1fr"
          colKeys={['date', 'payee', 'category', 'description', 'memo', 'amount']}
          rates={{}}
          categories={[]}
        />
      ))}
    </div>,
  )
  const rowEls = view.container.querySelectorAll<HTMLElement>('.acct-tr')
  return { view, rowEls }
}

/** A real click: pointerdown (which blurs the editor) then the click itself. */
function clickAt(el: HTMLElement) {
  fireEvent.pointerDown(el)
  act(() => (document.activeElement as HTMLElement | null)?.blur())
  fireEvent.click(el)
}

describe('ending an inline edit by clicking away', () => {
  it('commits the edit and opens no drawer — not even for the row that was clicked', () => {
    const onOpenDetail = vi.fn()
    const onUpdate = vi.fn()
    const { view, rowEls } = renderRows(onOpenDetail, onUpdate)
    fireEvent.doubleClick(rowEls[0]!.querySelector('.acct-td-memo') as HTMLElement)
    const input = view.container.querySelector('input.acct-editin') as HTMLInputElement
    expect(input).toBeTruthy()
    expect(document.activeElement).toBe(input)

    clickAt(rowEls[1]!.querySelector('.acct-td-memo') as HTMLElement)
    act(() => vi.advanceTimersByTime(500))

    expect(onUpdate).toHaveBeenCalledWith('t1', { description: 'DISP TEMP CREDIT' })
    expect(onOpenDetail).not.toHaveBeenCalled()
  })

  it('the NEXT click on a row opens its drawer as usual', () => {
    const onOpenDetail = vi.fn()
    const { view, rowEls } = renderRows(onOpenDetail)
    fireEvent.doubleClick(rowEls[0]!.querySelector('.acct-td-memo') as HTMLElement)
    expect(view.container.querySelector('input.acct-editin')).toBeTruthy()
    clickAt(rowEls[1]!)
    act(() => vi.advanceTimersByTime(500))

    clickAt(rowEls[1]!)
    act(() => vi.advanceTimersByTime(500))
    expect(onOpenDetail).toHaveBeenCalledTimes(1)
    expect(onOpenDetail.mock.calls[0]![0].id).toBe('t2')
  })

  it('a click inside the editor does not swallow anything', () => {
    const onOpenDetail = vi.fn()
    const { view, rowEls } = renderRows(onOpenDetail)
    fireEvent.doubleClick(rowEls[0]!.querySelector('.acct-td-memo') as HTMLElement)
    const input = view.container.querySelector('input.acct-editin') as HTMLInputElement
    fireEvent.pointerDown(input)
    fireEvent.click(input)
    act(() => vi.advanceTimersByTime(10))
    fireEvent.keyDown(input, { key: 'Enter' })
    act(() => vi.advanceTimersByTime(10))

    clickAt(rowEls[1]!)
    act(() => vi.advanceTimersByTime(500))
    expect(onOpenDetail).toHaveBeenCalledTimes(1)
  })
})

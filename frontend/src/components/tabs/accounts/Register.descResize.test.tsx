// @vitest-environment jsdom
//
// Description is ALWAYS the flexible minmax(floor,1fr) fill column — it holds the long bank
// strings, so it absorbs leftover width and the table always fills its container (no empty gap,
// even when the Lucius panel closes and the container widens). It has no fixed width to drag;
// instead its resize handle transfers width to/from the ADJACENT next column (Memo): dragging
// left widens the neighbour and the 1fr Description shrinks; dragging right narrows the neighbour
// and Description grows. Neighbour widths persist (localStorage); double-click resets them.
// (This replaced an earlier pin+trailing-spacer design that filled with an EMPTY gap — the exact
// bug the owner hit on closing Lucius, 2026-07-18.)

import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
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

function renderReg() {
  return render(
    <Register
      txns={[makeTxn()]}
      total={1}
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
      allEmpty={false}
      loadingTxns={false}
      isLoadingMore={false}
      payeeOptions={[]}
      payeeNames={{}}
      rates={{}}
      hiddenCols={new Set()}
    />,
  )
}

// The tracks live in `--reg-tmpl` on the register root; every row reads them from there.
const tmplOf = (c: HTMLElement) =>
  (c.querySelector('.acct-reg') as HTMLElement).style.getPropertyValue('--reg-tmpl')
// Collapse "minmax(120px, 1fr)" → a single "FILL" token so the tmpl splits into tracks by space.
const tracks = (tmpl: string) =>
  tmpl
    .replace(/minmax\([^)]*\)/g, 'FILL')
    .split(/\s+/)
    .filter(Boolean)
const fillIdx = (tmpl: string) => tracks(tmpl).indexOf('FILL')
const neighbourPx = (tmpl: string) => parseInt(tracks(tmpl)[fillIdx(tmpl) + 1] || '0', 10) // Memo track

function descHandle(c: HTMLElement): HTMLElement {
  const cells = Array.from(c.querySelectorAll('.acct-reg-head .acct-h')) as HTMLElement[]
  const cell = cells.find((el) => el.textContent?.includes('Description'))
  if (!cell) throw new Error('Description header cell not found')
  const handle = cell.querySelector('.acct-col-resize') as HTMLElement
  if (!handle) throw new Error('Description resize handle not found')
  return handle
}

function dragDesc(c: HTMLElement, dx: number) {
  const handle = descHandle(c)
  // Mouse is a Pointer Event input too; the shared resize hook captures this pointer so
  // later movement continues to reach the handle.
  fireEvent.pointerDown(handle, {
    pointerId: 1,
    pointerType: 'mouse',
    isPrimary: true,
    clientX: 500,
  })
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: 500 + dx })
  fireEvent.pointerUp(handle, { pointerId: 1 })
}

describe('Register — Description always fills; its handle resizes the neighbour', () => {
  it('renders Description as a minmax(…,1fr) fill column, with NO trailing spacer', () => {
    const { container } = renderReg()
    const tmpl = tmplOf(container)
    expect(tmpl).toMatch(/minmax\(\d+px, 1fr\)/) // Description fills
    expect(tmpl).not.toContain('minmax(0, 1fr)') // never an empty trailing spacer
    expect(fillIdx(tmpl)).toBeGreaterThan(-1)
  })

  it('dragging the Description handle LEFT widens the neighbour (Description shrinks) — still fill, no spacer', () => {
    const { container } = renderReg()
    const before = neighbourPx(tmplOf(container))
    dragDesc(container, -40) // drag left
    const tmpl = tmplOf(container)
    expect(neighbourPx(tmpl)).toBe(before + 40) // Memo widened by 40; the 1fr Description absorbs it
    expect(tmpl).toMatch(/minmax\(\d+px, 1fr\)/) // Description is STILL the fill column
    expect(tmpl).not.toContain('minmax(0, 1fr)') // and NO empty spacer ever appears
  })

  it('dragging the Description handle RIGHT narrows the neighbour (Description grows)', () => {
    const { container } = renderReg()
    const before = neighbourPx(tmplOf(container))
    dragDesc(container, 20) // drag right (stays above the neighbour's min)
    expect(neighbourPx(tmplOf(container))).toBe(before - 20)
  })

  it('persists the neighbour width across a remount (localStorage), Description still filling', () => {
    const first = renderReg()
    const before = neighbourPx(tmplOf(first.container))
    dragDesc(first.container, -60)
    expect(neighbourPx(tmplOf(first.container))).toBe(before + 60)
    first.unmount()

    const { container } = renderReg()
    expect(neighbourPx(tmplOf(container))).toBe(before + 60) // persisted
    expect(tmplOf(container)).toMatch(/minmax\(\d+px, 1fr\)/) // Description still fills
  })

  it('double-clicking the handle resets neighbour widths to default; Description keeps filling', () => {
    const { container } = renderReg()
    const before = neighbourPx(tmplOf(container))
    dragDesc(container, -60)
    expect(neighbourPx(tmplOf(container))).toBe(before + 60)

    fireEvent.doubleClick(descHandle(container))
    expect(neighbourPx(tmplOf(container))).toBe(before) // back to default
    expect(tmplOf(container)).toMatch(/minmax\(\d+px, 1fr\)/)
    expect(tmplOf(container)).not.toContain('minmax(0, 1fr)')
  })
})

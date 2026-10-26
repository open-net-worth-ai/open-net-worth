// @vitest-environment jsdom
//
// Reconcile asserts a figure against the CLEARED balance (the backend diffs `real_balance`
// against `cleared_balance` by default). The popover used to ask about, and send, the WORKING
// balance (cleared + uncleared), so "Yes, it matches" on an account with a pending charge
// booked that charge as a "Balance adjustment" transaction.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AccountHeader } from './AccountHeader'
import { fromApiAccount } from './data'
import type { ApiAccount } from './types'

const ACCOUNT = fromApiAccount({
  id: 'acc-cc',
  name: 'Card',
  kind: 'liability',
  type: 'credit',
  currency: 'SGD',
  balance: -6370.5,
  balance_base: -6370.5,
  cleared_balance: -6276.57,
  cleared_balance_base: -6276.57,
} as ApiAccount)

const noop = () => {}

function renderHeader(onReconcile: (a: unknown, real: number) => void) {
  render(
    <AccountHeader
      account={ACCOUNT}
      dueInfo={null}
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
      onReconcile={onReconcile}
      onReview={noop}
      onStartReview={noop}
    />,
  )
}

afterEach(cleanup)

function openReconcile() {
  const [trigger] = screen.getAllByText('Reconcile')
  if (!trigger) throw new Error('Reconcile trigger not rendered')
  fireEvent.click(trigger)
}

describe('Reconcile popover', () => {
  it('asks about, and confirms, the cleared balance — never the working one', () => {
    const onReconcile = vi.fn()
    renderHeader(onReconcile)
    openReconcile()

    expect(screen.getByRole('dialog').textContent).toContain('6,276.57')
    expect(screen.getByRole('dialog').textContent).not.toContain('6,370.50')

    fireEvent.click(screen.getByText('Yes, it matches'))
    expect(onReconcile).toHaveBeenCalledWith(ACCOUNT, -6276.57)
  })

  it('previews the adjustment against the cleared balance', () => {
    renderHeader(vi.fn())
    openReconcile()
    fireEvent.click(screen.getByText('No'))
    fireEvent.change(screen.getByPlaceholderText('actual'), { target: { value: '-6276.57' } })

    expect(screen.getByText('In sync')).toBeTruthy()
  })

  // QA 2026-10-25: reconciling from the ⋯ menu (the header's only route when the Lucius panel
  // is open) left the menu standing over the register after the toast, and Escape didn't shut it.
  it('closes the ⋯ menu once the reconcile from it is done', () => {
    const setMenuOpen = vi.fn()
    render(
      <AccountHeader
        account={ACCOUNT}
        dueInfo={null}
        stats={null}
        menuOpen
        setMenuOpen={setMenuOpen}
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
        onReconcile={vi.fn()}
        onReview={noop}
        onStartReview={noop}
      />,
    )
    const menuItem = screen
      .getAllByText('Reconcile')
      .find((el) => el.closest('.acct-pop-wrap--menu'))
    if (!menuItem) throw new Error('menu Reconcile item not rendered')
    fireEvent.click(menuItem)
    fireEvent.click(screen.getByText('Yes, it matches'))
    expect(setMenuOpen).toHaveBeenCalledWith(false)
  })

  it('Escape closes the ⋯ menu', () => {
    const setMenuOpen = vi.fn()
    render(
      <AccountHeader
        account={ACCOUNT}
        dueInfo={null}
        stats={null}
        menuOpen
        setMenuOpen={setMenuOpen}
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
        onReconcile={vi.fn()}
        onReview={noop}
        onStartReview={noop}
      />,
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(setMenuOpen).toHaveBeenCalledWith(false)
  })
})

describe('reconcile line — monthly nudge', () => {
  function renderWith(lastReconciled: string) {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 9, 26, 12))
    const account = { ...ACCOUNT, lastReconciled }
    render(
      <AccountHeader
        account={account}
        dueInfo={null}
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
    vi.useRealTimers()
  }

  it('warns with the age once the last reconcile is over a month old', () => {
    renderWith('2026-08-09')
    expect(screen.getByText(/78 days ago/)).toBeTruthy()
  })

  it('stays a plain check inside the month', () => {
    renderWith('2026-10-20')
    expect(screen.queryByText(/days ago/)).toBeNull()
    expect(screen.getByText(/Reconciled/)).toBeTruthy()
  })
})

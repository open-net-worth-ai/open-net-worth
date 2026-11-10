// @vitest-environment jsdom
//
// The "All accounts" balance card was redesigned (owner: "this is not helpful at all" — a
// single Cleared+Uncleared=Working figure across EVERY account type, including the mortgage,
// was almost always a large, uninterpretable negative). It now totals CASH accounts only
// (checking/savings/e-wallet), so the figure means something a person can act on, and the
// "Cash on hand" cell is the literal sum of the two beside it — never a same-valued-but-
// differently-named field (`cashBalance.total`), for the same reason as the original
// cleared+uncleared=working regression: a field that's supposed to equal the sum, read
// instead of writing the sum, is how that regression happened in the first place.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import {
  Overview,
  type OverviewCashBalance,
  type OverviewStats,
  type OverviewTotals,
} from './Overview'

afterEach(() => cleanup())

/** `.acct-bal-lbl` text, trimmed to just the label (drops the info-popover "i" trigger's own
 *  text) — sidesteps text-matcher ambiguity ("Uncleared" contains "Cleared" as a substring;
 *  "Cash on hand" contains "Cash"), which a loose `getByText(..., {exact:false})` falls into. */
function balanceLabels(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('.acct-bal-lbl')).map(
    (el) => el.childNodes[0]?.textContent?.trim() ?? '',
  )
}

const totals: OverviewTotals = {
  cash: 0,
  fixed_deposit: 0,
  investment: 0,
  retirement: 0,
  receivable: 0,
  credit: 0,
  loan: 0,
  liabilities: 0,
}
const stats: OverviewStats = { count: 0, review: 0 }
const noop = vi.fn()

function renderOverview(cashBalance: OverviewCashBalance) {
  return render(
    <Overview
      totals={totals}
      groups={[]}
      cashBalance={cashBalance}
      stats={stats}
      baseCurrency="SGD"
      collapsed={false}
      onReopen={noop}
      onAddTxn={noop}
      onAdd={noop}
      onManagePayees={noop}
      onManageCategories={noop}
      onImport={noop}
      onExport={noop}
      onReview={noop}
      onStartReview={noop}
      onShowUncleared={noop}
    />,
  )
}

describe('Overview — the balance card totals cash accounts only, never the whole book', () => {
  it('renders Cash on hand as the sum of cleared and uncleared, not the unrelated `total` field', () => {
    // `total` stands in for what the OLD all-account figure would have been (mortgage and
    // all) — cleared+uncleared (cash accounts only) is far smaller and, unlike before, is
    // usually positive.
    renderOverview({ cleared: 25000, uncleared: -500, total: -358116.24 })

    expect(screen.getByText('S$24,500.00')).toBeTruthy()
    expect(screen.queryByText('S$358,116.24')).toBeNull()
    expect(screen.queryByText('-S$358,116.24')).toBeNull()
  })

  it('labels the terms a user might not recognize, with one explanation of all three', () => {
    const { container } = renderOverview({ cleared: 25000, uncleared: -500, total: 24500 })

    expect(balanceLabels(container)).toEqual(['Cleared', 'Uncleared', 'Cash on hand'])
    // One "i", beside the title — not one under each 16px label (owner, 2026-10-26).
    const info = screen.getByRole('button', { name: 'What these figures mean' })
    expect(container.querySelector('.acct-bal-k')?.contains(info)).toBe(true)
    expect(container.querySelector('.acct-bal-lbl .info-pop-btn')).toBeNull()
    fireEvent.click(info)
    const tip = screen.getByRole('tooltip').textContent ?? ''
    expect(tip).toContain('Cleared')
    expect(tip).toContain('Uncleared')
    expect(tip).toContain('Cash on hand')
  })

  it('no longer mentions "Working balance" or the unscoped "Balance" heading', () => {
    const { container } = renderOverview({ cleared: 0, uncleared: 0, total: 0 })
    expect(balanceLabels(container)).not.toContain('Working balance')
    expect(container.querySelector('.acct-bal-k')?.childNodes[0]?.textContent?.trim()).toBe('Cash')
  })

  it('offers archived accounts from the page actions menu', () => {
    renderOverview({ cleared: 0, uncleared: 0, total: 0 })

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))

    expect(screen.getByRole('button', { name: /Archived accounts/i })).toBeTruthy()
  })
})

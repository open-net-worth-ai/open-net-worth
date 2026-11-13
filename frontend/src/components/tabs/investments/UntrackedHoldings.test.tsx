// @vitest-environment jsdom
// Untracked holdings (flag allow_brokerage_total_valuation, ADR 2026-11-10 amendment 2): a Brokerage
// total account's holdings not entered. The server leaves them out of the book and says so in
// `untracked_excluded`; these surfaces say it to the user. Synthetic figures only.

import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { Allocation } from './Allocation'
import { EmptyHoldings } from './EmptyHoldings'
import { Rebalance } from './Rebalance'
import { Risk } from './Risk'
import type { DerivedBook } from './useInvestmentsBook'
import type { UntrackedExcluded } from '../../../types/aggregates'
import { fBase } from '../../../utils/format'
import { ASSET_CLASS_LABEL, ASSET_CLASS_ORDER } from '../../../utils/constants'

afterEach(cleanup)

const U: UntrackedExcluded = {
  total: 4980,
  pct_of_invested: 62.25,
  accounts: [{ account_id: 'acc_1', name: 'Old 401(k)', type: 'retirement', value: 4980 }],
}

describe('Untracked holdings — the band', () => {
  it('is labelled and ordered with the invested bands', () => {
    expect(ASSET_CLASS_LABEL.untracked_holdings).toBe('Untracked holdings')
    expect(ASSET_CLASS_ORDER.indexOf('untracked_holdings')).toBeLessThan(
      ASSET_CLASS_ORDER.indexOf('cash'),
    )
  })
})

describe('Untracked holdings — Investments', () => {
  it('Allocation shows a "Not broken down" slice as a share of everything invested', () => {
    render(
      <Allocation
        d={{ thresholdPp: 5, untracked: U } as unknown as DerivedBook}
        alloc={[{ cls: 'equity', cur: 100, tgt: 0 }]}
      />,
    )
    const slice = screen.getByTestId('untracked-slice')
    expect(slice.textContent).toContain('Not broken down')
    expect(slice.textContent).toContain('62%')
    expect(slice.textContent).toContain(fBase(4980))
    expect(slice.textContent).toContain('Old 401(k)')
  })

  it('Allocation draws no slice when nothing is untracked (flag off)', () => {
    render(
      <Allocation
        d={{ thresholdPp: 5, untracked: null } as unknown as DerivedBook}
        alloc={[{ cls: 'equity', cur: 100, tgt: 0 }]}
      />,
    )
    expect(screen.queryByTestId('untracked-slice')).toBeNull()
  })

  it('Rebalance says what it left out and how to bring it in', () => {
    render(
      <Rebalance
        thresholdPp={5}
        untracked={U}
        alloc={[{ cls: 'equity', cur: 100, tgt: 60, driftPp: 40, tradeBase: -1208 }]}
      />,
    )
    expect(screen.getByTestId('untracked-note').textContent).toBe(
      `${fBase(4980)} at Old 401(k) isn't broken into holdings — add holdings to rebalance it.`,
    )
  })

  it('Rebalance says it even before targets are set', () => {
    render(
      <Rebalance
        thresholdPp={5}
        untracked={U}
        alloc={[{ cls: 'equity', cur: 100, tgt: 0, driftPp: 0, tradeBase: 0 }]}
      />,
    )
    expect(screen.getByTestId('untracked-note')).toBeTruthy()
  })

  it('Rebalance and Risk stay as they were without it', () => {
    render(
      <>
        <Rebalance
          thresholdPp={5}
          alloc={[{ cls: 'equity', cur: 100, tgt: 0, driftPp: 0, tradeBase: 0 }]}
        />
        <Risk risk={[{ nm: 'ACME', pct: 100, val: 3020, hot: true }]} grand={3020} />
      </>,
    )
    expect(screen.queryByTestId('untracked-note')).toBeNull()
  })

  it('Risk says concentration leaves it out', () => {
    render(
      <Risk risk={[{ nm: 'ACME', pct: 100, val: 3020, hot: true }]} grand={3020} untracked={U} />,
    )
    expect(screen.getByTestId('untracked-note').textContent).toContain('Old 401(k)')
  })

  it('the empty state counts a total with no holdings as invested, not as nothing', () => {
    render(<EmptyHoldings untracked={{ ...U, pct_of_invested: 100 }} />)
    expect(screen.getByText('No holdings entered yet')).toBeTruthy()
    expect(screen.getByText(/is counted as\s+invested/)).toBeTruthy()
  })
})

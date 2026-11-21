// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ACTUAL_APP } from './budgetApps'
import { ToBudgetCompare } from './ToBudgetCompare'

afterEach(cleanup)

describe("Ready to Assign beside Actual's To Budget", () => {
  it('shows both, the gap, and the part card payment envelopes hold', () => {
    render(
      <ToBudgetCompare
        app={ACTUAL_APP}
        figures={{
          month: '2026-11',
          theirs: 1000,
          here: 900,
          difference: -100,
          card_payment_envelopes: 75,
        }}
      />,
    )
    expect(screen.getByText('Here: 900.00 · Actual: 1,000.00')).toBeTruthy()
    expect(
      screen.getByText(/They differ by 100.00\. Actual has no card payment envelopes/),
    ).toBeTruthy()
    expect(screen.getByText(/\(75\.00\)/)).toBeTruthy()
    expect(screen.getByText(/The other 25.00 is how the two treat overspending/)).toBeTruthy()
  })

  it('says when they agree, and shows nothing without figures', () => {
    const { container, rerender } = render(<ToBudgetCompare app={ACTUAL_APP} figures={null} />)
    expect(container.textContent).toBe('')
    rerender(
      <ToBudgetCompare
        app={ACTUAL_APP}
        figures={{
          month: '2026-11',
          theirs: 500,
          here: 500,
          difference: 0,
          card_payment_envelopes: 0,
        }}
      />,
    )
    expect(screen.getByText('They agree.')).toBeTruthy()
  })
})

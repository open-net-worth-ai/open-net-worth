// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { PositionVerdictLine } from './PositionVerdictLine'

describe('PositionVerdictLine', () => {
  afterEach(cleanup)

  it('shows the call, why, and each level with its distance and where it came from', () => {
    render(
      <PositionVerdictLine
        priceMaxFrac={2}
        verdict={{
          action: 'hold',
          why: 'A close below the stop would mean earnings, not mood.',
          currency: 'USD',
          levels: [
            { kind: 'stop', price: 112, source: 'thesis', distance_pct: -18.2 },
            { kind: 'trim', price: 160, source: 'lucius', distance_pct: 16.8 },
          ],
        }}
      />,
    )
    expect(screen.getByText('Hold')).toBeTruthy()
    expect(screen.getByText('A close below the stop would mean earnings, not mood.')).toBeTruthy()
    const [stop, trim] = screen.getAllByRole('listitem')
    expect(stop.textContent).toMatch(/^Stop \$112(\.00)? · 18\.2% below · your thesis$/)
    expect(trim.textContent).toMatch(/^Trim \$160(\.00)? · 16\.8% above · Lucius suggests$/)
  })

  it('shows the call alone when there are no levels', () => {
    render(
      <PositionVerdictLine
        verdict={{ action: 'wait', why: 'Not in the buy zone yet.', currency: 'USD', levels: [] }}
      />,
    )
    expect(screen.getByText('Wait')).toBeTruthy()
    expect(screen.queryByRole('list')).toBeNull()
  })
})

// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Risk } from './Risk'
import { splitLine } from './riskSplit'

describe('Risk card — fund look-through split', () => {
  it('says how much is held directly and inside which funds', () => {
    expect(
      splitLine({
        directPct: 19.57,
        funds: [
          { symbol: 'ACMI', pct: 3.63 },
          { symbol: 'ACMQ', pct: 2.61 },
        ],
      }),
    ).toBe('19.6% direct · 3.6% via ACMI · 2.6% via ACMQ')
  })

  it('a name held only through funds says so', () => {
    expect(
      splitLine({
        directPct: 0,
        funds: [
          { symbol: 'ACMI', pct: 3.68 },
          { symbol: 'ACMQ', pct: 2.66 },
        ],
      }),
    ).toBe('all via funds: 3.7% ACMI · 2.7% ACMQ')
  })

  it('shows the split under the bar, and nothing for a plain name', () => {
    render(
      <Risk
        grand={10000}
        risk={[
          {
            nm: 'ACMT',
            pct: 26,
            val: 2600,
            hot: true,
            split: { directPct: 20, funds: [{ symbol: 'ACMI', pct: 6 }] },
          },
          { nm: 'ACMB', pct: 5, val: 500, hot: false },
        ]}
      />,
    )
    expect(screen.getByText('20.0% direct · 6.0% via ACMI')).toBeTruthy()
    expect(screen.getByText('ACMT ⊕')).toBeTruthy()
    expect(screen.getByText('ACMB')).toBeTruthy()
    expect(document.querySelectorAll('.mk-risk-split')).toHaveLength(1)
  })
})

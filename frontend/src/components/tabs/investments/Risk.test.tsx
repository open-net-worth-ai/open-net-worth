// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Risk } from './Risk'
import { splitDetail, splitLine } from './riskSplit'

afterEach(cleanup)

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

  const MANY = {
    directPct: 19.6,
    funds: [
      { symbol: 'ACMI', pct: 3.6 },
      { symbol: 'ACMQ', pct: 2.6 },
      { symbol: 'ACMW', pct: 2.1 },
      { symbol: 'ACMV', pct: 1.8 },
      { symbol: 'ACMS', pct: 1.3 },
    ],
  }

  it('names the two largest funds and folds the rest into "+N more"', () => {
    expect(splitLine(MANY)).toBe('19.6% direct · 3.6% via ACMI · 2.6% via ACMQ · +3 more 5.2%')
    expect(splitLine({ ...MANY, directPct: 0 })).toBe(
      'all via funds: 3.6% ACMI · 2.6% ACMQ · +3 more 5.2%',
    )
  })

  it('the hover lists every fund in the design-system tooltip', () => {
    const detail = splitDetail(MANY)
    expect(detail.split('\n')).toEqual([
      '19.6% held directly',
      '3.6% inside ACMI',
      '2.6% inside ACMQ',
      '2.1% inside ACMW',
      '1.8% inside ACMV',
      '1.3% inside ACMS',
      "Counted from each fund's top 10 holdings — at least this much.",
    ])
    vi.useFakeTimers()
    try {
      render(
        <Risk grand={10000} risk={[{ nm: 'ACMT', pct: 31, val: 3100, hot: true, split: MANY }]} />,
      )
      // both the name and the split line open it
      expect(document.querySelectorAll('.mk-risk-row .tip-anchor')).toHaveLength(2)
      fireEvent.mouseEnter(screen.getByText('ACMT ⊕').parentElement!)
      act(() => {
        vi.advanceTimersByTime(1000)
      })
      const tip = screen.getByRole('tooltip')
      expect(tip.className).toContain('tip-bubble--multiline')
      expect(tip.textContent).toContain('1.3% inside ACMS')
    } finally {
      vi.useRealTimers()
    }
  })
})

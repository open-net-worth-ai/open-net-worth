// @vitest-environment jsdom

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BandRow } from './planData'
import { FanChart } from './charts'

const band = (age: number, p10: number, p50: number, p90: number): BandRow => ({
  year: 2030 + age,
  age,
  p10,
  p25: p10,
  p50,
  p75: p90,
  p90,
})

afterEach(cleanup)

const topPx = (el: Element) => parseFloat((el as HTMLElement).style.top)

describe('FanChart end-of-line label vs y-axis ticks', () => {
  // Default height 210, padT 14, padB 22 → ticks at y = 57.5, 101, 144.5 for maxY*(3/4, 1/2, 1/4).
  // p90 peaks at 100 → maxY = 104, so a final p50 of 26 (= maxY/4) lands exactly on the 144.5 tick.
  it('drops the tick the end label sits on, keeping the others', () => {
    const { getByTestId, getAllByTestId } = render(
      <FanChart bands={[band(40, 5, 10, 50), band(60, 10, 26, 100)]} />,
    )
    const endY = topPx(getByTestId('fan-end-label'))
    const ticks = getAllByTestId('fan-y-tick').map(topPx)

    expect(endY).toBeCloseTo(144.5)
    expect(ticks).toEqual([57.5, 101])
  })

  it('keeps every tick when the end label is clear of all of them', () => {
    // final p50 of 39 → y ≈ 122.75, more than 14px from both the 101 and 144.5 ticks
    const { getAllByTestId } = render(
      <FanChart bands={[band(40, 5, 10, 50), band(60, 10, 39, 100)]} />,
    )

    expect(getAllByTestId('fan-y-tick').map(topPx)).toEqual([57.5, 101, 144.5])
  })

  it('uses the rendered label and tick heights, not a fixed gap', () => {
    // A taller label (text zoom, a bigger font): 30px label + 10px tick → clear = 20 + 2 = 22px,
    // so the end label at y ≈ 122.75 now collides with BOTH the 101 and 144.5 rows (21.75px away).
    const rect = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(function (this: HTMLElement) {
        const h = { 'fan-end-label': 30, 'fan-y-tick': 10 }[this.dataset.testid ?? ''] ?? 0
        return { height: h, width: 0, top: 0, left: 0, right: 0, bottom: h } as DOMRect
      })
    try {
      const { getAllByTestId } = render(
        <FanChart bands={[band(40, 5, 10, 50), band(60, 10, 39, 100)]} />,
      )

      expect(getAllByTestId('fan-y-tick').map(topPx)).toEqual([57.5])
    } finally {
      rect.mockRestore()
    }
  })
})

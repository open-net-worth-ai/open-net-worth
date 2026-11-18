// @vitest-environment jsdom
//
// Regression tests for the privacy-mode canvas mask. Privacy mode is a CSS
// convention (body.hide-amounts overlays `***` on DOM `.money` nodes) — but
// Chart.js draws ticks/tooltips as canvas PIXELS, which CSS cannot reach, and
// OvChart only rebuilds on `sig` changes, so a toggle never repainted the
// axis. The seam fix: kTick()/moneyTip() read the body class live at draw
// time, and OvChart watches the class with a MutationObserver to force the
// redraw. These tests pin all three behaviours.

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fBase } from '../../../utils/format'

// OvChart constructs real Chart.js charts against a canvas jsdom can't
// rasterize — mock the constructor and capture instances so the redraw test
// can assert on update() calls. `Chart.defaults` stays undefined so the
// module-scope defaults block is skipped by its own guard.
const instances = vi.hoisted(
  () =>
    [] as {
      update: ReturnType<typeof vi.fn>
      resize: ReturnType<typeof vi.fn>
      destroy: ReturnType<typeof vi.fn>
      active: unknown[]
      setActiveElements: ReturnType<typeof vi.fn>
    }[],
)

vi.mock('chart.js', () => {
  class FakeChart {
    static register = vi.fn()
    static instances = {}
    update = vi.fn()
    resize = vi.fn()
    destroy = vi.fn()
    active: unknown[] = []
    getActiveElements = () => this.active
    setActiveElements = vi.fn((els: unknown[]) => {
      this.active = els
    })
    tooltip = { setActiveElements: vi.fn() }
    constructor() {
      instances.push(this)
    }
  }
  return {
    Chart: FakeChart,
    ArcElement: {},
    DoughnutController: {},
    BarElement: {},
    BarController: {},
    LineElement: {},
    LineController: {},
    PointElement: {},
    CategoryScale: {},
    LinearScale: {},
    Tooltip: {},
    Legend: {},
    Filler: {},
  }
})

import { OvChart, kTick, moneyTip } from './charts'

afterEach(() => {
  cleanup()
  document.body.classList.remove('hide-amounts')
  instances.length = 0
})

describe('kTick — privacy mask on canvas axis ticks', () => {
  it('formats compact currency when amounts are visible', () => {
    document.body.classList.remove('hide-amounts')
    const out = kTick(98000)
    expect(out).not.toBe('***')
    expect(out).toMatch(/\d/)
  })

  it("masks every tick to '***' under body.hide-amounts — the canvas can't inherit the CSS .money mask", () => {
    document.body.classList.add('hide-amounts')
    expect(kTick(98000)).toBe('***')
    expect(kTick(2500)).toBe('***')
    expect(kTick(0)).toBe('***')
  })
})

describe('moneyTip — privacy mask on tooltip values', () => {
  it('returns the exact fBase figure when amounts are visible', () => {
    document.body.classList.remove('hide-amounts')
    expect(moneyTip(98234.56)).toBe(fBase(98234.56))
  })

  it('masks the full-precision hover value under body.hide-amounts — the tooltip leaks more than a tick', () => {
    document.body.classList.add('hide-amounts')
    expect(moneyTip(98234.56)).toBe('***')
  })
})

describe('OvChart — privacy toggle forces a redraw', () => {
  it('calls chart.update() when body.hide-amounts flips while sig is unchanged', async () => {
    render(
      <OvChart config={{ type: 'line', data: { labels: [], datasets: [] } } as never} sig="a" />,
    )
    await waitFor(() => expect(instances.length).toBe(1))
    const chart = instances[0]!
    expect(chart.update).not.toHaveBeenCalled()

    document.body.classList.add('hide-amounts')
    await waitFor(() => expect(chart.update).toHaveBeenCalled())

    document.body.classList.remove('hide-amounts')
    await waitFor(() => expect(chart.update).toHaveBeenCalledTimes(2))
  })
})

// On a phone a tapped tooltip covered most of the chart and nothing obvious dismissed it.
describe('OvChart — a touch tooltip can be dismissed', () => {
  async function chartWithTooltip() {
    render(
      <OvChart config={{ type: 'line', data: { labels: [], datasets: [] } } as never} sig="t" />,
    )
    await waitFor(() => expect(instances.length).toBe(1))
    const chart = instances[0]!
    chart.active = [{ datasetIndex: 0, index: 0 }]
    return chart
  }

  it('hides on a tap outside the chart', async () => {
    const chart = await chartWithTooltip()
    fireEvent.pointerDown(document.body)
    expect(chart.setActiveElements).toHaveBeenCalledWith([])
    expect(chart.active).toEqual([])
  })

  it('hides when the page scrolls', async () => {
    const chart = await chartWithTooltip()
    fireEvent.scroll(window)
    expect(chart.active).toEqual([])
  })

  it('stays while the finger is on the chart, then hides a moment after it lifts', async () => {
    const chart = await chartWithTooltip()
    vi.useFakeTimers()
    try {
      const canvas = document.querySelector('canvas')!
      fireEvent.pointerDown(canvas)
      expect(chart.active).toHaveLength(1)
      fireEvent.pointerUp(canvas, { pointerType: 'touch' })
      expect(chart.active).toHaveLength(1)
      vi.advanceTimersByTime(3000)
      expect(chart.active).toEqual([])
    } finally {
      vi.useRealTimers()
    }
  })
})

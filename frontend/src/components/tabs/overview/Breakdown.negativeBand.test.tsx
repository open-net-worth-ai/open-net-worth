// @vitest-environment jsdom
//
// Regression for MATH-13: the `alloc` widget's liability row carries a
// negative share-of-total (backend `_pct(value, total)` on a raw-negative
// liability value against the total-assets denominator). `BreakdownBar` fed
// that straight into `style.width`, which CSS rejects — the liability
// segment vanished and the remaining segments visually exceeded 100% of the
// bar. The fix clamps a segment's rendered width to zero instead of letting
// a negative percent leak into the DOM.

import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BreakdownBody } from './Breakdown'
import type { BreakdownRow } from './Breakdown'

// OvChart constructs a real Chart.js chart against a canvas jsdom can't
// rasterize (see charts.test.tsx) — mock the constructor and capture the
// config it was built with so the tests can invoke the tooltip callbacks.
const configs = vi.hoisted(() => [] as { options?: Record<string, unknown> }[])

vi.mock('chart.js', () => {
  class FakeChart {
    static register = vi.fn()
    static instances = {}
    update = vi.fn()
    resize = vi.fn()
    destroy = vi.fn()
    constructor(_el: unknown, config: { options?: Record<string, unknown> }) {
      configs.push(config)
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

afterEach(() => {
  cleanup()
  configs.length = 0
})

describe('BreakdownBar', () => {
  it('renders a negative-value segment as a zero-width band, never a negative CSS width', () => {
    // total = total assets (equity + cash); the liability is shown against
    // that same denominator, per the alloc widget's actual shape.
    const rows: BreakdownRow[] = [
      { key: 'equity', label: 'Equity', value: 60, color: '#111' },
      { key: 'cash', label: 'Cash', value: 60, color: '#222' },
      { key: 'liability', label: 'Liability', value: -20, color: '#333' },
    ]

    const { container } = render(<BreakdownBody rows={rows} total={120} viz="list" />)

    const segments = Array.from(container.querySelectorAll('.ov-breakdown-bar i'))
    expect(segments).toHaveLength(3)

    const widths = segments.map((seg) => (seg as HTMLElement).style.width)
    for (const width of widths) {
      expect(width.startsWith('-')).toBe(false)
    }

    const liabilitySeg = segments[2] as HTMLElement
    expect(liabilitySeg.style.width).toBe('0%')

    const equitySeg = segments[0] as HTMLElement
    const cashSeg = segments[1] as HTMLElement
    expect(equitySeg.style.width).toBe('50%')
    expect(cashSeg.style.width).toBe('50%')

    const total = widths.reduce((sum, w) => sum + parseFloat(w), 0)
    expect(total).toBeLessThanOrEqual(100)
  })
})

describe('Breakdown chart tooltips', () => {
  // Regression: tooltipLine() used to recompute value/total*100 itself,
  // ignoring the server's own `row.pct` (ADR
  // 2026-07-31-overview-aggregates-are-server-computed). Give a row a `pct`
  // that deliberately disagrees with the local division and assert the
  // tooltip prints the server's figure.
  const rows: [BreakdownRow, BreakdownRow] = [
    { key: 'a', label: 'A', value: 50, color: '#111', pct: 90 },
    { key: 'b', label: 'B', value: 50, color: '#222', pct: 10 },
  ]

  it('bar tooltip prints the server pct, not value/total*100', () => {
    render(<BreakdownBody rows={rows} total={100} viz="bar" />)

    const config = configs[configs.length - 1]
    expect(config).toBeTruthy()
    const plugins = config!.options?.plugins as
      | { tooltip?: { callbacks?: { label?: (ctx: { dataIndex: number }) => string } } }
      | undefined
    const label = plugins?.tooltip?.callbacks?.label
    expect(label).toBeTruthy()

    // Local division would say 50%; the row's own pct says 90%.
    expect(label!({ dataIndex: 0 })).toContain('90.00%')
    expect(label!({ dataIndex: 0 })).not.toContain('50.00%')
  })

  it('portals the full donut tooltip above the chart and dismisses it on mouseout', async () => {
    const label = 'A merchant with a name wider than the donut chart'
    const { container } = render(
      <BreakdownBody rows={[rows[0], { ...rows[1], label }]} total={100} viz="pie" />,
    )
    const plugins = configs.at(-1)!.options?.plugins as {
      tooltip: {
        enabled: boolean
        external: (context: {
          chart: { canvas: HTMLCanvasElement }
          tooltip: {
            opacity: number
            caretX: number
            caretY: number
            dataPoints: { dataIndex: number }[]
          }
        }) => void
      }
    }
    const { tooltip } = plugins
    expect(tooltip.enabled).toBe(false)
    const chart = { canvas: container.querySelector('canvas')! }
    const point = { caretX: 100, caretY: 50, dataPoints: [{ dataIndex: 1 }] }
    act(() => tooltip.external({ chart, tooltip: { ...point, opacity: 1 } }))

    const popup = screen.getByRole('tooltip')
    expect(popup.parentElement).toBe(document.body)
    expect(popup.textContent).toContain(label)
    expect(popup.textContent).toContain('10.00%')
    expect(popup.textContent).not.toContain('50.00%')

    try {
      await act(async () => document.body.classList.add('hide-amounts'))
      expect(popup.textContent).toBe(label + ': *** (***)')
    } finally {
      await act(async () => document.body.classList.remove('hide-amounts'))
    }

    act(() => tooltip.external({ chart, tooltip: { ...point, opacity: 0 } }))
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})

// @vitest-environment jsdom
//
// The net-worth chart's range is picked on the card itself (issue #222): the header menu
// offers every range the server precomputes, a pick is saved to the widget's own `range`
// setting, and a range longer than the history says where the history starts.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))
// jsdom has no canvas: the chart itself is not what these tests are about.
vi.mock('../charts', async (orig) => ({
  ...(await orig<typeof import('../charts')>()),
  OvChart: () => <div data-testid="ov-chart" />,
}))

import { netWorthWidgetComponents } from './netWorth'
import { NET_WORTH_RANGES } from './netWorthRanges'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

const point = (date: string, nw: number) => ({
  date,
  net_worth: nw,
  accounts: nw,
  liabilities: 0,
  classes: { cash: nw },
  market_tracked: false,
})

const HISTORY = [point('2025-03-31', 1000), point('2026-10-31', 2500), point('2026-11-24', 3000)]
const range = (from: string, delta: number, span: number, clamped = false) => ({
  delta,
  pct: 10,
  saved: delta,
  market: 0,
  span_months: span,
  from_date: from,
  to_date: '2026-11-24',
  clamped,
})

function serve() {
  authFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/net-worth/stats')) {
      return jsonOk({
        base_currency: 'SGD',
        net_worth: 3000,
        history: HISTORY,
        ranges: {
          '3M': range('2026-10-31', 500, 1),
          '5Y': range('2025-03-31', 2000, 20, true),
        },
        comparison: null,
        month_to_date: null,
        composition: { classes: ['cash'], has_liabilities: false },
        latest: HISTORY[2],
      })
    }
    if (url.startsWith('/api/net-worth')) return jsonOk({ base_currency: 'SGD', net_worth: 3000 })
    return jsonOk({})
  })
}

function renderChart(defId: 'hero' | 'networth-chart', config: Record<string, unknown>) {
  const Comp = netWorthWidgetComponents[defId]!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === defId)!
  const instance: WidgetInstance = {
    instanceId: `w-${defId}`,
    defId,
    x: 0,
    y: 0,
    w: 8,
    h: 300,
    config,
  }
  const onPatch = vi.fn()
  render(<Comp instance={instance} def={def} onPatch={onPatch} onNavigate={vi.fn()} />)
  return onPatch
}

describe('net-worth chart range', () => {
  it('offers every precomputed range in the widget settings and on the card', () => {
    for (const defId of ['hero', 'networth-chart']) {
      const def = WIDGET_DEFINITIONS.find((d) => d.defId === defId)!
      const setting = def.settings!.find((s) => s.key === 'range')!
      expect(setting.options).toEqual([...NET_WORTH_RANGES])
      expect(setting.default).toBe('3M')
    }
    serve()
    renderChart('networth-chart', { range: '1Y' })
    fireEvent.click(screen.getByRole('button', { name: 'Range' }))
    const items = screen.getAllByRole('menuitemradio').map((el) => el.textContent)
    expect(items).toEqual([...NET_WORTH_RANGES])
  })

  it('saves a pick to the widget’s own range setting', () => {
    serve()
    const onPatch = renderChart('hero', { range: '3M', chart: 'Trend' })
    fireEvent.click(screen.getByRole('button', { name: 'Range' }))
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'YTD' }))
    expect(onPatch).toHaveBeenCalledWith({ config: { range: 'YTD', chart: 'Trend' } })
  })

  it('says where the history starts when the range is longer than it', async () => {
    serve()
    renderChart('hero', { range: '5Y' })
    await waitFor(() =>
      expect(document.querySelector('.ov-hero-attrib')?.textContent).toMatch(
        /since Mar 2025, when your history starts/,
      ),
    )
  })

  it('names the span and the amount for an ordinary range', async () => {
    serve()
    renderChart('hero', { range: '3M' })
    await waitFor(() =>
      expect(document.querySelector('.ov-hero-attrib')?.textContent).toMatch(
        /^\+\D*500(\.00)? over 1 month$/,
      ),
    )
  })

  it('has no range to pick in composition mode', () => {
    serve()
    renderChart('hero', { range: '3M', chart: 'Composition' })
    expect(screen.queryByRole('button', { name: 'Range' })).toBeNull()
  })
})

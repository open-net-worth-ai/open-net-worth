// @vitest-environment jsdom
//
// `dashboard_server_panels`: on, a panel widget asks `/api/query` for its `panel_*` source and
// draws the server's rows, totals and verdicts as given; off, the registry hands out the old
// component, which reads its old endpoint.

import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

import { WIDGET_COMPONENTS } from '../registry'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'
import { overrideFlagsForTests } from '../../../../hooks/useFlag'
import { clearQueryCache } from '../../../../state/queryCache'
import { setBaseCurrency } from '../../../../utils/format'
import type { CrossoverPanel, SpendingPacePanel, UpcomingPanel } from './types'

setBaseCurrency('USD')

/** Answer every query in a `/api/query` request with `answers[source]`. */
function serve(answers: Record<string, unknown>) {
  authFetch.mockImplementation(async (url: string) => {
    const parsed = new URL(url, 'http://x')
    if (parsed.pathname !== '/api/query') {
      return { ok: true, status: 200, json: async () => answers[parsed.pathname] ?? {} }
    }
    const { queries } = JSON.parse(parsed.searchParams.get('q') ?? '{}')
    const lines = queries.map((q: { refId: string; source: string }) =>
      JSON.stringify({ refId: q.refId, status: 200, data: answers[q.source] }),
    )
    return new Response(lines.join('\n') + '\n', { status: 200 })
  })
}

function renderWidget(defId: string, config: Record<string, unknown> = {}) {
  const Comp = WIDGET_COMPONENTS[defId]!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === defId)!
  const instance: WidgetInstance = {
    instanceId: `w-${defId}`,
    defId,
    x: 0,
    y: 0,
    w: 6,
    h: 200,
    config,
  }
  return render(<Comp instance={instance} def={def} onPatch={vi.fn()} />)
}

function asked(): string[] {
  return authFetch.mock.calls.flatMap(([url]) => {
    const parsed = new URL(String(url), 'http://x')
    if (parsed.pathname !== '/api/query') return [parsed.pathname]
    const { queries } = JSON.parse(parsed.searchParams.get('q') ?? '{}')
    return queries.map((q: { source: string }) => q.source)
  })
}

const UPCOMING: UpcomingPanel = {
  base_currency: 'USD',
  days: 30,
  rows: [
    {
      date: '2026-11-24',
      days: 2,
      kind: 'income',
      direction: 'in',
      label: 'Salary',
      sub: 'Recurring income',
      amount_base: 4000,
    },
    {
      date: '2026-11-25',
      days: 3,
      kind: 'bill',
      direction: 'out',
      label: 'Rent',
      sub: 'Recurring bill',
      amount_base: 1500,
    },
  ],
  going_out_total_base: 1500,
  coming_in_total_base: 4000,
  overdue_count: 1,
  overdue_total_base: 80,
  no_dated_bills: false,
}

const CROSSOVER: CrossoverPanel = {
  passive_monthly: 100,
  annual_passive: 1200,
  expenses_monthly: 1000,
  coverage_pct: 10.4,
  to_go_monthly: 900,
  known: true,
  coverage_pct_rounded: 10,
  coverage_bar_pct: 10,
  headline: 'year',
  year: 2035,
  projected: true,
}

const PACE: SpendingPacePanel = {
  month: '2026-11',
  day_of_month: 15,
  days_in_month: 30,
  pace_pct: 50,
  pace_pct_rounded: 50,
  tick_pct: 50,
  unmatched: [],
  rows: [
    {
      key: 'fun',
      label: 'Fun',
      color: '',
      assigned: 100,
      spent: 56,
      fill_pct: 56,
      over: false,
      remaining: 44,
      avg_per_day: 3.73,
      left_per_day: 2.93,
      state: 'ahead',
      fill_pct_rounded: 56,
      bar_pct: 56,
    },
  ],
}

beforeEach(() => clearQueryCache())
afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

describe('server panels on', () => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ dashboard_server_panels: true })))
  afterEach(() => restore())

  it('Next 30 days draws the server rows, days and totals', async () => {
    serve({ panel_upcoming: UPCOMING })
    const { container } = renderWidget('upcoming')
    await waitFor(() => expect(container.textContent).toContain('Salary'))
    const text = container.textContent ?? ''
    expect(text).toContain('in 2d')
    expect(text).toContain('$1,500.00 going out')
    expect(text).toContain('$4,000.00 coming in')
    expect(text).toContain('1 overdue')
    expect(asked()).toEqual(['panel_upcoming'])
  })

  it('Crossover shows the year the server projected', async () => {
    serve({ panel_crossover: CROSSOVER })
    const { container } = renderWidget('crossover')
    await waitFor(() => expect(container.textContent).toContain('2035'))
    expect(container.textContent).toContain('projected')
    expect(container.textContent).toContain('10%')
    expect(asked()).toEqual(['panel_crossover'])
  })

  it('Spending pace colours a row by the state the server judged', async () => {
    serve({ panel_spending_pace: PACE })
    const { container } = renderWidget('spending-pace', { categories: ['Fun'] })
    await waitFor(() => expect(container.textContent).toContain('Fun'))
    const row = container.querySelector('article.row') as HTMLElement
    expect(row.style.getPropertyValue('--health')).toBe('var(--warn)')
    expect(container.textContent).toContain('50% of month elapsed')
  })
})

describe('server panels off', () => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ dashboard_server_panels: false })))
  afterEach(() => restore())

  it('Next 30 days keeps its old read', async () => {
    serve({})
    renderWidget('upcoming')
    await waitFor(() => expect(authFetch).toHaveBeenCalled())
    expect(asked()).not.toContain('panel_upcoming')
  })
})

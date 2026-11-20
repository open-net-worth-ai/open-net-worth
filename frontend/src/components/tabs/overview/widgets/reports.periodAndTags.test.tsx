// @vitest-environment jsdom
//
// A user asked for Money flow and the spending charts over "last month, quarter, 6 months,
// year", for a spending group to open into its categories, and to filter by tag. Each Reports
// widget now has a Period and a Tags setting: the default option keeps the window the widget
// always had (no `period` sent), any other sends one, and tags travel as repeated `tag=`.

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))
vi.mock('../charts', async (orig) => ({
  ...(await orig<typeof import('../charts')>()),
  OvChart: () => <canvas data-testid="ov-chart" />,
}))

import { reportsWidgetComponents } from './reports'
import { reportScope } from './reportScope'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { RegisterDeepLink, WidgetInstance } from '../types'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

const defOf = (defId: string) => WIDGET_DEFINITIONS.find((d) => d.defId === defId)!
const instanceOf = (defId: string, config: WidgetInstance['config'] = {}): WidgetInstance => ({
  instanceId: `w-${defId}`,
  defId,
  x: 0,
  y: 0,
  w: 6,
  h: 300,
  config,
})

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

describe('reportScope', () => {
  it('sends no period while a widget keeps its default option', () => {
    expect(reportScope(instanceOf('rep-spendcat'), defOf('rep-spendcat')).scope).toEqual({
      period: undefined,
      tags: [],
      categories: [],
    })
    expect(
      reportScope(instanceOf('rep-trends', { period: '6 months' }), defOf('rep-trends')).scope
        .period,
    ).toBeUndefined()
  })

  it('maps a chosen option to the server period and keeps the tags', () => {
    const sc = reportScope(
      instanceOf('rep-sankey', { period: 'This year', tags: ['trip', 42, ''] }),
      defOf('rep-sankey'),
    )
    expect(sc.scope).toEqual({ period: 'ytd', tags: ['trip'], categories: [] })
    expect(sc.periodLabel).toBe('This year')
  })

  it('ignores an option the widget does not offer (a month chart has no one-month window)', () => {
    const sc = reportScope(instanceOf('rep-trends', { period: 'Last month' }), defOf('rep-trends'))
    expect(sc.scope.period).toBeUndefined()
  })
})

const ROWS = [
  { key: 'env_hotels', label: 'Hotels', color: '', amount: 300 },
  { key: 'env_food', label: 'Food', color: '', amount: 85.5 },
]
const PERIOD_POINT = {
  ym: '2026-10',
  expense_rows: ROWS,
  expense_total: 385.5,
  income_rows: [],
  income_total: 0,
}
const ROLLUP = {
  base_currency: 'SGD',
  months: ['2026-08', '2026-09', '2026-10'],
  tags: ['trip'],
  points: [PERIOD_POINT],
  current: PERIOD_POINT,
  expense_series: [],
  income_series: [],
  expense_table: [],
  income_table: [],
  expense_totals: { current: 0, average: 0, total: 0 },
  income_totals: { current: 0, average: 0, total: 0 },
  net_totals: { current: 0, average: 0, total: 0 },
  avg_monthly_spend: 0,
  net_total: 0,
  spending_month: '2026-10',
  current_month: '2026-10',
  merchants: [],
  savings: { income: 0, spent: 0, saved: 0, rate_pct: 0, known: false, ym: '2026-10' },
  period: {
    ...PERIOD_POINT,
    id: '3m',
    from: '2026-08',
    to: '2026-10',
    months: ['2026-08', '2026-09', '2026-10'],
    merchants: [{ key: 'pay_inn', label: 'QA Inn', amount: 300, pct: 77.8 }],
    savings: { income: 0, spent: 385.5, saved: 0, rate_pct: 0, known: false },
  },
}

describe('a Reports widget set to a period and a tag', () => {
  it('asks for the period and tags, and shows the whole period', async () => {
    authFetch.mockImplementation(() => jsonOk(ROLLUP))
    const Comp = reportsWidgetComponents['rep-spendcat']!
    render(
      <Comp
        instance={instanceOf('rep-spendcat', { period: '3 months', tags: ['trip'] })}
        def={defOf('rep-spendcat')}
        onPatch={vi.fn()}
      />,
    )
    await waitFor(() => expect(screen.getByText('Hotels')).toBeTruthy())
    const url = authFetch.mock.calls
      .map((c) => c[0] as string)
      .find((u) => u.startsWith('/api/reports/rollup'))!
    expect(url).toContain('period=3m')
    expect(url).toContain('tag=trip')
    expect(screen.getByText(/#trip/)).toBeTruthy()
  })

  it('opens a merchant over the period and its tags', async () => {
    authFetch.mockImplementation(() => jsonOk(ROLLUP))
    const onOpenRegister = vi.fn()
    const Comp = reportsWidgetComponents['rep-merchants']!
    render(
      <Comp
        instance={instanceOf('rep-merchants', { period: '3 months', tags: ['trip'] })}
        def={defOf('rep-merchants')}
        onPatch={vi.fn()}
        onOpenRegister={onOpenRegister}
      />,
    )
    fireEvent.click(await screen.findByText('QA Inn'))
    expect(onOpenRegister).toHaveBeenCalledWith({
      payee: 'QA Inn',
      month: '2026-08',
      monthTo: '2026-10',
      tag: ['trip'],
    } satisfies RegisterDeepLink)
  })
})

// The compact graph stops at spending groups; the full one carries their categories.
const GROUP = {
  id: 'sg:g_travel',
  label: 'Travel',
  column: 2,
  kind: 'spend_group',
  amount: 385.5,
  pct: 100,
  color_slot: 0,
}
const FLOW_BASE = {
  month: '2026-10',
  months: ['2026-08', '2026-09', '2026-10'],
  period: '3m',
  tags: ['trip'],
  base_currency: 'SGD',
  income_total: 0,
  spend_total: 385.5,
  saved: 0,
  shortfall: 385.5,
  flow_total: 385.5,
  savings_rate_pct: null,
}
const COMPACT = {
  ...FLOW_BASE,
  columns: 3,
  nodes: [
    { id: 'total', label: 'Spent', column: 1, kind: 'total', amount: 385.5, pct: 100 },
    {
      id: 'shortfall',
      label: 'From savings',
      column: 0,
      kind: 'shortfall',
      amount: 385.5,
      pct: 100,
    },
    GROUP,
  ],
  links: [
    { source: 'shortfall', target: 'total', amount: 385.5 },
    { source: 'total', target: 'sg:g_travel', amount: 385.5 },
  ],
}
const FULL = {
  ...COMPACT,
  columns: 4,
  nodes: [
    ...COMPACT.nodes,
    {
      id: 'sp:env_hotels',
      label: 'Hotels',
      column: 3,
      kind: 'spend',
      amount: 300,
      pct: 77.8,
      group_pct: 77.8,
      category_ids: ['cat_hotels'],
      uncategorized: false,
    },
  ],
  links: [...COMPACT.links, { source: 'sg:g_travel', target: 'sp:env_hotels', amount: 300 }],
}

describe('Money flow card', () => {
  it('opens a spending group into its categories, and a category into its transactions', async () => {
    authFetch.mockImplementation((url: string) =>
      jsonOk(url.includes('compact=true') ? COMPACT : FULL),
    )
    const onOpenRegister = vi.fn()
    const Comp = reportsWidgetComponents['rep-sankey']!
    render(
      <Comp
        instance={instanceOf('rep-sankey', { period: '3 months', tags: ['trip'] })}
        def={defOf('rep-sankey')}
        onPatch={vi.fn()}
        onOpenRegister={onOpenRegister}
      />,
    )
    const group = await screen.findByRole('link', { name: /Travel:/ })
    expect(
      authFetch.mock.calls.map((c) => c[0] as string).find((u) => u.includes('compact=true')),
    ).toContain('period=3m')
    fireEvent.click(group)

    await screen.findByRole('button', { name: /All groups/ })
    const fullUrl = authFetch.mock.calls
      .map((c) => c[0] as string)
      .find((u) => u.includes('compact=false'))
    expect(fullUrl).toContain('period=3m')
    expect(fullUrl).toContain('tag=trip')

    fireEvent.click(await screen.findByRole('link', { name: /Hotels:/ }))
    expect(onOpenRegister).toHaveBeenCalledWith({
      category: 'cat_hotels',
      month: '2026-08',
      monthTo: '2026-10',
      tag: ['trip'],
    })

    fireEvent.click(screen.getByRole('button', { name: /All groups/ }))
    expect(await screen.findByRole('link', { name: /Travel:/ })).toBeTruthy()
  })
})

describe('Savings rate card', () => {
  it('says one line under the gauge, with the 3-month average on hover', async () => {
    authFetch.mockImplementation(() =>
      jsonOk({
        ...ROLLUP,
        period: undefined,
        current: { ...PERIOD_POINT, ym: '2026-11' },
        savings: {
          income: 5000,
          spent: 3000,
          saved: 2000,
          rate_pct: 40,
          known: true,
          ym: '2026-10',
          trailing: { monthly: 1800, rate_pct: 36, months: 3, from: '2026-08', to: '2026-10' },
        },
      }),
    )
    const Comp = reportsWidgetComponents['rep-savings']!
    const { container } = render(
      <Comp instance={instanceOf('rep-savings')} def={defOf('rep-savings')} onPatch={vi.fn()} />,
    )
    await screen.findByText('40%')
    expect(container.querySelectorAll('.ov-savings-sub')).toHaveLength(1)
    expect(screen.queryByText(/3-month average/)).toBeNull()
    expect(screen.queryByText(/card/i)).toBeNull()
    expect(container.querySelector('.ov-savings-gauge-wrap')!.getAttribute('title')).toMatch(
      /3-month average \(Aug to Oct\)/,
    )
  })
})

describe('the header filter', () => {
  const CATS = [
    { id: 'cat_dining', label: 'Dining' },
    { id: 'cat_hotels', label: 'Hotels' },
  ]
  function route() {
    authFetch.mockImplementation((url: string) =>
      jsonOk(
        url.startsWith('/api/categories')
          ? CATS
          : url.startsWith('/api/transactions/tags')
            ? { tags: [{ tag: 'trip', count: 3 }] }
            : ROLLUP,
      ),
    )
  }

  it('names the period and filters on the widget, and a pick saves to the widget at once', async () => {
    route()
    const onPatch = vi.fn()
    const Comp = reportsWidgetComponents['rep-spendcat']!
    render(
      <Comp
        instance={instanceOf('rep-spendcat', { period: '3 months', categories: ['cat_dining'] })}
        def={defOf('rep-spendcat')}
        onPatch={onPatch}
      />,
    )
    const pill = await screen.findByRole('button', { name: /Filter: Aug – Oct · Dining/ })
    expect(authFetch.mock.calls.some((c) => String(c[0]).includes('category=cat_dining'))).toBe(
      true,
    )
    fireEvent.click(pill)
    const pop = await screen.findByRole('dialog', { name: 'Filter this widget' })
    fireEvent.click(await within(pop).findByRole('checkbox', { name: 'Hotels' }))
    expect(onPatch).toHaveBeenLastCalledWith({
      config: { period: '3 months', categories: ['cat_dining', 'cat_hotels'] },
    })
    fireEvent.focus(within(pop).getByPlaceholderText('Period'))
    fireEvent.mouseDown(within(pop).getByRole('button', { name: '6 months' }))
    expect(onPatch).toHaveBeenLastCalledWith({
      config: { period: '6 months', categories: ['cat_dining'] },
    })
    fireEvent.click(within(pop).getByRole('button', { name: /Clear categories and tags/ }))
    expect(onPatch).toHaveBeenLastCalledWith({
      config: { period: '3 months', categories: [], tags: [] },
    })
  })

  it('keeps the filter on an empty widget, so a filter that matches nothing can be undone', async () => {
    const empty = { ...PERIOD_POINT, expense_rows: [], expense_total: 0 }
    authFetch.mockImplementation((url: string) =>
      jsonOk(
        url.startsWith('/api/categories')
          ? CATS
          : { ...ROLLUP, points: [empty], period: { ...ROLLUP.period, ...empty } },
      ),
    )
    const Comp = reportsWidgetComponents['rep-spendcat']!
    render(
      <Comp
        instance={instanceOf('rep-spendcat', { period: '3 months', categories: ['cat_hotels'] })}
        def={defOf('rep-spendcat')}
        onPatch={vi.fn()}
      />,
    )
    expect(await screen.findByText(/No spending in Aug – Oct · 1 category/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Filter: Aug – Oct · Hotels/ })).toBeTruthy()
  })

  it('offers only a period on Savings rate', async () => {
    route()
    const Comp = reportsWidgetComponents['rep-savings']!
    authFetch.mockImplementation((url: string) =>
      jsonOk(
        url.startsWith('/api/categories')
          ? CATS
          : {
              ...ROLLUP,
              period: undefined,
              savings: { income: 10, spent: 5, saved: 5, rate_pct: 50, known: true, ym: '2026-10' },
            },
      ),
    )
    render(
      <Comp instance={instanceOf('rep-savings')} def={defOf('rep-savings')} onPatch={vi.fn()} />,
    )
    fireEvent.click(await screen.findByRole('button', { name: /^Filter:/ }))
    const pop = await screen.findByRole('dialog', { name: 'Filter this widget' })
    // A period alone is a plain menu of periods, not a box holding one dropdown.
    expect(within(pop).getByRole('menuitemradio', { name: '3 months' })).toBeTruthy()
    expect(within(pop).queryByText('Categories')).toBeNull()
    expect(within(pop).queryByText('Tags')).toBeNull()
  })

  it('opens a merchant over the chosen categories too', async () => {
    authFetch.mockImplementation(() => jsonOk(ROLLUP))
    const onOpenRegister = vi.fn()
    const Comp = reportsWidgetComponents['rep-merchants']!
    render(
      <Comp
        instance={instanceOf('rep-merchants', { period: '3 months', categories: ['cat_hotels'] })}
        def={defOf('rep-merchants')}
        onPatch={vi.fn()}
        onOpenRegister={onOpenRegister}
      />,
    )
    fireEvent.click(await screen.findByText('QA Inn'))
    expect(onOpenRegister).toHaveBeenCalledWith(
      expect.objectContaining({ payee: 'QA Inn', category: ['cat_hotels'] }),
    )
  })

  it('shows every merchant on All, not none', async () => {
    authFetch.mockImplementation(() => jsonOk(ROLLUP))
    const Comp = reportsWidgetComponents['rep-merchants']!
    render(
      <Comp
        instance={{ ...instanceOf('rep-merchants', { period: '3 months' }), limit: 0 }}
        def={defOf('rep-merchants')}
        onPatch={vi.fn()}
      />,
    )
    // All is "no row cap": slice(0, null) read it as zero rows.
    expect(await screen.findByText('QA Inn')).toBeTruthy()
    const key = authFetch.mock.calls.map((c) => String(c[0])).find((u) => u.includes('rollup'))
    expect(key).toContain('merchant_limit=0')
  })
})

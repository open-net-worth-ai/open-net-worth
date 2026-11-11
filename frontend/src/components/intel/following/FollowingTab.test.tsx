// @vitest-environment jsdom
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))
vi.mock('../../../hooks/useIsMobile', () => ({ useIsMobile: () => false }))
// The chart engine draws on a canvas jsdom does not have: a stand-in shows the chart's name and
// what its tooltip would say at the last point, so a test can read both.
vi.mock('./ActivityChart', () => ({
  CHART_HEIGHT: 200,
  ActivityChart: (p: {
    label: string
    points: { date: string }[]
    tip?: (date: string) => ReactNode
  }) => (
    <div role="img" aria-label={p.label}>
      {p.tip?.(p.points[p.points.length - 1]!.date)}
    </div>
  ),
}))

import { authFetch } from '../../../api/client'
import { clearQueryCache } from '../../../state/queryCache'
import { FollowingTab } from './FollowingTab'
import { COPY } from './following.copy'
import { shortDate } from './following.format'
import { DIRECTORY, fund, installServer, member, type Server } from './followingTestKit'

const mockFetch = authFetch as ReturnType<typeof vi.fn>
const onAskLucius = vi.fn()
let server: Server

// Every box is wide: the tables are tables and the toolbars their wide form.
const width = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
beforeAll(() =>
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get: () => 1200,
  }),
)
afterAll(() => {
  if (width) Object.defineProperty(HTMLElement.prototype, 'clientWidth', width)
})

function setup(watched: string[] = ['fol_fund']) {
  server = { portfolios: [fund(), member()], watched: new Set(watched), directory: DIRECTORY }
  installServer(mockFetch, server)
  return render(<FollowingTab onAskLucius={onAskLucius} />)
}

async function open(name: string) {
  fireEvent.click(await screen.findByRole('button', { name: COPY.openLabel(name) }))
  return screen.findByRole('heading', { name: new RegExp(name) })
}

const sections = (name: string) => screen.getByRole('tablist', { name: COPY.sectionsLabel(name) })
const tab = (name: string, label: string) =>
  fireEvent.click(within(sections(name)).getByRole('tab', { name: label }))
const filters = () => screen.getByRole('tablist', { name: COPY.filterLabel })
const symbols = (table: HTMLElement) =>
  within(table)
    .getAllByRole('row')
    .slice(1)
    .map((r) => r.querySelector('.mk-hsym')?.textContent ?? r.textContent)
const calls = (method: string) =>
  mockFetch.mock.calls.filter(([, init]) => ((init as RequestInit)?.method ?? 'GET') === method)

beforeEach(() => {
  clearQueryCache()
  sessionStorage.clear()
  navigate.mockReset()
  onAskLucius.mockReset()
})
afterEach(() => {
  cleanup()
  mockFetch.mockReset()
})

describe('Discover', () => {
  it('lists every portfolio, with a count on every filter', async () => {
    setup()
    await screen.findByRole('button', { name: COPY.openLabel('Borealis Partners') })
    const bar = filters()
    for (const name of ['All 3', 'Watching 1', 'Funds 2', 'Politicians 1'])
      expect(within(bar).getByRole('tab', { name })).toBeTruthy()
    fireEvent.click(within(bar).getByRole('tab', { name: 'Politicians 1' }))
    expect(screen.queryByRole('button', { name: COPY.openLabel('Acme Capital') })).toBeNull()
    expect(screen.getByRole('button', { name: COPY.openLabel('Pat Placeholder') })).toBeTruthy()
  })

  it('searches in place and says when nothing matches', async () => {
    setup()
    const search = await screen.findByRole('searchbox', { name: COPY.searchLabel })
    fireEvent.change(search, { target: { value: 'zzz' } })
    expect(await screen.findByText(COPY.noMatch('zzz'))).toBeTruthy()
  })

  it('opens a portfolio, and the back button returns to the list', async () => {
    setup()
    await open('Acme Capital')
    fireEvent.click(screen.getByRole('button', { name: COPY.backToAll }))
    expect(await screen.findByRole('button', { name: COPY.openLabel('Acme Capital') })).toBeTruthy()
  })
})

describe('the header', () => {
  it('names who it is and the two facts every tab rests on, as separate chips', async () => {
    setup()
    await open('Acme Capital')
    expect(screen.getByText('Ada Acme')).toBeTruthy()
    const snapshot = screen.getByText(COPY.holdingsOn(shortDate('2026-06-30')))
    expect(snapshot.closest('[title]')?.getAttribute('title')).toBe(
      COPY.disclosedOn(shortDate('2026-08-14')),
    )
    expect(screen.getByText(COPY.latestTrade(shortDate('2026-10-02')))).toBeTruthy()
  })

  it('copies the portfolio with Lucius through the automation template', async () => {
    setup()
    await open('Acme Capital')
    fireEvent.click(screen.getByRole('button', { name: COPY.copyCta }))
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(expect.objectContaining({ to: '/automations' })),
    )
  })

  it('watches with the bell, and stops only after the confirmation', async () => {
    setup([])
    await open('Acme Capital')
    fireEvent.click(screen.getByRole('button', { name: COPY.watchLabel }))
    await screen.findByRole('button', { name: COPY.unwatchLabel })
    expect(calls('POST')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: COPY.unwatchLabel }))
    const dialog = await screen.findByRole('dialog')
    expect(calls('DELETE')).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: COPY.unwatch }))
    await waitFor(() => expect(calls('DELETE')).toHaveLength(1))
  })
})

describe('Trading Activity', () => {
  it('opens on the whole range with every move, and a filter narrows it', async () => {
    setup()
    await open('Acme Capital')
    const period = screen.getByRole('combobox', { name: COPY.periodLabel }) as HTMLSelectElement
    expect(period.selectedOptions[0]!.textContent).toBe('Apr 2026 – Now')
    const table = screen.getByRole('table', { name: 'Apr 2026 – Now' })
    expect(symbols(table).sort()).toEqual(['ACME', 'BOLT', 'BOLT', 'CRUX', 'OXYZ'])
    fireEvent.click(within(filters()).getByRole('tab', { name: 'Sold out' }))
    expect(symbols(screen.getByRole('table', { name: 'Apr 2026 – Now' }))).toEqual(['OXYZ'])
  })

  it('shows the dated trades after the last report when that period is picked', async () => {
    setup()
    await open('Acme Capital')
    fireEvent.change(screen.getByRole('combobox', { name: COPY.periodLabel }), {
      target: { value: 'since' },
    })
    expect(symbols(screen.getByRole('table', { name: 'Since Jun 30' }))).toEqual(['BOLT'])
  })

  it('charts the portfolio over the chosen period with its moves on it', async () => {
    setup()
    await open('Acme Capital')
    expect(screen.getByRole('img', { name: /each move marked/ })).toBeTruthy()
  })

  it('copies one picked move through Lucius, the trade spelled out', async () => {
    setup()
    await open('Acme Capital')
    fireEvent.change(screen.getByRole('combobox', { name: COPY.periodLabel }), {
      target: { value: 'since' },
    })
    const table = screen.getByRole('table', { name: 'Since Jun 30' })
    fireEvent.click(within(table).getAllByRole('row')[1]!)
    expect(screen.getByRole('button', { name: new RegExp(COPY.tradeDetails) })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /^Copy this trade:/ }))
    expect(onAskLucius).toHaveBeenCalledWith(expect.stringContaining('BOLT'))
  })

  it('offers a member Bought and Sold, newest first', async () => {
    setup()
    await open('Pat Placeholder')
    expect(screen.queryByText(/^Holdings on /)).toBeNull()
    expect(screen.getByText(COPY.latestTrade(shortDate('2026-10-02')))).toBeTruthy()
    const bar = filters()
    expect(
      within(bar)
        .getAllByRole('tab')
        .map((t) => t.textContent),
    ).toEqual(['All', 'Bought', 'Sold'])
    fireEvent.click(within(bar).getByRole('tab', { name: 'Sold' }))
    const table = screen.getAllByRole('table')[0]!
    expect(symbols(table)).toEqual(['OLDR'])
  })
})

describe('Holdings', () => {
  it('lists every sector with its share, and a sector narrows the table to it', async () => {
    setup()
    await open('Acme Capital')
    tab('Acme Capital', COPY.tabHoldings)
    const keys = screen.getByRole('group', { name: COPY.keysBySector })
    const names = within(keys)
      .getAllByRole('button')
      .map((b) => b.querySelector('.fol-key-name')?.textContent)
    expect(names).toEqual(['Technology', 'Industrials', 'Energy'])
    expect(within(keys).getByRole('button', { name: /^Technology\s*62\.0%/ })).toBeTruthy()

    const table = () => screen.getByRole('table', { name: COPY.holdingsTitle })
    fireEvent.click(within(keys).getByRole('button', { name: /Technology/ }))
    expect(symbols(table())).toEqual(['ACME', 'CRUX'])
    fireEvent.click(within(keys).getByRole('button', { name: /Technology/ }))
    expect(symbols(table())).toHaveLength(5)
  })

  it('keeps a holding with no ticker as a row with the fix, and searches the list', async () => {
    setup()
    await open('Acme Capital')
    tab('Acme Capital', COPY.tabHoldings)
    expect(screen.getByRole('button', { name: COPY.tickerSetAction })).toBeTruthy()
    fireEvent.change(screen.getByRole('searchbox', { name: COPY.holdingsSearch }), {
      target: { value: 'zzz' },
    })
    expect(screen.getByText(COPY.noHoldingMatch('zzz'))).toBeTruthy()
  })
})

describe('Overview', () => {
  it('shows the two figures and the return against the benchmark for the chosen window', async () => {
    setup()
    await open('Acme Capital')
    tab('Acme Capital', COPY.tabOverview)
    expect(screen.getByText(COPY.kpiValue)).toBeTruthy()
    expect(screen.getByText(/^\+\$100(\.0)?M$/)).toBeTruthy()
    expect(screen.getByText('SPY +10.8%')).toBeTruthy()
    fireEvent.click(
      within(screen.getByRole('tablist', { name: COPY.perfPeriodLabel })).getByRole('tab', {
        name: '6M',
      }),
    )
    expect(screen.getByText('SPY +6.0%')).toBeTruthy()
  })
})

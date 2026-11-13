// @vitest-environment jsdom
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
// A small stand-in for the router: the open portfolio lives in the address (?portfolio=), so
// navigating changes what useSearch returns, and opening one adds a history entry Back undoes.
const router = vi.hoisted(() => {
  let search: Record<string, unknown> = {}
  const back: Record<string, unknown>[] = []
  const subs = new Set<() => void>()
  const emit = () => subs.forEach((f) => f())
  return {
    get: () => search,
    subscribe: (f: () => void) => (subs.add(f), () => void subs.delete(f)),
    go(next: Record<string, unknown>, replace: boolean) {
      if (!replace) back.push(search)
      search = next
      emit()
    },
    back() {
      search = back.pop() ?? {}
      emit()
    },
    reset() {
      search = {}
      back.length = 0
    },
  }
})
const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', async () => {
  const { useSyncExternalStore } = await import('react')
  return {
    useNavigate: () => navigate,
    useSearch: () => useSyncExternalStore(router.subscribe, router.get),
    // Where the app is going: in these tests, always the Intel screen.
    useRouter: () => ({ latestLocation: { pathname: '/intel' } }),
  }
})
/** Picks the page's time range from its menu (one range, shared by every tab). */
function pickRange(key: string) {
  const period = screen.getAllByRole('combobox', { name: COPY.periodLabel })[0] as HTMLSelectElement
  fireEvent.change(period, { target: { value: key } })
}

function followRoutes() {
  navigate.mockImplementation((o: { to: string; search?: unknown; replace?: boolean }) => {
    if (o.to !== '/intel') return
    const next =
      typeof o.search === 'function'
        ? (o.search as (p: Record<string, unknown>) => Record<string, unknown>)(router.get())
        : (o.search as Record<string, unknown>)
    router.go(next, !!o.replace)
  })
}
vi.mock('../../../hooks/useIsMobile', () => ({ useIsMobile: () => false }))
const openAsset = vi.hoisted(() => vi.fn())
vi.mock('../../../state/assetNav', () => ({ useAssetNav: () => ({ openAsset }) }))
// The owner's own book holds one of the fund's stocks.
vi.mock('../usePortfolioTickerMaps', () => ({
  usePortfolioTickerMaps: () => ({ heldSet: new Set(['BOLT']) }),
}))
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
  router.reset()
  followRoutes()
  vi.spyOn(window.history, 'back').mockImplementation(() => router.back())
  onAskLucius.mockReset()
  openAsset.mockReset()
})
afterEach(() => {
  cleanup()
  mockFetch.mockReset()
  vi.restoreAllMocks()
})

describe('Discover', () => {
  it('lists every portfolio, with a count on every filter', async () => {
    setup()
    await screen.findByRole('button', { name: COPY.openLabel('Borealis Partners') })
    const bar = filters()
    // Every filter, each with its count; one with nothing in it shows 0, never disappears.
    expect(
      within(bar)
        .getAllByRole('tab')
        .map((t) => t.textContent),
    ).toEqual(['All 3', 'Watching 1', 'Funds 2', 'Politicians 1', 'ETFs 0'])
    fireEvent.click(within(bar).getByRole('tab', { name: /^Politicians/ }))
    expect(screen.queryByRole('button', { name: COPY.openLabel('Acme Capital') })).toBeNull()
    expect(screen.getByRole('button', { name: COPY.openLabel('Pat Placeholder') })).toBeTruthy()
  })

  it('searches in place and says when nothing matches', async () => {
    setup()
    const search = await screen.findByRole('searchbox', { name: COPY.searchLabel })
    fireEvent.change(search, { target: { value: 'zzz' } })
    expect(await screen.findByText(COPY.noMatch('zzz'))).toBeTruthy()
  })

  it('opens a portfolio, and the breadcrumb returns to the list', async () => {
    setup()
    await open('Acme Capital')
    // "Famous Portfolios / Acme Capital": its first part goes back (owner, 2026-11-13).
    const crumb = screen.getByRole('navigation', { name: COPY.breadcrumbLabel })
    expect(crumb.textContent).toContain('Acme Capital')
    fireEvent.click(within(crumb).getByRole('button', { name: COPY.backToAll }))
    expect(await screen.findByRole('button', { name: COPY.openLabel('Acme Capital') })).toBeTruthy()
  })
})

describe('the header', () => {
  it('keeps the header to one line, and puts what it holds and when on Overview', async () => {
    setup()
    await open('Acme Capital')
    // The header: the name and whose it is, nothing else.
    expect(screen.getByText('Ada Acme')).toBeTruthy()
    expect(screen.queryByText(COPY.figFiled)).toBeNull()
    // Overview (the Holdings tab's label since 2026-11-13): a description written from the data,
    // then the key figures, then what they hold.
    tab('Acme Capital', COPY.tabHoldings)
    expect(screen.getByRole('region', { name: COPY.aboutLabel('Acme Capital') })).toBeTruthy()
    expect(screen.getByText(COPY.figStocks)).toBeTruthy()
    const filed = screen.getByText(shortDate('2026-08-14'))
    fireEvent.mouseEnter(filed.closest('.tip-anchor')!)
    expect(await screen.findByText(COPY.holdingsAsOf(shortDate('2026-06-30')))).toBeTruthy()
    // A fund's newest trade is in its table, never a key figure (only a member's is).
    expect(screen.queryByText(COPY.figLatestTrade)).toBeNull()
  })

  it('copies the portfolio with Lucius through the automation template', async () => {
    setup()
    await open('Acme Capital')
    fireEvent.click(screen.getByRole('button', { name: COPY.copyCta }))
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(
        expect.objectContaining({
          to: '/automations',
          search: { template: 'copy-followed-portfolio', for: 'Acme Capital' },
        }),
      ),
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
  it('opens on 1Y, the window the card shows; All lists every move, and a filter narrows it', async () => {
    setup()
    await open('Acme Capital')
    const period = screen.getByRole('combobox', { name: COPY.periodLabel }) as HTMLSelectElement
    expect([...period.options].map((o) => o.textContent)).toEqual([
      '1W',
      '1M',
      '3M',
      '6M',
      'YTD',
      '1Y',
      '2Y',
      '3Y',
      'All',
    ])
    // One range for the whole page, 1Y by default (owner, 2026-11-13).
    expect(period.selectedOptions[0]!.textContent).toBe('1Y')
    pickRange('ALL')
    const table = screen.getByRole('table', { name: 'All' })
    expect(symbols(table).sort()).toEqual(['ACME', 'BOLT', 'BOLT', 'CRUX', 'OXYZ'])
    fireEvent.click(within(filters()).getByRole('tab', { name: /^Sold out/ }))
    expect(symbols(screen.getByRole('table', { name: 'All' }))).toEqual(['OXYZ'])
  })

  it('keeps the moves whose day is in the range: a trade by its day, a report by its disclosure', async () => {
    // Today is Oct 20: 1M (from Sep 20) holds BOLT's Oct 2 trade, not the report disclosed
    // Aug 14; 3M (from Jul 20) holds both.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-20T12:00:00Z'))
    setup()
    await open('Acme Capital')
    const pick = (label: string) =>
      fireEvent.click(
        within(screen.getByRole('tablist', { name: COPY.periodLabel })).getByRole('tab', {
          name: label,
        }),
      )
    pick('1M')
    expect(symbols(screen.getByRole('table', { name: '1M' }))).toEqual(['BOLT'])
    pick('3M')
    expect(symbols(screen.getByRole('table', { name: '3M' })).sort()).toEqual([
      'ACME',
      'BOLT',
      'BOLT',
      'CRUX',
      'OXYZ',
    ])
    vi.useRealTimers()
  })

  it('charts the portfolio over the chosen period with its moves on it', async () => {
    setup()
    await open('Acme Capital')
    expect(screen.getByRole('img', { name: /each move marked/ })).toBeTruthy()
  })

  it('copies one picked move through Lucius, the trade spelled out', async () => {
    setup()
    await open('Acme Capital')
    pickRange('ALL')
    const table = screen.getByRole('table', { name: 'All' })
    const bolt = within(table)
      .getAllByRole('row')
      .find((r) => r.textContent?.includes('Oct 2'))!
    fireEvent.click(bolt)
    // The card switches to the move once its prices are in.
    expect(await screen.findByRole('button', { name: new RegExp(COPY.tradeDetails) })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /^Copy this trade:/ }))
    expect(onAskLucius).toHaveBeenCalledWith(expect.stringContaining('BOLT'))
    // Trade details carries Copy trade too: on a phone that is the only place it is.
    fireEvent.click(screen.getByRole('button', { name: new RegExp(COPY.tradeDetails) }))
    const sheet = await screen.findByRole('dialog', { name: COPY.tradeDetails })
    expect(within(sheet).getByRole('button', { name: /^Copy this trade:/ })).toBeTruthy()
  })

  it('offers a member Bought and Sold, newest first', async () => {
    setup()
    await open('Pat Placeholder')
    tab('Pat Placeholder', COPY.tabHoldings)
    // A member has no quarterly report: their latest trade is the date the figures rest on.
    expect(screen.queryByText(COPY.figFiled)).toBeNull()
    expect(screen.getByText(COPY.figLatestTrade)).toBeTruthy()
    expect(screen.getByText(shortDate('2026-10-02'))).toBeTruthy()
    tab('Pat Placeholder', COPY.tabActivity)
    const bar = filters()
    expect(
      within(bar)
        .getAllByRole('tab')
        .map((t) => t.textContent),
    ).toEqual(['All 2', 'Bought 1', 'Sold 1'])
    fireEvent.click(within(bar).getByRole('tab', { name: /^Sold/ }))
    const table = screen.getAllByRole('table')[0]!
    expect(symbols(table)).toEqual(['OLDR'])
  })
})

// Latest moves is off for now (owner, 2026-11-12: SHOW_LATEST in ActivityTab.tsx). These come
// back with it.
describe.skip('Latest moves', () => {
  it('leads with the newest trades, then the last report’s biggest buys and sales', async () => {
    setup()
    await open('Acme Capital')
    const strip = screen.getByRole('region', { name: COPY.latestTitle })
    const group = (title: string) =>
      [...within(strip).getByText(title).parentElement!.querySelectorAll('.fol-move-chip b')].map(
        (e) => e.textContent,
      )
    // Newest first, each named by when it became public, never by the quarter it covers.
    const titles = [...strip.querySelectorAll('.fol-latest-title')].map((t) => t.textContent)
    expect(titles).toEqual([
      COPY.recentTrades,
      COPY.boughtReported('Aug 14'),
      COPY.soldReported('Aug 14'),
    ])
    expect(group(COPY.recentTrades)).toEqual(['BOLT'])
    expect(group(COPY.boughtReported('Aug 14'))).toEqual(['BOLT', 'CRUX'])
    expect(group(COPY.soldReported('Aug 14'))).toEqual(['OXYZ', 'ACME'])
  })

  it('shows a move in the table, picked, ready to copy', async () => {
    setup()
    await open('Acme Capital')
    const strip = screen.getByRole('region', { name: COPY.latestTitle })
    fireEvent.click(within(strip).getByRole('button', { name: /^Sold out OXYZ/ }))
    const period = screen.getByRole('tablist', { name: COPY.periodLabel })
    expect(within(period).getByRole('tab', { selected: true }).textContent).toBe('All')
    expect(screen.getByRole('button', { name: /^Copy this trade:.*OXYZ/ })).toBeTruthy()
  })

  it('says which of their stocks you own', async () => {
    setup()
    await open('Acme Capital')
    const table = screen.getByRole('table', { name: 'All' })
    const bolt = within(table)
      .getAllByRole('row')
      .find((r) => r.textContent?.startsWith('BOLT'))!
    expect(within(bolt).getByText(COPY.youOwn)).toBeTruthy()
    const acme = within(table)
      .getAllByRole('row')
      .find((r) => r.textContent?.startsWith('ACME'))!
    expect(within(acme).queryByText(COPY.youOwn)).toBeNull()
  })
})

describe('Holdings', () => {
  it('rings every sector by its share, and a legend row names itself and narrows the table', async () => {
    setup()
    await open('Acme Capital')
    tab('Acme Capital', COPY.tabHoldings)
    const map = screen.getByRole('group', { name: COPY.mapBySector })
    const tiles = within(map).getAllByRole('button')
    expect(tiles.map((t) => t.getAttribute('aria-label'))).toEqual([
      'Technology, 62.0%, −33.1% this quarter',
      'Industrials, 30.0%, +100.0% this quarter',
      'Energy, 4.0%',
    ])

    const table = () => screen.getByRole('table', { name: COPY.holdingsTitle })
    fireEvent.click(tiles[0]!)
    expect(symbols(table())).toEqual(['ACME', 'CRUX'])
    const picked = document.querySelector('.fol-picked') as HTMLElement
    expect(within(picked).getByText('Technology')).toBeTruthy()
    expect(within(picked).getByText('62.0%')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: COPY.showAll }))
    expect(symbols(table())).toHaveLength(5)
  })

  it('opens a holding’s stock page from its row', async () => {
    setup()
    await open('Acme Capital')
    tab('Acme Capital', COPY.tabHoldings)
    const table = screen.getByRole('table', { name: COPY.holdingsTitle })
    fireEvent.click(within(table).getAllByRole('row')[1]!)
    expect(openAsset).toHaveBeenCalledWith(expect.objectContaining({ ticker: 'ACME' }))
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

describe('Overview: the return', () => {
  // A figure is a small name over its value; find the value by its name.
  const figure = (name: string) =>
    screen
      .getAllByText(name)
      .map((e) => e.closest('.fol-ret3-item'))
      .find(Boolean)
      ?.querySelector('.fol-ret3-v')?.textContent

  it('shows the return against the benchmark for the chosen window, on Overview', async () => {
    setup()
    await open('Acme Capital')
    tab('Acme Capital', COPY.vsTitle('Acme Capital'))
    expect(figure('SPY')).toBe('+10.8%')
    fireEvent.click(
      within(screen.getByRole('tablist', { name: COPY.perfPeriodLabel })).getByRole('tab', {
        name: '6M',
      }),
    )
    expect(figure('SPY')).toBe('+6.0%')
  })

  it('draws your own line from where your history starts when the range starts earlier', async () => {
    setup()
    // Your history starts halfway through the year the chart shows.
    server.ownReturns = [
      { date: '2026-05-10', portfolio: 0.1, benchmark: null },
      { date: '2026-11-10', portfolio: 0.21, benchmark: null },
    ]
    await open('Acme Capital')
    tab('Acme Capital', COPY.vsTitle('Acme Capital'))
    // Rebased to its own first day: 1.21 / 1.10 - 1 = +10%. It used to vanish.
    expect((await screen.findAllByText(COPY.yourPortfolio)).length).toBeGreaterThan(0)
    expect(figure(COPY.yourPortfolio)).toBe('+10.0%')
    const label = screen.getAllByText(COPY.yourPortfolio).find((e) => e.closest('.fol-ret3-item'))!
    fireEvent.mouseEnter(label.closest('.tip-anchor')!)
    expect(await screen.findByText(COPY.yourSince(shortDate('2026-05-10')))).toBeTruthy()
  })
})

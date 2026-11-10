// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, fireEvent, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { StoryArc } from '../intel/storyMeta'

// ── Mocks: data via authFetch; heavy children replaced by thin stand-ins ────────

const mockNavigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useRouterState: () => ({ matches: [] }),
}))

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

vi.mock('../../state/portfolio', () => ({
  usePortfolioContext: () => ({
    positionRows: [
      { ticker: 'MSFT', instrument_id: 'i_msft', _base: 1000, _pct: 50, quantity: '1' },
    ],
  }),
}))

vi.mock('../intel/IntelBriefCard', () => ({
  IntelBriefCard: ({ children }: { children?: ReactNode }) => (
    <div data-testid="holdings-brief">{children}</div>
  ),
}))
vi.mock('../intel/WatchPulseCard', () => ({
  // Stands in for the real card: reports its scope, and — like the real one — hands the (here:
  // no) rows to renderBody, so the desk with the news stories renders inside it.
  WatchPulseCard: ({
    scope,
    ownedRows,
    renderBody,
  }: {
    scope: { name: string; tickerCount: number }
    ownedRows: { ticker: string }[]
    renderBody: (rows: never[], unpriced: number) => ReactNode
  }) => (
    <div data-testid="pulse-watched">
      <span data-testid="pulse-watched-scope">
        {`scope:${scope.name}:${scope.tickerCount} owned:${ownedRows.map((r) => r.ticker).join(',')}`}
      </span>
      {renderBody([], 0)}
    </div>
  ),
}))
vi.mock('../intel/WatchlistsSection', () => ({
  WatchlistsSection: ({ activeListId }: { activeListId: string }) => (
    <div data-testid="sidebar">
      <span data-testid="sidebar-list">{activeListId}</span>
    </div>
  ),
}))

import { Intel } from './Intel'
import { AssetNavProvider } from '../../state/assetNav'
import { invalidateQueryCache } from '../../state/queryCache'
import { requestIntelStoryFocus } from '../../hooks/useIntelStoryIndex'

function story(id: string, type: StoryArc['type'], ticker?: string, read = false): StoryArc {
  return {
    id,
    type,
    ticker,
    label: `${id} label`,
    status: 'active',
    summary: `${id} summary`,
    action_items: [],
    timeline: [],
    created_at: '2026-09-25T00:00:00Z',
    updated_at: '2026-09-25T00:00:00Z',
    read,
  }
}

function heldRow(ticker: string, weight: number) {
  return {
    ticker,
    name: `${ticker} Co`,
    instrument_id: null,
    watchlist_ids: [],
    price: 100,
    currency: 'USD',
    day_pct: -1,
    rel_day_pct: null,
    range_pos: 0.5,
    from_high_pct: -10,
    vs_200d_pct: null,
    volume_ratio: null,
    earnings_at: null,
    earnings_in_days: null,
    market_state: null,
    nearest_alert: null,
    findings: [],
    score: 0,
    fit: null,
    story_count: 0,
    story_id: null,
    week52_low: 80,
    week52_high: 120,
    position: { value: 600, weight, day_pl: -6 },
    move: null,
    forward_pe: null,
    trailing_pe: null,
    intent: null,
    thesis: null,
    in_buy_zone: null,
    material: [],
    trend: null,
    next_event: null,
  }
}

const STORIES = [
  story('s-msft', 'asset', 'MSFT'),
  story('s-risk', 'risk', undefined, true),
  story('s-nvda', 'asset', 'NVDA'),
  story('s-macro', 'macro'),
  { ...story('s-topic', 'topic'), label: 'AI capex: hyperscalers raise spend' },
]

function item(id: string, type: 'ticker' | 'topic', value: string, lists: string[]) {
  return {
    id,
    type,
    value,
    label: value,
    asset_class: 'equity',
    monitor_news: true,
    added_by: 'user',
    note: '',
    watchlist_ids: lists,
  }
}

function ok(body: unknown) {
  return Promise.resolve({ ok: true, json: async () => body } as Response)
}

beforeEach(() => {
  invalidateQueryCache(() => true)
  localStorage.clear()
  sessionStorage.clear()
  mockNavigate.mockReset()
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/intel/signals')) return ok(STORIES)
    if (url === '/api/watchlist')
      return ok({
        items: [
          item('w1', 'ticker', 'NVDA', ['wl-tech']),
          // You own MSFT, and it's on your Tech list too.
          item('w2', 'ticker', 'MSFT', ['default', 'wl-tech']),
          // Topics are followed, in no watchlist (ADR 2026-09-27-topics-are-followed-not-listed).
          item('w3', 'topic', 'AI capex', []),
          item('w4', 'topic', 'US mortgage rates', []),
        ],
        config: {
          enabled: false,
          price_check_interval_seconds: 60,
          news_scan_interval_seconds: 3600,
        },
        watchlists: [
          { id: 'default', name: 'My holdings', is_auto_sync: true, kind: 'auto', order: 0 },
          { id: 'wl-tech', name: 'Tech', is_auto_sync: false, kind: 'manual', order: 1 },
          { id: 'wl-div', name: 'Dividends', is_auto_sync: false, kind: 'manual', order: 2 },
        ],
      })
    if (url === '/api/alerts') return ok({ alerts: [] })
    if (url === '/api/intel/watch-pulse?population=held')
      return ok({
        generated_at: '2026-09-25T00:00:00Z',
        population: 'held',
        benchmark: null,
        status: 'ok',
        summary: { tracked: 2, priced: 2, up: 0, down: 1, flagged: 0 },
        rows: [heldRow('MSFT', 60), heldRow('AAPL', 40)],
      })
    return ok({})
  })
})
afterEach(cleanup)

function mount(props: Partial<Parameters<typeof Intel>[0]> = {}) {
  return render(
    <AssetNavProvider>
      <Intel onChatSuggest={vi.fn()} {...props} />
    </AssetNavProvider>,
  ) as ReturnType<typeof render> & { rerenderWith?: (n: ReactNode) => void }
}

const tabButton = (c: HTMLElement, label: string) =>
  [...c.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find((b) =>
    b.textContent?.startsWith(label),
  )!

describe('Intel tabs', () => {
  it('has one tab per watchlist: My holdings, your lists in order, Topics & macro', async () => {
    const { findByTestId, container } = mount()
    await findByTestId('holdings-brief')
    const labels = [
      ...container.querySelectorAll('[aria-label="Intelligence view"] [role="tab"]'),
    ].map((b) => b.textContent)
    // Unread per tab — a story shows on every tab it belongs to, so they don't sum to the feed:
    // s-msft is on My holdings AND on Tech (you hold MSFT and it's on that list).
    expect(labels).toEqual(['My holdings1', 'Tech2', 'Dividends', 'Topics & macro2'])
  })

  it('opens on My holdings: one card with portfolio stories, then each holding with its own stories', async () => {
    const { findByTestId, container, findByText } = mount()
    expect(await findByText('Across your portfolio')).toBeTruthy()
    const card = await findByTestId('holdings-brief')
    expect(card.textContent).toContain('s-risk label')
    // The held name with a story floats above the quiet one, and carries that story's line.
    await findByText('s-msft label')
    const names = [...container.querySelectorAll('.intel-desk-row .intel-pulse-sym')].map(
      (e) => e.textContent,
    )
    expect(names).toEqual(['MSFT'])
    expect(await findByText(/1 quiet holding/)).toBeTruthy()
    expect(container.textContent).not.toContain('s-nvda')
  })

  it('unfolds a holding’s stories in place', async () => {
    const { findByText, queryByText } = mount()
    await findByText(/1 quiet holding/) // prices have landed; rows are final
    const line = (await findByText('s-msft label')).closest('button')!
    expect(queryByText('s-msft summary')).toBeNull()
    fireEvent.click(line)
    expect(await findByText('s-msft summary')).toBeTruthy()
    expect(line.getAttribute('aria-expanded')).toBe('true')
  })

  it('jumps from the status bar to the story, unfolding its holding', async () => {
    const { findByText } = mount()
    await findByText(/1 quiet holding/)
    fireEvent.click(await findByText(/2 active/))
    expect(await findByText('s-msft summary')).toBeTruthy()
  })

  it('a list tab shows everything on the list — owned names included — and mirrors ?sub=', async () => {
    const { findByTestId, container } = mount()
    await findByTestId('holdings-brief')
    fireEvent.click(tabButton(container, 'Tech'))
    const card = await findByTestId('pulse-watched')
    expect((await findByTestId('pulse-watched-scope')).textContent).toBe('scope:Tech:2 owned:MSFT')
    // The news scan's stories on both its names sit INSIDE the card, not in a feed below it.
    expect(card.textContent).toContain('s-nvda label')
    expect(card.textContent).toContain('s-msft label')
    expect(container.querySelector('.intel-feed-filter')).toBeNull()
    expect(sessionStorage.getItem('intel-subtab')).toBe('list:wl-tech')
    const call = mockNavigate.mock.calls.at(-1)?.[0]
    expect(call.to).toBe('/intel')
    expect(call.search({ x: 1 })).toEqual({ x: 1, sub: 'list:wl-tech' })
  })

  it('puts topic stories on Topics & macro, never on a list tab', async () => {
    const { findByText, container, queryByText } = mount({ subTabRequest: 'list:wl-div' })
    await findByText('Nothing on this list yet.')
    expect(queryByText('AI capex: hyperscalers raise spend')).toBeNull()
    fireEvent.click(tabButton(container, 'Topics & macro'))
    await findByText('Topics you follow')
    expect(container.textContent).toContain('AI capex: hyperscalers raise spend')
  })

  it('honours a ?sub=macro deep link: every topic you follow, and no list card beside it', async () => {
    const { findByText, queryByTestId, container } = mount({ subTabRequest: 'macro' })
    expect(await findByText('Topics you follow')).toBeTruthy()
    expect(await findByText('s-macro summary')).toBeTruthy()
    const lead = container.querySelector('.intel-tab-lead')?.textContent ?? ''
    expect(lead).toContain('AI capex')
    expect(lead).toContain('US mortgage rates')
    // One column: no list pane (and no Monitoring card) beside Topics & macro.
    expect(queryByTestId('sidebar')).toBeNull()
    expect(container.querySelector('.intel-side')).toBeNull()
    expect(container.querySelector('.intel-layout--solo')).toBeTruthy()
  })

  it('opens the last list you used for a legacy ?sub=watching link, else your first', async () => {
    localStorage.setItem('intel-watchlist', 'wl-div')
    const first = mount({ subTabRequest: 'watching' })
    expect((await first.findByTestId('pulse-watched-scope')).textContent).toContain('Dividends')
    first.unmount()
    localStorage.clear()
    const again = mount({ subTabRequest: 'watching' })
    expect((await again.findByTestId('pulse-watched-scope')).textContent).toContain('Tech')
  })

  it('falls back to My holdings for a list that no longer exists', async () => {
    const { findByTestId, container } = mount({ subTabRequest: 'list:gone' })
    await findByTestId('holdings-brief')
    expect(tabButton(container, 'My holdings').getAttribute('aria-selected')).toBe('true')
  })

  it('the sidebar card follows the tab', async () => {
    const { findByTestId, container } = mount()
    await findByTestId('holdings-brief')
    expect((await findByTestId('sidebar-list')).textContent).toBe('default')
    fireEvent.click(tabButton(container, 'Dividends'))
    expect((await findByTestId('sidebar-list')).textContent).toBe('wl-div')
    expect(localStorage.getItem('intel-watchlist')).toBe('wl-div')
  })

  it('"New list" opens the watchlists workspace', async () => {
    const { findByTestId, getByRole } = mount()
    await findByTestId('holdings-brief')
    fireEvent.click(getByRole('button', { name: /New list/ }))
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/intel/monitors' })
  })

  it('closing the workspace navigates to /intel instead of going back in history', async () => {
    // Regression: Settings' close pushes /intel/monitors, so the entry behind it is /settings —
    // history.back() reopened Settings, and the two dialogs looped.
    const back = vi.spyOn(window.history, 'back')
    const { findByRole } = mount({ showMonitorsRoute: true })
    const dialog = await findByRole('dialog', { name: /Watchlists & monitors/ })
    mockNavigate.mockClear()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(back).not.toHaveBeenCalled()
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/intel', search: { sub: 'holdings' } })
    back.mockRestore()
  })

  it('jumps to a tab that has the story when focused from elsewhere, and unfolds it', async () => {
    requestIntelStoryFocus('s-nvda')
    const { findByTestId, container, findByText } = mount()
    await findByTestId('pulse-watched')
    expect(tabButton(container, 'Tech').getAttribute('aria-selected')).toBe('true')
    expect(await findByText('s-nvda summary')).toBeTruthy()
  })

  it('focuses a story handed off from the Investments tab', async () => {
    requestIntelStoryFocus('s-macro')
    const { findByText, container } = mount()
    expect(await findByText('Topics you follow')).toBeTruthy()
    expect(tabButton(container, 'Topics & macro').getAttribute('aria-selected')).toBe('true')
    expect(sessionStorage.getItem('intel-focus-story')).toBeNull()
  })

  it('"Mark all read" only marks the current tab', async () => {
    const { findByText } = mount()
    fireEvent.click(await findByText('Mark all read'))
    const patched = mockAuthFetch.mock.calls
      .filter(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')
      .map(([url]) => url)
    expect(patched).toEqual(['/api/intel/signals/s-msft'])
  })
})

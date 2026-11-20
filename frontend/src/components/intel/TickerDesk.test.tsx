// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import type { ReactNode } from 'react'

vi.mock('../../api/client', () => ({
  authFetch: vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => ({ matches: [] }),
}))

import { TickerDesk } from './TickerDesk'
import { groupByName } from './tickerGroups'
import { AssetNavProvider } from '../../state/assetNav'
import type { StoryArc } from './storyMeta'
import type { WatchPulseRow } from '../../types/watchPulse'

function Wrapper({ children }: { children: ReactNode }) {
  return <AssetNavProvider>{children}</AssetNavProvider>
}

function row(ticker: string, over: Partial<WatchPulseRow> = {}): WatchPulseRow {
  return {
    ticker,
    name: `${ticker} Co`,
    instrument_id: null,
    watchlist_ids: [],
    price: 100,
    currency: 'USD',
    day_pct: 1,
    rel_day_pct: null,
    range_pos: 0.5,
    from_high_pct: -5,
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
    position: null,
    move: null,
    forward_pe: null,
    trailing_pe: null,
    intent: null,
    thesis: null,
    in_buy_zone: null,
    material: [],
    trend: null,
    next_event: null,
    ...over,
  }
}

const FLAG = {
  kind: 'big_move',
  tone: 'bad' as const,
  weight: 40,
  severity: 'medium' as const,
  text: 'Down 9.0% today',
}

function story(id: string, ticker: string | undefined, over: Partial<StoryArc> = {}): StoryArc {
  return {
    id,
    type: 'asset',
    ticker,
    label: `${id} headline`,
    status: 'active',
    summary: `${id} summary`,
    action_items: [],
    timeline: [],
    created_at: '2026-09-26T00:00:00Z',
    updated_at: '2026-09-26T00:00:00Z',
    ...over,
  }
}

type DeskProps = Parameters<typeof TickerDesk>[0]

function deskProps(over: Partial<DeskProps> = {}): DeskProps {
  return {
    rows: [],
    loading: false,
    unpriced: 0,
    noun: { one: 'watched ticker', many: 'watched tickers' },
    sortKey: 'intel-pulse-sort',
    empty: 'empty',
    feed: [],
    archive: [],
    active: null,
    showArchive: false,
    onToggleArchive: vi.fn(),
    onRead: vi.fn(),
    onDismiss: vi.fn(),
    onRestore: vi.fn(),
    onDelete: vi.fn(),
    onMarkAllRead: vi.fn(),
    onFocusStory: vi.fn(),
    onEditAlerts: vi.fn(),
    onAskLucius: vi.fn(),
    registerRef: vi.fn(),
    ...over,
  }
}

function mount(over: Partial<DeskProps> = {}) {
  return render(<TickerDesk {...deskProps(over)} />, { wrapper: Wrapper })
}

afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe('TickerDesk', () => {
  it('sorts from its own menu, remembered under the tab’s key', () => {
    // Both rows carry a flag so neither folds away as "quiet".
    const rows = [
      row('AAA', { day_pct: 1, findings: [FLAG] }),
      row('BBB', { day_pct: -9, findings: [FLAG] }),
    ]
    const order = (c: HTMLElement) =>
      [...c.querySelectorAll('.intel-pulse-sym')].map((e) => e.textContent)
    const first = mount({ rows, sortKey: 'intel-pulse-sort-held' })
    expect(order(first.container)).toEqual(['AAA', 'BBB'])
    fireEvent.change(first.getByLabelText('Sort the list'), { target: { value: 'today' } })
    expect(order(first.container)).toEqual(['BBB', 'AAA'])
    expect(localStorage.getItem('intel-pulse-sort-held')).toBe('today')
    expect(localStorage.getItem('intel-pulse-sort')).toBeNull()
    first.unmount()
    expect(order(mount({ rows, sortKey: 'intel-pulse-sort-held' }).container)).toEqual([
      'BBB',
      'AAA',
    ])
  })

  it('tags the names you own on a list tab', () => {
    const rows = [
      row('MSFT', { findings: [FLAG], position: { value: 600, weight: 12, day_pl: 5 } }),
      row('AMD', { findings: [FLAG] }),
    ]
    const { container } = mount({ rows, markOwned: true })
    const owned = [...container.querySelectorAll('.intel-pulse-owned')].map(
      (e) => e.closest('.intel-pulse-row')?.querySelector('.intel-pulse-sym')?.textContent,
    )
    expect(owned).toEqual(['MSFT'])
  })

  it('has no sort menu for a single name', () => {
    const { queryByLabelText } = mount({ rows: [row('AAA')] })
    expect(queryByLabelText('Sort the list')).toBeNull()
  })

  it('counts unpriced names only once prices have landed', () => {
    const { queryByText, rerender } = mount({ loading: true, unpriced: 2 })
    expect(queryByText(/without a live price/)).toBeNull()
    rerender(
      <Wrapper>
        <TickerDesk {...deskProps({ unpriced: 2 })} />
      </Wrapper>,
    )
    expect(queryByText('2 watched tickers without a live price.')).toBeTruthy()
  })

  it('folds each ticker’s news-scan story under its pulse row', () => {
    const { container, getByText } = mount({
      rows: [row('AMD', { findings: [FLAG] })],
      feed: [story('news', 'AMD', { label: 'AMD guidance cut' })],
    })
    const amdRow = container.querySelector('.intel-desk-row')!
    expect(amdRow.textContent).toContain('Down 9.0% today')
    expect(amdRow.textContent).toContain('AMD guidance cut')
    expect(getByText('AMD guidance cut').closest('button')?.getAttribute('aria-expanded')).toBe(
      'false',
    )
  })

  it('never repeats the flags as a headline: a price-signal-only ticker reads "Price signal · N flags"', () => {
    const { container } = mount({
      rows: [row('AMD', { findings: [FLAG, { ...FLAG, kind: 'lost_200d', text: 'Fell below' }] })],
      feed: [story('sig', 'AMD', { watch: true, label: 'AMD: Down 9.0% today' })],
    })
    const line = container.querySelector('.intel-desk-line')!
    expect(line.textContent).toContain('Price signal')
    expect(line.textContent).toContain('2 flags')
    expect(line.textContent).not.toContain('AMD: Down 9.0% today')
  })

  it('leads with the news story even when the price signal is more urgent', () => {
    const { container } = mount({
      rows: [row('AMD', { findings: [FLAG] })],
      feed: [
        story('sig', 'AMD', {
          watch: true,
          status: 'action_needed',
          updated_at: new Date().toISOString(), // an older one reads "Needs recheck"
        }),
        story('news', 'AMD', { label: 'AMD wins contract', status: 'monitoring' }),
      ],
    })
    const line = container.querySelector('.intel-desk-line')!
    expect(line.textContent).toContain('Needs attention') // the ticker's most urgent status
    expect(line.textContent).toContain('AMD wins contract')
    expect(line.textContent).toContain('+1 more')
  })

  it('keeps the caller’s sort when order is "given"', () => {
    const rows = [row('AAA'), row('BBB')]
    const feed = [story('b', 'BBB', { status: 'action_needed' })]
    expect(groupByName(rows, feed, { order: 'urgency' }).groups.map((g) => g.key)).toEqual([
      'BBB',
      'AAA',
    ])
    expect(groupByName(rows, feed, { order: 'given' }).groups.map((g) => g.key)).toEqual([
      'AAA',
      'BBB',
    ])
  })

  it('shows when the news was last scanned', () => {
    const { getByText, rerender } = mount({ newsScannedAt: null })
    expect(getByText('News not scanned yet')).toBeTruthy()
    rerender(
      <Wrapper>
        <TickerDesk
          {...deskProps({ newsScannedAt: new Date(Date.now() - 2 * 3600_000).toISOString() })}
        />
      </Wrapper>,
    )
    expect(getByText('News scanned 2h ago')).toBeTruthy()
  })

  it('unfolds a ticker when one of its stories becomes active', () => {
    const { getByText, rerender } = mount({
      rows: [row('AMD')],
      feed: [story('news', 'AMD')],
    })
    expect(() => getByText('news summary')).toThrow()
    rerender(
      <Wrapper>
        <TickerDesk
          {...deskProps({ rows: [row('AMD')], feed: [story('news', 'AMD')], active: 'news' })}
        />
      </Wrapper>,
    )
    expect(getByText('news summary')).toBeTruthy()
    fireEvent.click(getByText('news headline', { selector: '.intel-desk-line-title' }))
    expect(() => getByText('news summary')).toThrow()
  })
  it('leads a ticker’s line with its explained alert when there is one', () => {
    const { container } = mount({
      rows: [row('AMD', { findings: [FLAG] })],
      feed: [
        story('sig', 'AMD', {
          watch: true,
          explained: {
            trigger: 'company_move',
            what_happened: 'AMD fell 9.0%, about 11× its usual move.',
            why: 'w',
            what_it_means: 'm',
            worth_weighing: [],
            sources: [],
            at: '',
          },
        }),
      ],
    })
    const line = container.querySelector('.intel-desk-line')!
    expect(line.textContent).toContain('Explained')
    expect(line.textContent).toContain('AMD fell 9.0%, about 11× its usual move.')
  })
  it('labels a price-signal line “Lucius’s read” when the story carries one', () => {
    const { container } = mount({
      rows: [row('AMD', { findings: [FLAG] })],
      feed: [
        story('sig', 'AMD', {
          watch: true,
          lucius_read: { text: 'r', watch_for: [], sources: [], at: '' },
        }),
      ],
    })
    const type = container.querySelector('.intel-desk-line-type')!
    expect(type.textContent).toBe('Lucius’s read')
    expect(type.classList.contains('intel-desk-line-type--read')).toBe(true)
  })
  it('counts a decision only for a recent story with a real action (the signal queue)', () => {
    const { container } = mount({
      rows: [row('AMD'), row('MU')],
      feed: [
        story('old', 'AMD', {
          status: 'action_needed',
          action_items: ['Trim below 20%'],
          updated_at: '2026-01-01T00:00:00Z',
        }),
        story('quiet', 'MU', {
          status: 'action_needed',
          action_items: ['No action needed'],
          updated_at: new Date().toISOString(),
        }),
      ],
    })
    const bar = container.querySelector('.intel-desk-status')!.textContent ?? ''
    expect(bar).toContain('1 needs recheck')
    expect(bar).toContain('1 monitoring')
    expect(bar).toContain('Nothing needs a decision')
    expect(bar).not.toMatch(/needs attention/)
  })
})

import { describe, expect, it } from 'vitest'
import {
  buildTabs,
  listTickers,
  partitionByTab,
  resolveTabKey,
  tabOfStory,
  type IntelTabKey,
} from './intelTabs'
import type { StoryArc, StoryType } from './storyMeta'
import type { Watchlist, WatchlistItem } from './WatchlistsSection'

const list = (id: string, name: string, order: number, auto = false): Watchlist => ({
  id,
  name,
  description: null,
  is_auto_sync: auto,
  kind: auto ? 'auto' : 'manual',
  order,
})

const LISTS = [
  list('wl-div', 'Dividends', 2),
  list('default', 'My holdings', 0, true),
  list('wl-tech', 'Tech', 1),
]

function item(id: string, type: WatchlistItem['type'], value: string, lists: string[]) {
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
  } as WatchlistItem
}

const ITEMS = [
  item('w1', 'ticker', 'NVDA', ['wl-tech']),
  item('w2', 'ticker', 'MSFT', ['default', 'wl-tech']),
  item('w3', 'ticker', 'AMD', ['wl-tech', 'wl-div']),
  item('w4', 'topic', 'AI capex', ['wl-div']),
]

function story(id: string, type: StoryType, ticker?: string, label = id): StoryArc {
  return {
    id,
    type,
    ticker,
    label,
    status: 'active',
    summary: '',
    action_items: [],
    timeline: [],
    created_at: '',
    updated_at: '',
  }
}

const HELD = new Set(['MSFT'])
const WATCHED = new Set(['NVDA', 'AMD'])
const ids = (xs: StoryArc[] | undefined) => (xs ?? []).map((s) => s.id)

describe('buildTabs', () => {
  it('is My holdings, then Topics & macro, then your lists in their order', () => {
    expect(buildTabs(LISTS).map((t) => t.key)).toEqual([
      'holdings',
      'macro',
      'list:wl-tech',
      'list:wl-div',
    ])
  })

  it('is just My holdings and Macro without lists of your own', () => {
    expect(buildTabs([LISTS[1]!]).map((t) => t.label)).toEqual(['My holdings', 'Topics & macro'])
  })
})

describe('resolveTabKey', () => {
  it('keeps fixed tabs and lists that exist', () => {
    expect(resolveTabKey('holdings', LISTS)).toBe('holdings')
    expect(resolveTabKey('macro', LISTS)).toBe('macro')
    expect(resolveTabKey('list:wl-div', LISTS)).toBe('list:wl-div')
  })

  it('opens Copy Trading by its key, and by its old key from before the rename', () => {
    expect(resolveTabKey('copy-trading', LISTS)).toBe('copy-trading')
    expect(resolveTabKey('portfolios', LISTS)).toBe('copy-trading')
  })

  it('opens the last list for the legacy "watching", else the first', () => {
    expect(resolveTabKey('watching', LISTS, 'wl-div')).toBe('list:wl-div')
    expect(resolveTabKey('watching', LISTS, 'gone')).toBe('list:wl-tech')
    expect(resolveTabKey('watching', [LISTS[1]!])).toBe('holdings')
  })

  it('falls back to My holdings for a deleted list, and rejects what is not a tab', () => {
    expect(resolveTabKey('list:gone', LISTS)).toBe('holdings')
    expect(resolveTabKey('list:default', LISTS)).toBe('holdings') // the auto list is My holdings
    expect(resolveTabKey('nonsense', LISTS)).toBeNull()
    expect(resolveTabKey(null, LISTS)).toBeNull()
  })
})

describe('partitionByTab', () => {
  const byTab = partitionByTab(
    [
      story('msft', 'asset', 'MSFT'),
      story('amd', 'asset', 'AMD'),
      story('nvda', 'asset', 'nvda'),
      story('risk', 'risk'),
      story('topic', 'topic', undefined, 'AI capex: hyperscalers raise spend'),
      story('fed', 'macro'),
      story('pltr', 'opportunity', 'PLTR'),
    ],
    HELD,
    WATCHED,
    listTickers(LISTS, ITEMS),
  )

  it('keeps My holdings as before: held names and portfolio-wide risk', () => {
    expect(ids(byTab.holdings)).toEqual(['msft', 'risk'])
  })

  it('gives a list every story on its tickers, owned or not', () => {
    expect(ids(byTab['list:wl-tech'])).toEqual(['msft', 'amd', 'nvda'])
  })

  it('shows a ticker on two lists on both — and never a topic on a list', () => {
    expect(ids(byTab['list:wl-div'])).toEqual(['amd'])
  })

  it('puts topics, macro and what no list claims on Topics & macro', () => {
    expect(ids(byTab.macro)).toEqual(['topic', 'fed', 'pltr'])
  })

  it('opens the tab on screen when it has the story, else the first that does', () => {
    const tabs = buildTabs(LISTS)
    const on = (id: string, current: IntelTabKey) => tabOfStory(id, current, byTab, tabs)
    expect(on('amd', 'list:wl-div')).toBe('list:wl-div')
    expect(on('amd', 'holdings')).toBe('list:wl-tech')
    expect(on('msft', 'macro')).toBe('holdings')
    expect(on('nope', 'macro')).toBeNull()
  })
})

describe('listTickers', () => {
  it('collects each list’s tickers — topics belong to none', () => {
    const t = listTickers(LISTS, [...ITEMS, item('w9', 'topic', 'Fed', ['wl-div'])])
    expect([...t.get('wl-tech')!]).toEqual(['NVDA', 'MSFT', 'AMD'])
    expect([...t.get('wl-div')!]).toEqual(['AMD'])
    expect(t.has('default')).toBe(false)
  })
})

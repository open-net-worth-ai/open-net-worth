// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { Watchlist, WatchlistItem } from './WatchlistsSection'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

// AssetNavProvider uses useNavigate + useRouterState internally — mock both.
const mockNavigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useRouterState: () => ({ matches: [] }),
}))

import { WatchlistsSection } from './WatchlistsSection'
import { AssetNavProvider } from '../../state/assetNav'

// AssetRow contains an AssetLink which requires the provider.
function Wrapper({ children }: { children: ReactNode }) {
  return <AssetNavProvider>{children}</AssetNavProvider>
}

const DEFAULT_LIST: Watchlist = {
  id: 'default',
  name: 'My holdings',
  description: '',
  is_auto_sync: true,
  kind: 'manual',
  order: 0,
  muted: false,
}

const MANUAL_LIST: Watchlist = {
  id: 'my-watch',
  name: 'My watchlist',
  description: '',
  is_auto_sync: false,
  kind: 'manual',
  order: 1,
  muted: false,
}

function baseProps(items: WatchlistItem[] = []) {
  return {
    watchlists: [DEFAULT_LIST],
    items,
    alerts: [],
    activeListId: 'default',
    tickerMeta: {},
    tickerCcy: {},
    ownedTickers: new Set<string>(),
    onEditAlerts: vi.fn(),
    onManageAll: vi.fn(),
    onRefresh: vi.fn(),
  }
}

function manualProps(items: WatchlistItem[] = []) {
  return {
    watchlists: [DEFAULT_LIST, MANUAL_LIST],
    items,
    alerts: [],
    activeListId: 'my-watch',
    tickerMeta: {},
    tickerCcy: {},
    ownedTickers: new Set<string>(),
    onEditAlerts: vi.fn(),
    onManageAll: vi.fn(),
    onRefresh: vi.fn(),
  }
}

afterEach(cleanup)
beforeEach(() => mockAuthFetch.mockReset())

function makeItem(overrides: Partial<WatchlistItem> = {}): WatchlistItem {
  return {
    id: 'item-1',
    type: 'ticker',
    value: 'AAPL',
    label: 'Apple',
    asset_class: 'equity',
    monitor_news: true,
    added_by: 'user',
    note: '',
    watchlist_ids: ['default'],
    ...overrides,
  }
}

describe('WatchlistsSection — add ticker (ADR 0018: auto-sync list is locked)', () => {
  it('hides the add-ticker input for the auto-sync (My holdings) list', () => {
    const { queryByPlaceholderText } = render(<WatchlistsSection {...baseProps()} />, {
      wrapper: Wrapper,
    })
    expect(queryByPlaceholderText(/Add ticker/i)).toBeNull()
  })

  it('shows the add-ticker input for a manual watchlist', () => {
    const { getByPlaceholderText } = render(<WatchlistsSection {...manualProps()} />, {
      wrapper: Wrapper,
    })
    expect(getByPlaceholderText(/Add ticker/i)).toBeTruthy()
  })

  it('POSTs the ticker to a manual list and refreshes', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      status: 201,
      json: () => Promise.resolve({ id: 'new-1', watchlist_ids: ['my-watch'] }),
    })
    const props = manualProps()
    const { getByPlaceholderText, getByText } = render(<WatchlistsSection {...props} />, {
      wrapper: Wrapper,
    })
    fireEvent.change(getByPlaceholderText(/Add ticker/i), { target: { value: 'nvda' } })
    fireEvent.click(getByText('Add'))
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled())
    const call = mockAuthFetch.mock.calls[0] as [string, { method: string; body: string }]
    expect(call[0]).toBe('/api/watchlist/items')
    expect(call[1].method).toBe('POST')
    const body = JSON.parse(call[1].body)
    expect(body).toMatchObject({ type: 'ticker', value: 'NVDA', watchlist: 'my-watch' })
    await waitFor(() => expect(props.onRefresh).toHaveBeenCalled())
  })

  it('surfaces a 409 (already in this watchlist) and does NOT refresh', async () => {
    mockAuthFetch.mockResolvedValue({ ok: false, status: 409, json: () => Promise.resolve({}) })
    const props = manualProps()
    const { getByPlaceholderText, getByText, findByText } = render(
      <WatchlistsSection {...props} />,
      { wrapper: Wrapper },
    )
    fireEvent.change(getByPlaceholderText(/Add ticker/i), { target: { value: 'aapl' } })
    fireEvent.click(getByText('Add'))
    expect(await findByText(/already in this watchlist/i)).toBeTruthy()
    expect(props.onRefresh).not.toHaveBeenCalled()
  })

  it('disables Add when the input is empty', () => {
    const { getByText } = render(<WatchlistsSection {...manualProps()} />, { wrapper: Wrapper })
    expect((getByText('Add') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('WatchlistsSection — watchlist_ids membership filtering', () => {
  it('renders items that belong to the active watchlist via watchlist_ids', () => {
    const item = makeItem({ watchlist_ids: ['default'] })
    const { getByText } = render(<WatchlistsSection {...baseProps([item])} />, { wrapper: Wrapper })
    expect(getByText('AAPL')).toBeTruthy()
  })

  it('does not render items that do not belong to the active watchlist', () => {
    const item = makeItem({ watchlist_ids: ['other-list'] })
    const { queryByText } = render(<WatchlistsSection {...baseProps([item])} />, {
      wrapper: Wrapper,
    })
    // AAPL should not be shown in the default watchlist.
    expect(queryByText('AAPL')).toBeNull()
  })

  it('names the list the tab is on', () => {
    const { getByText } = render(<WatchlistsSection {...manualProps()} />, { wrapper: Wrapper })
    expect(getByText(MANUAL_LIST.name, { selector: '.intel-wlc-head-title' })).toBeTruthy()
  })
})

describe('WatchlistsSection — price precision', () => {
  // The digits come from the ITEM's asset class: the price feed (tickerMeta) never carries one,
  // so reading it there showed every equity at crypto precision.
  it('shows an equity at two decimals and a crypto at its own precision', () => {
    const items = [
      makeItem({ id: 'eq', value: 'AAPL', asset_class: 'equity' }),
      makeItem({ id: 'cx', value: 'SOL-USD', label: 'Solana', asset_class: 'crypto' }),
    ]
    const tickerMeta = { AAPL: { price: 187.4567 }, 'SOL-USD': { price: 142.1234 } }
    const { container } = render(
      <WatchlistsSection {...baseProps(items)} tickerMeta={tickerMeta} />,
      { wrapper: Wrapper },
    )
    const prices = [...container.querySelectorAll('.intel-wlc-row-price')].map(
      (el) => el.textContent,
    )
    expect(prices.some((t) => t?.includes('187.46') && !t.includes('187.4567'))).toBe(true)
    expect(prices.some((t) => t?.includes('142.1234'))).toBe(true)
  })
})

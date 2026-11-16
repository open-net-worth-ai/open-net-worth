// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type * as Api from '../../../api/paperTrading'

// The holdings' name cells can open an asset's page; the test has no router behind them.
vi.mock('../../../state/assetNav', () => ({ useAssetNav: () => ({ openAsset: () => {} }) }))

const card = {
  id: 'ppf_1',
  name: 'Buy-zone strategy',
  status: 'active',
  home_currency: 'USD',
  benchmark: 'SPY',
  origin: { kind: 'cash', ref: null, label: 'Cash' },
  created_by: 'owner',
  source_label: 'Cash',
  run_no: 1,
  started_at: '2026-11-01T00:00:00Z',
  archived_at: null,
  day_count: 15,
  value: 103000,
  cash_home: 96250,
  return_pct: 0.03,
  benchmark_return_pct: 0.01,
  open_orders: 0,
  sparkline: [],
  holdings_count: 1,
  weights: [{ symbol: 'NVDA', weight: 0.04 }],
  recent_trades: [
    { label: 'Bought 20 NVDA at 187.46', side: 'buy', symbol: 'NVDA', at: '2026-11-15T00:00:00Z' },
  ],
}
const holding = {
  asset_key: 'inst:nvda',
  kind: 'listed',
  kind_label: 'Stocks & ETFs',
  symbol: 'NVDA',
  name: 'NVIDIA Corporation',
  currency: 'USD',
  unit: null,
  quantity: 20,
  price: 187.42,
  price_currency: 'USD',
  basis: 'quote',
  basis_label: null,
  value_home: 3748.4,
  cost_home: 3749.15,
  pnl_home: -0.75,
  pnl_pct: -0.0002,
  frozen: null,
  marked_on: null,
  weight: 0.037,
}
const page = {
  portfolio: card,
  figures: {
    value: 103000,
    return_pct: 0.03,
    benchmark_return_pct: 0.01,
    your_return_pct: null,
    cash_home: 96250,
    cash: [],
    open_orders: 0,
    unrealized_home: 0,
    realized_home: 0,
  },
  holdings: [holding],
  holding_groups: [],
  orders: [],
  activity: [
    {
      id: 'e1',
      at: '2026-11-15T00:00:00Z',
      kind: 'buy',
      symbol: 'NVDA',
      name: 'NVIDIA',
      note: 'On the pullback',
      label: 'Bought 20 NVDA at 187.46',
      opening: false,
      thesis: null,
    },
  ],
}

const api = vi.hoisted(() => ({
  list: vi.fn(),
  place: vi.fn(),
  preview: vi.fn(),
  search: vi.fn(),
}))
vi.mock('../../../api/paperTrading', async (importOriginal) => {
  const real = await importOriginal<typeof Api>()
  return {
    ...real,
    fetchPaperList: api.list,
    fetchPaperPage: () => Promise.resolve(page),
    fetchPaperPerformance: () =>
      Promise.resolve({
        series: [],
        return_pct: 0.03,
        benchmark: { symbol: 'SPY', return_pct: 0.01 },
        yours_return_pct: null,
      }),
    fetchPaperDefaults: () =>
      Promise.resolve({
        base_currency: 'USD',
        default_cash: 240000,
        benchmark: 'SPY',
        followed: [],
        max_active: 20,
        active_count: 1,
        kinds: {},
      }),
    fetchPaperSearch: api.search,
    previewPaperOrder: api.preview,
    placePaperOrder: api.place,
  }
})

import { PaperTab } from './PaperTab'
import { invalidateQueryCache } from '../../../state/queryCache'

describe('Paper Trading', () => {
  beforeEach(() => {
    invalidateQueryCache(() => true)
    api.list.mockResolvedValue({ portfolios: [card], archived: [] })
    api.search.mockResolvedValue([
      {
        asset_key: 'inst:nvda',
        kind: 'listed',
        kind_label: 'Stocks & ETFs',
        symbol: 'NVDA',
        name: 'NVIDIA Corporation',
        currency: 'USD',
        unit: null,
        exchange: null,
      },
    ])
    api.preview.mockResolvedValue({
      decision: 'fill',
      reason: null,
      price: 187.46,
      basis: 'quote',
      basis_label: null,
      source: 'Yahoo',
      quote_as_of: null,
      session_open: true,
      next_line: 'Fills now at about 187.46.',
      estimate: { quantity: 5, total: 937.3, total_home: 937.3, currency: 'USD', commission: 0 },
      cash_after_home: 95312.7,
      thesis: null,
      warnings: [],
    })
    api.place.mockResolvedValue({ order: {}, toast: 'Paper buy filled: 5 NVDA at 187.46' })
  })
  afterEach(cleanup)

  it('shows an empty state with a way to start when there is no paper portfolio', async () => {
    api.list.mockResolvedValue({ portfolios: [], archived: [] })
    render(<PaperTab />)
    expect(await screen.findByText('Try an idea before you put money in')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Create manually' })).toBeTruthy()
  })

  it('lists each paper portfolio as a card carrying the Paper tag', async () => {
    render(<PaperTab />)
    const open = await screen.findByRole('button', { name: 'Open Buy-zone strategy' })
    expect(within(open).getByText('Paper')).toBeTruthy()
    expect(within(open).getAllByText('NVDA').length).toBeGreaterThan(0)
  })

  it('opens a portfolio, looks a symbol up, previews and places a paper buy', async () => {
    render(<PaperTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open Buy-zone strategy' }))
    expect(await screen.findByText('Paper value')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Paper trade' }))
    const symbol = screen.getByLabelText('Symbol')
    fireEvent.change(symbol, { target: { value: 'nvda' } })
    await act(async () => {
      fireEvent.blur(symbol)
    })
    expect(api.search).toHaveBeenCalledWith('NVDA')
    expect(await screen.findByText('NVIDIA Corporation · Stocks & ETFs')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Shares'), { target: { value: '5' } })
    expect(await screen.findByText('Fills now at about 187.46.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Place paper buy' }))
    await waitFor(() => expect(api.place).toHaveBeenCalledTimes(1))
    const [, asset, draft] = api.place.mock.calls[0] ?? []
    expect(asset.asset_key).toBe('inst:nvda')
    expect(draft).toMatchObject({ side: 'buy', quantity: 5, order_type: 'market' })
    expect(await screen.findByText('Paper buy filled: 5 NVDA at 187.46')).toBeTruthy()
  })

  it('says why when the server refuses an order, and keeps the button off', async () => {
    api.preview.mockResolvedValue({
      decision: 'reject',
      reason: 'Only 96,250.00 USD of paper cash is free.',
      price: null,
      basis: null,
      basis_label: null,
      source: null,
      quote_as_of: null,
      session_open: true,
      next_line: null,
      estimate: null,
      cash_after_home: null,
      thesis: null,
      warnings: [],
    })
    render(<PaperTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open Buy-zone strategy' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Paper trade' }))
    const symbol = screen.getByLabelText('Symbol')
    fireEvent.change(symbol, { target: { value: 'NVDA' } })
    await act(async () => {
      fireEvent.blur(symbol)
    })
    fireEvent.change(screen.getByLabelText('Shares'), { target: { value: '10000' } })
    expect(await screen.findByText('Only 96,250.00 USD of paper cash is free.')).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: 'Place paper buy' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })
})

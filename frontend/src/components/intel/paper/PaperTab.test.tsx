// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type * as Api from '../../../api/paperTrading'

// The holdings' name cells can open an asset's page; the test has no router behind them.
vi.mock('../../../state/assetNav', () => ({ useAssetNav: () => ({ openAsset: () => {} }) }))
// The address bar: the open portfolio lives in `?paper=`, so navigating changes what useSearch
// returns (as Copy Trading's tests do it).
const router = vi.hoisted(() => {
  let search: Record<string, unknown> = {}
  const subs = new Set<() => void>()
  return {
    get: () => search,
    subscribe: (f: () => void) => (subs.add(f), () => void subs.delete(f)),
    navigate(o: { search?: unknown }) {
      search =
        typeof o.search === 'function'
          ? (o.search as (p: Record<string, unknown>) => Record<string, unknown>)(search)
          : ((o.search as Record<string, unknown>) ?? {})
      subs.forEach((f) => f())
    },
    reset: () => void (search = {}),
  }
})
vi.mock('@tanstack/react-router', async () => {
  const { useSyncExternalStore } = await import('react')
  return {
    useNavigate: () => router.navigate,
    useSearch: () => useSyncExternalStore(router.subscribe, router.get),
  }
})

const card = {
  id: 'ppf_1',
  name: 'Buy-zone strategy',
  status: 'active',
  home_currency: 'USD',
  benchmark: 'SPY',
  origin: { kind: 'cash', ref: null, label: 'Cash' },
  created_by: 'owner',
  source: null,
  source_label: '',
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
  mandate: null,
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
  mandate: null,
  autopilot: {
    mode: 'manual',
    cadence: 'daily',
    instructions: '',
    next_run_at: null,
    running: false,
    review_soon: false,
    paused_reason: null,
    runs: [],
  },
}

const api = vi.hoisted(() => ({
  list: vi.fn(),
  place: vi.fn(),
  preview: vi.fn(),
  search: vi.fn(),
  update: vi.fn(),
  autopilot: vi.fn(),
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
    updatePaperPortfolio: api.update,
    setPaperAutopilot: api.autopilot,
  }
})

import { PaperTab } from './PaperTab'
import { invalidateQueryCache } from '../../../state/queryCache'

describe('Paper Trading', () => {
  beforeEach(() => {
    invalidateQueryCache(() => true)
    router.reset()
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
    api.update.mockResolvedValue({})
    api.place.mockResolvedValue({ order: {}, toast: 'Paper buy filled: 5 NVDA at 187.46' })
  })
  afterEach(cleanup)

  it('shows an empty state with a way to start when there is no paper portfolio', async () => {
    api.list.mockResolvedValue({ portfolios: [], archived: [] })
    render(<PaperTab />)
    expect(await screen.findByText('Try an idea before you put money in')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Create manually' })).toBeTruthy()
  })

  it('lists each paper portfolio as a card, with no Paper chip (the tab says it)', async () => {
    render(<PaperTab />)
    const open = await screen.findByRole('button', { name: 'Open Buy-zone strategy' })
    expect(within(open).queryByText('Paper')).toBeNull()
    expect(within(open).getAllByText('NVDA').length).toBeGreaterThan(0)
  })

  it('searches the portfolios, and creates from one New menu as Automations does', async () => {
    api.list.mockResolvedValue({
      portfolios: [card, { ...card, id: 'ppf_2', name: 'Dividend run', weights: [] }],
      archived: [],
    })
    const onAskLucius = vi.fn()
    render(<PaperTab onAskLucius={onAskLucius} />)
    await screen.findByRole('button', { name: 'Open Buy-zone strategy' })
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search paper portfolios' }), {
      target: { value: 'dividend' },
    })
    expect(screen.queryByRole('button', { name: 'Open Buy-zone strategy' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Open Dividend run' })).toBeTruthy()
    // A holding's symbol finds its portfolio too.
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search paper portfolios' }), {
      target: { value: 'nvda' },
    })
    expect(screen.getByRole('button', { name: 'Open Buy-zone strategy' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Create manually' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'New paper portfolio' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: /Create with Lucius/ }))
    expect(onAskLucius).toHaveBeenCalled()
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
  it('renames a portfolio from its Edit dialog', async () => {
    render(<PaperTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open Buy-zone strategy' }))
    fireEvent.click(
      await screen.findByRole('button', { name: 'More actions for Buy-zone strategy' }),
    )
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }))
    const field = screen.getByLabelText('Name')
    fireEvent.change(field, { target: { value: '  Dip buys  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith('ppf_1', { name: 'Dip buys' }))
  })

  it('draws holdings by sector, and a picked sector lists only its holdings', async () => {
    const coin = { ...holding, asset_key: 'inst:coin', symbol: 'ACOIN', name: 'Acme Coin' }
    Object.assign(page, {
      holdings: [
        { ...holding, sector: 'Technology' },
        { ...coin, sector: 'Crypto' },
      ],
      sectors: [
        { sector: 'Technology', weight: 0.6, holdings: [] },
        { sector: 'Crypto', weight: 0.4, holdings: [] },
      ],
    })
    try {
      render(<PaperTab />)
      fireEvent.click(await screen.findByRole('button', { name: 'Open Buy-zone strategy' }))
      expect(await screen.findByText('By sector')).toBeTruthy()
      expect(screen.getByText('Acme Coin')).toBeTruthy()
      fireEvent.click(screen.getByRole('button', { name: 'Technology, 60.0%' }))
      await waitFor(() => expect(screen.queryByText('Acme Coin')).toBeNull())
      expect(screen.getByText('NVIDIA Corporation')).toBeTruthy()
    } finally {
      Object.assign(page, { holdings: [holding], sectors: undefined })
    }
  })

  it('turns autopilot on only once there is a mandate', async () => {
    api.autopilot.mockResolvedValue({})
    render(<PaperTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open Buy-zone strategy' }))
    fireEvent.click(await screen.findByRole('tab', { name: 'Autopilot' }))
    expect(await screen.findByRole('button', { name: 'Set a mandate' })).toBeTruthy()
    expect((screen.getByLabelText('Turn autopilot on') as HTMLInputElement).disabled).toBe(true)
    Object.assign(card, { mandate: { goal: 'Beat SPY', max_position_pct: 20 } })
    try {
      cleanup()
      invalidateQueryCache(() => true)
      render(<PaperTab />)
      fireEvent.click(await screen.findByRole('tab', { name: 'Autopilot' }))
      expect(screen.queryByRole('button', { name: 'Set a mandate' })).toBeNull()
      expect(screen.getByText('At most 20% in one holding')).toBeTruthy()
      fireEvent.click(screen.getByLabelText('Turn autopilot on'))
      await waitFor(() =>
        expect(api.autopilot).toHaveBeenCalledWith('ppf_1', {
          mode: 'auto',
        }),
      )
    } finally {
      Object.assign(card, { mandate: null })
    }
  })

  it('sets a mandate from the Edit dialog, blank fields meaning no limit', async () => {
    render(<PaperTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open Buy-zone strategy' }))
    fireEvent.click(await screen.findByRole('tab', { name: 'Mandate' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Set a mandate' }))
    fireEvent.change(screen.getByLabelText('Goal'), { target: { value: ' Beat SPY ' } })
    fireEvent.change(screen.getByLabelText(/Max per holding/), { target: { value: '15' } })
    fireEvent.change(screen.getByLabelText(/May buy only/), { target: { value: 'acme, nvda' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.update).toHaveBeenCalledWith('ppf_1', {
        mandate: { goal: 'Beat SPY', max_position_pct: 15, universe: ['ACME', 'NVDA'] },
      }),
    )
  })

  it('says which mandate rule a buy breaks, and places it only as "Place anyway"', async () => {
    api.preview.mockResolvedValue({
      ...(await api.preview()),
      mandate_breaches: [
        {
          rule: 'max_position_pct',
          message: 'NVDA would be 22% of the portfolio; the mandate allows 15%.',
        },
      ],
    })
    render(<PaperTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open Buy-zone strategy' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Paper trade' }))
    const symbol = screen.getByLabelText('Symbol')
    fireEvent.change(symbol, { target: { value: 'NVDA' } })
    await act(async () => {
      fireEvent.blur(symbol)
    })
    fireEvent.change(screen.getByLabelText('Shares'), { target: { value: '120' } })
    expect(
      await screen.findByText('NVDA would be 22% of the portfolio; the mandate allows 15%.'),
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Place anyway' }))
    await waitFor(() =>
      expect(api.place).toHaveBeenLastCalledWith(
        'ppf_1',
        expect.anything(),
        expect.objectContaining({ quantity: 120, override_mandate: true }),
      ),
    )
  })
})

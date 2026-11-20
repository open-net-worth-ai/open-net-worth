// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { overrideFlagsForTests } from '../../hooks/useFlag'
import { AssetDetailView } from './AssetDetailView'
import type { AssetDetail, TradingAgentsInput } from '../../types/assetDetail'
import { makeStatus, makeSummary } from './tradingAgentsFixtures'

vi.mock('../../hooks/useAssetDetail', () => ({ useAssetDetail: vi.fn() }))
import { useAssetDetail } from '../../hooks/useAssetDetail'

const mockUseAssetDetail = useAssetDetail as ReturnType<typeof vi.fn>

vi.mock('../../hooks/useAnalystConsensus', () => ({
  useAnalystConsensus: () => ({ data: null, loading: false }),
}))
vi.mock('../../lucius/useScreenContract', () => ({
  useRegisterHandles: vi.fn(),
  useRegisterScreenPrompts: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useSearch: () => ({}),
}))
vi.mock('./AssetPriceChart', () => ({ AssetPriceChart: () => <div>Price chart</div> }))
vi.mock('./QuoteStats', () => ({ QuoteStats: () => null }))
vi.mock('./MarketStatusChip', () => ({ MarketStatusChip: () => null }))
vi.mock('./KeyFactsCard', () => ({ KeyFactsCard: () => null }))
vi.mock('./PeersCard', () => ({ PeersCard: () => null }))
vi.mock('./TradingAgentsCard', () => ({
  TradingAgentsCard: ({
    focus,
    takeView,
  }: {
    focus: { runId: string } | null
    takeView: { view: string | null } | null
  }) => (
    <div>
      TradingAgents card {focus?.runId ?? 'unfocused'} view {takeView?.view ?? 'none'}
    </div>
  ),
}))

const ta = vi.hoisted(() => ({ rows: [] as unknown[] }))
vi.mock('../../hooks/useTradingAgents', () => ({
  TaError: class TaError extends Error {},
  startTaRun: vi.fn(),
  cancelTaRun: vi.fn(),
  useTaStatus: () => ({ data: makeStatus(), error: null, initialLoading: false }),
  useTaRuns: () => ({ data: { runs: ta.rows, total: ta.rows.length }, error: null }),
  useTaRun: () => ({ data: undefined, error: null }),
  useTaEstimate: () => ({ data: undefined, error: null }),
  useTaContextPreview: () => ({ data: undefined, error: null }),
}))

// Minimal valid AssetDetail with a populated take (`portfolio_impact`).
function assetDetail(overrides: Partial<AssetDetail> = {}): AssetDetail {
  return {
    instrument_id: 'acme',
    asset: {
      name: 'Acme Corp',
      symbol: 'ACME',
      ticker: 'ACME',
      exchange: 'ETR',
      mic: null,
      country: 'DE',
      asset_class: 'equity',
      currency: 'EUR',
      description: null,
    },
    position: null,
    price: {
      last: 30,
      change_abs: 0.5,
      change_pct: 1.7,
      currency: 'EUR',
      as_of: '2026-08-29T12:00:00Z',
      provider: 'yahoo',
      state: 'live',
      market: null,
    },
    news: [],
    key_issues: [],
    facts: {
      ceo: null,
      employees: null,
      ipo_date: null,
      industry: null,
      sector: null,
      exchange: null,
      country: null,
      unverified: true,
    },
    crypto_facts: null,
    peers: [],
    watchlist: { following: false, price_alerts: [] },
    last_refreshed_at: '2026-08-29T12:00:00Z',
    has_ticker: true,
    portfolio_impact: 'Position weight 4.2%. Bull case rests on order growth reaccelerating.',
    has_narrative: true,
    ...overrides,
  } as AssetDetail
}

const today = () => new Date().toISOString().slice(0, 10)
const INPUT: TradingAgentsInput = {
  run_id: 'tar_000000000001',
  signal: 'Overweight',
  trade_date: today(),
  age_days: 3,
}

function mockHook(input: TradingAgentsInput | null, over: Partial<AssetDetail> = {}) {
  mockUseAssetDetail.mockReturnValue({
    data: assetDetail({ trading_agents_input: input, ...over }),
    loading: false,
    refreshing: false,
    backgroundRefreshing: false,
    error: false,
    refresh: vi.fn(),
    refetch: vi.fn(),
    refreshNotice: null,
  })
}

let restore: () => void
beforeEach(() => {
  restore = overrideFlagsForTests({ trading_agents: true })
  ta.rows = [makeSummary({ trade_date: today() })]
})
afterEach(() => {
  cleanup()
  restore()
  vi.clearAllMocks()
})

const line = () => screen.queryByRole('button', { name: /^Research view:/ })

describe("AssetDetailView — the take's research view line", () => {
  it('names the signal and the age under the take, in plain text', () => {
    mockHook(INPUT)
    render(<AssetDetailView instrumentId="acme" />)
    expect(line()?.textContent).toBe('Research view: Overweight · 3 days ago')
    expect(line()?.querySelector('.ta-rating')).toBeNull()
    expect(screen.queryByText('may be outdated')).toBeNull()
  })

  it("adds the server's gloss when it sent one, and nothing derived when it did not", () => {
    mockHook({ ...INPUT, signal: 'Sell', age_days: 0, view: 'negative outlook, not a buy now' })
    render(<AssetDetailView instrumentId="acme" />)
    expect(line()?.textContent).toBe(
      'Research view: Sell · today — negative outlook, not a buy now',
    )
    cleanup()
    mockHook({ ...INPUT, signal: 'Sell', age_days: 0 })
    render(<AssetDetailView instrumentId="acme" />)
    expect(line()?.textContent).toBe('Research view: Sell · today')
  })

  it('says the run may be outdated past three days, in words as well as colour', () => {
    mockHook({ ...INPUT, age_days: 4 })
    render(<AssetDetailView instrumentId="acme" />)
    expect(screen.getByText('may be outdated')).toBeTruthy()
    expect(line()?.getAttribute('aria-label')).toContain('4 days ago. May be outdated')
  })

  it.each([
    [0, 'today'],
    [1, 'yesterday'],
  ])('words an age of %i day(s) as "%s"', (age_days, text) => {
    mockHook({ ...INPUT, age_days })
    render(<AssetDetailView instrumentId="acme" />)
    expect(line()?.textContent).toContain(text)
  })

  it('offers to add the research when the take used none', () => {
    ta.rows = []
    mockHook(null)
    render(<AssetDetailView instrumentId="acme" />)
    expect(line()).toBeNull()
    expect(screen.getByRole('button', { name: 'Add TradingAgents research' })).toBeTruthy()
  })

  describe.each([false, true])('with the trading_agents flag %s', (on) => {
    it(on ? 'shows the row' : 'hides the row', () => {
      restore()
      restore = overrideFlagsForTests({ trading_agents: on })
      mockHook(INPUT)
      render(<AssetDetailView instrumentId="acme" />)
      expect(line() !== null).toBe(on)
    })
  })

  it('opens that run on the Analysis tab and hands the card the view', () => {
    mockHook({ ...INPUT, view: 'steady outlook' })
    render(<AssetDetailView instrumentId="acme" />)
    expect(screen.queryByText(/TradingAgents card/)).toBeNull()
    fireEvent.click(line()!)
    expect(screen.getByText('TradingAgents card tar_000000000001 view steady outlook')).toBeTruthy()
  })

  it('is hidden for crypto and for an asset with no ticker', () => {
    const base = assetDetail()
    mockHook(INPUT, { asset: { ...base.asset, asset_class: 'crypto' } })
    const { unmount } = render(<AssetDetailView instrumentId="acme" />)
    expect(line()).toBeNull()
    expect(screen.queryByRole('button', { name: 'Add TradingAgents research' })).toBeNull()
    unmount()
    mockHook(INPUT, { has_ticker: false, asset: { ...base.asset, ticker: null } })
    render(<AssetDetailView instrumentId="acme" />)
    expect(line()).toBeNull()
  })
})

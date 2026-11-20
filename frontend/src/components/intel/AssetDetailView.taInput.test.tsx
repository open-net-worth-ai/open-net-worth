// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { overrideFlagsForTests } from '../../hooks/useFlag'
import { AssetDetailView } from './AssetDetailView'
import type { AssetDetail, TradingAgentsInput } from '../../types/assetDetail'

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
  TradingAgentsCard: ({ focus }: { focus: { runId: string } | null }) => (
    <div>TradingAgents card {focus?.runId ?? 'unfocused'}</div>
  ),
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

const INPUT: TradingAgentsInput = {
  run_id: 'tar_000000000001',
  signal: 'Overweight',
  trade_date: '2026-11-17',
  age_days: 3,
}

function mockHook(input: TradingAgentsInput | null) {
  mockUseAssetDetail.mockReturnValue({
    data: assetDetail({ trading_agents_input: input }),
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
})
afterEach(() => {
  cleanup()
  restore()
  vi.clearAllMocks()
})

const chip = () => screen.queryByRole('button', { name: /Includes TradingAgents analysis/ })

describe("AssetDetailView — the take's TradingAgents chip", () => {
  it('names the rating and the age under the take', () => {
    mockHook(INPUT)
    render(<AssetDetailView instrumentId="acme" />)
    expect(chip()?.getAttribute('aria-label')).toBe(
      'Includes TradingAgents analysis: Overweight, 3 days ago. Open the full analysis.',
    )
    expect(screen.getByText('3 days ago')).toBeTruthy()
    expect(screen.queryByText('may be outdated')).toBeNull()
  })

  it('says the run may be outdated past three days, in words as well as colour', () => {
    mockHook({ ...INPUT, age_days: 4 })
    render(<AssetDetailView instrumentId="acme" />)
    expect(screen.getByText('may be outdated')).toBeTruthy()
    expect(chip()?.getAttribute('aria-label')).toContain('4 days ago, may be outdated')
  })

  it.each([
    [0, 'today'],
    [1, 'yesterday'],
  ])('words an age of %i day(s) as "%s"', (age_days, text) => {
    mockHook({ ...INPUT, age_days })
    render(<AssetDetailView instrumentId="acme" />)
    expect(screen.getByText(text)).toBeTruthy()
  })

  it('is absent when the take used no run', () => {
    mockHook(null)
    render(<AssetDetailView instrumentId="acme" />)
    expect(chip()).toBeNull()
  })

  describe.each([false, true])('with the trading_agents flag %s', (on) => {
    it(on ? 'shows the chip' : 'hides the chip', () => {
      restore()
      restore = overrideFlagsForTests({ trading_agents: on })
      mockHook(INPUT)
      render(<AssetDetailView instrumentId="acme" />)
      expect(chip() !== null).toBe(on)
    })
  })

  it('opens that run on the Analysis tab', () => {
    mockHook(INPUT)
    render(<AssetDetailView instrumentId="acme" />)
    expect(screen.queryByText(/TradingAgents card/)).toBeNull()
    fireEvent.click(chip()!)
    expect(screen.getByText('TradingAgents card tar_000000000001')).toBeTruthy()
  })
})

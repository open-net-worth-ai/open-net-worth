// @vitest-environment jsdom
//
// Regression (owner-reproduced 2026-10-28, JTKWY): with the live quote unavailable the header
// falls back to the last recorded close, whose `as_of` is a bare "YYYY-MM-DD". Parsed as a UTC
// instant and shown in the exchange's zone it read "As of Oct 26, 8:00 PM EDT" for Oct 27's
// close — the wrong day and a time that never happened. A date-only as_of renders as a date.

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AssetDetailView } from './AssetDetailView'
import type { AssetDetail } from '../../types/assetDetail'

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

function mockPrice(price: Record<string, unknown>) {
  mockUseAssetDetail.mockReturnValue({
    data: {
      instrument_id: 'jtkwy',
      asset: {
        name: 'Just Eat Takeaway.com N.V.',
        symbol: 'JTKWY',
        ticker: 'JTKWY',
        exchange: 'OTC Markets',
        mic: null,
        country: null,
        asset_class: 'equity',
        currency: 'USD',
        description: null,
      },
      position: null,
      price,
      news: [],
      key_issues: [],
      facts: null,
      crypto_facts: null,
      peers: [],
      watchlist: { following: false, price_alerts: [] },
      last_refreshed_at: '2026-10-28T03:00:00Z',
      has_ticker: true,
      portfolio_impact: null,
    } as unknown as AssetDetail,
    loading: false,
    refreshing: false,
    backgroundRefreshing: false,
    error: false,
    refresh: vi.fn(),
    refetch: vi.fn(),
    refreshNotice: null,
  })
}

const NEW_YORK = { timezone: 'America/New_York', is_open: false, session_bounds: null }

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('AssetDetailView — date-only as_of', () => {
  it("shows a recorded close's calendar day, not a converted time", () => {
    mockPrice({
      last: 4.76,
      change_abs: 0,
      change_pct: 0,
      currency: 'USD',
      as_of: '2026-10-27',
      market: NEW_YORK,
      state: 'stale',
      degraded_reason: 'local_history',
    })
    render(<AssetDetailView instrumentId="jtkwy" />)
    const caption = screen.getByText(/^As of/)
    expect(caption.textContent).toMatch(/27/)
    expect(caption.textContent).not.toMatch(/26|PM|AM|EDT/)
  })

  it('still shows the time of a live quote', () => {
    mockPrice({
      last: 4.76,
      change_abs: null,
      change_pct: null,
      currency: 'USD',
      as_of: '2026-10-27T20:00:00Z',
      market: NEW_YORK,
    })
    render(<AssetDetailView instrumentId="jtkwy" />)
    expect(screen.getByText(/^As of/).textContent).toMatch(/4:00\s?PM/)
  })
})

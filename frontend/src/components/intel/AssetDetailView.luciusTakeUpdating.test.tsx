// @vitest-environment jsdom

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

// Minimal valid AssetDetail — same base shape as
// AssetDetailView.marketAttribution.test.tsx's mock, with a held position (so
// `portfolio_impact` — the gate on the "Lucius's take" card — is populated).
function assetDetail(overrides: Partial<AssetDetail> = {}): AssetDetail {
  return {
    instrument_id: 'kstl',
    asset: {
      name: 'Kestrel Dash SE',
      symbol: 'KSTL',
      ticker: 'KSTL',
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

function mockHook(overrides: Partial<ReturnType<typeof useAssetDetail>> = {}) {
  mockUseAssetDetail.mockReturnValue({
    data: assetDetail(),
    loading: false,
    refreshing: false,
    backgroundRefreshing: false,
    error: false,
    refresh: vi.fn(),
    refetch: vi.fn(),
    refreshNotice: null,
    ...overrides,
  })
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("AssetDetailView — Lucius's take card, scoped background-refresh indicator", () => {
  it('shows a scoped "Updating" badge on the card when backgroundRefreshing is true', () => {
    mockHook({ backgroundRefreshing: true })

    render(<AssetDetailView instrumentId="kstl" />)

    // The card itself rendered (gated on portfolio_impact).
    const label = screen.getByText("Lucius's take on your position")
    const badge = screen.getByRole('status', {
      name: "Lucius's take is updating in the background",
    })
    expect(badge.textContent).toContain('Updating')
    // Scoped to the card: the badge sits in the same header as the card's own label,
    // not floating elsewhere on the page.
    expect(label.parentElement?.contains(badge)).toBe(true)
  })

  it('shows no badge when backgroundRefreshing is false', () => {
    mockHook({ backgroundRefreshing: false })

    render(<AssetDetailView instrumentId="kstl" />)

    expect(screen.getByText("Lucius's take on your position")).toBeTruthy()
    expect(
      screen.queryByRole('status', { name: "Lucius's take is updating in the background" }),
    ).toBeNull()
  })

  it('shows no badge for an ordinary explicit refresh (refreshing=true), only for backgroundRefreshing', () => {
    // The explicit Refresh button no longer regenerates this card's fields (ADR
    // 2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation) — showing this badge
    // during `refreshing` would misattribute what's actually being refreshed.
    mockHook({ refreshing: true, backgroundRefreshing: false })

    render(<AssetDetailView instrumentId="kstl" />)

    expect(
      screen.queryByRole('status', { name: "Lucius's take is updating in the background" }),
    ).toBeNull()
  })

  it('renders nothing for the card (and no badge) when there is no portfolio_impact yet', () => {
    mockHook({ data: assetDetail({ portfolio_impact: null }), backgroundRefreshing: true })

    render(<AssetDetailView instrumentId="kstl" />)

    expect(screen.queryByText("Lucius's take on your position")).toBeNull()
    expect(
      screen.queryByRole('status', { name: "Lucius's take is updating in the background" }),
    ).toBeNull()
  })

  // Owner ask 2026-08-30: a bare numbers-only lead line ("Your position: X%, +Y% unrealized.")
  // with no cached narrative reads as a complete, deliberately terse take — never show it
  // standalone. R4 fix (owner-reproduced live bug, 2026-08-31, KODK): an EARLIER version of
  // this behavior showed the generating badge off `!has_narrative` alone, with no regard for
  // whether a pass was actually running — a hung/very-slow backend call left it showing
  // "Generating" for 10+ minutes with nothing to ever turn it off. Now gated on the REAL
  // in-flight signal (`backgroundRefreshing`/`refreshing`) — see generatingLuciusTake's own
  // comment in AssetDetailView.tsx for the full incident writeup.

  it('shows the generating badge and a skeleton body, never the bare numbers, while a real pass is in flight', () => {
    mockHook({
      data: assetDetail({
        portfolio_impact: 'Your position: 1.67% of your net worth, +21.4% unrealized.',
        has_narrative: false,
        refresh_stage: null,
      }),
      backgroundRefreshing: true,
    })

    render(<AssetDetailView instrumentId="kstl" />)

    const badge = screen.getByRole('status', {
      name: "Lucius's take is updating in the background",
    })
    expect(badge.textContent).toContain('Generating')
    // The body is a skeleton (shimmering placeholder rows), not the literal progress text
    // repeated as a sentence — labeled for a11y via aria-label on the same role="status".
    expect(screen.getByRole('status', { name: 'Generating' })).toBeTruthy()
    expect(screen.queryByText('Generating', { selector: 'p' })).toBeNull()
    expect(
      screen.queryByText('Your position: 1.67% of your net worth, +21.4% unrealized.'),
    ).toBeNull()
  })

  it('shows the BARE numbers-only lead line (never a fake "Generating") when there is no narrative and no pass is actually running', () => {
    mockHook({
      data: assetDetail({
        portfolio_impact: 'Your position: 1.67% of your net worth, +21.4% unrealized.',
        has_narrative: false,
        refresh_stage: null,
      }),
      refreshing: false,
      backgroundRefreshing: false,
    })

    render(<AssetDetailView instrumentId="kstl" />)

    expect(
      screen.queryByRole('status', { name: "Lucius's take is updating in the background" }),
    ).toBeNull()
    expect(
      screen.getByText('Your position: 1.67% of your net worth, +21.4% unrealized.'),
    ).toBeTruthy()
  })

  // Owner ask 2026-08-31: stage-specific copy (searching/analysing/retrying) read as noisy
  // and inconsistent across devices on THIS card specifically — always the same plain
  // "Generating" label here regardless of refresh_stage, unlike ResearchingStatus's shared
  // per-stage labels used elsewhere (the news skeleton).
  it('keeps the plain "Generating" badge even once a real refresh_stage lands', () => {
    mockHook({
      data: assetDetail({
        portfolio_impact: 'Your position: 1.67% of your net worth, +21.4% unrealized.',
        has_narrative: false,
        refresh_stage: 'searching',
      }),
      backgroundRefreshing: true,
    })

    render(<AssetDetailView instrumentId="kstl" />)

    const badge = screen.getByRole('status', {
      name: "Lucius's take is updating in the background",
    })
    expect(badge.textContent).toContain('Generating')
    expect(screen.queryByText('Searching the web for news')).toBeNull()
  })
})

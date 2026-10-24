// @vitest-environment jsdom
//
// R2 fix (2026-08-31, owner-reproduced live on a cold KO ad-hoc open): key_issues_refresh_stage
// split off from the shared refresh_stage field, and a dedicated "Key Issues — generating"
// placeholder was added where NOTHING rendered before (no skeleton, no banner, no empty state —
// a blank gap once news had landed but key_issues hadn't). These tests cover the page-level
// consequences of that split: the Key Issues section's own generating state, the misleading
// "click Refresh" empty state no longer showing during a background pass, and the page-level
// RefreshingBanner judging each pipeline's own stage field independently.

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { act } from 'react'
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
// Spy-capable stubs (not plain () => null) so the R5 fix's `factsGenerating` describe block
// below can assert exactly what each card was told, without pulling in either card's own
// rendering (already covered by KeyFactsCard.test.tsx / PeersCard.test.tsx directly).
vi.mock('./KeyFactsCard', () => ({
  KeyFactsCard: ({ generating }: { generating?: boolean }) => (
    <div data-testid="key-facts-card" data-generating={String(!!generating)} />
  ),
}))
vi.mock('./PeersCard', () => ({
  PeersCard: ({ generating }: { generating?: boolean }) => (
    <div data-testid="peers-card" data-generating={String(!!generating)} />
  ),
}))
vi.mock('./AnalystConsensus', () => ({
  AnalystConsensus: () => null,
  AnalystConsensusRailCard: () => null,
}))
vi.mock('./AssetWatchlistMenu', () => ({ AssetWatchlistMenu: () => null }))

// Same base shape as the sibling luciusTakeUpdating/marketAttribution test mocks — a
// never-held, never-watched (ad-hoc) instrument by default, since that's the case the
// concurrent-pipeline bug this file covers actually reproduces on.
function assetDetail(overrides: Partial<AssetDetail> = {}): AssetDetail {
  return {
    instrument_id: 'ko',
    asset: {
      name: 'The Coca-Cola Company',
      symbol: 'KO',
      ticker: 'KO',
      exchange: 'NYSE',
      mic: null,
      country: 'US',
      asset_class: 'equity',
      currency: 'USD',
      description: null,
    },
    position: null,
    price: {
      last: 89.66,
      change_abs: 0.6,
      change_pct: 0.67,
      currency: 'USD',
      as_of: '2026-08-31T09:34:00Z',
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
    last_refreshed_at: null,
    has_ticker: true,
    portfolio_impact: null,
    has_narrative: false,
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

describe('AssetDetailView — Key Issues generating placeholder (Overview tab)', () => {
  it('renders the generating skeleton, not nothing, once news has landed but key_issues has not', () => {
    // The exact live gap: hasNews true, hasIssues false, a background pass active — before
    // this fix, NEITHER the top skeleton (gated on !hasNews) NOR the bottom empty state
    // (this test's OWN case (c) covers that gate) rendered anything at all here.
    mockHook({
      data: assetDetail({ news: [{ headline: 'Coca-Cola hits new high' }] } as never),
      backgroundRefreshing: true,
    })

    render(<AssetDetailView instrumentId="ko" />)

    const badge = screen.getByRole('status', { name: 'Key issues are generating' })
    expect(badge.textContent).toContain('Generating')
    expect(screen.queryByText('No key issues yet')).toBeNull()
  })

  it('renders the generating skeleton while the explicit POST refresh is in flight too', () => {
    mockHook({
      data: assetDetail({ news: [{ headline: 'n' }] } as never),
      refreshing: true,
    })

    render(<AssetDetailView instrumentId="ko" />)

    expect(screen.getByRole('status', { name: 'Key issues are generating' })).toBeTruthy()
  })

  it('shows the real Key Issues panel, not the generating skeleton, once key_issues has landed', () => {
    mockHook({
      data: assetDetail({
        news: [{ headline: 'n' }],
        key_issues: [
          {
            question: 'Q?',
            bullish: { text: 'b', sources: [] },
            bearish: { text: 'r', sources: [] },
          },
        ],
        has_narrative: true,
      } as never),
      backgroundRefreshing: true,
    })

    render(<AssetDetailView instrumentId="ko" />)

    expect(screen.queryByRole('status', { name: 'Key issues are generating' })).toBeNull()
  })

  it('still shows the generating skeleton when key_issues landed but portfolio_narrative did not (owner-reproduced, UBER open-then-watch, 2026-09-03)', () => {
    // The exact live bug: the analyst pass can fill key_issues while leaving
    // portfolio_narrative empty, producing a thin/generic result not grounded in the same-day
    // news already on screen. Treating key_issues alone as "landed" showed that partial result
    // as final with no sign a fuller pass was still coming — hasIssues now requires
    // has_narrative too, so this case falls through to the generating placeholder instead.
    mockHook({
      data: assetDetail({
        news: [{ headline: 'n' }],
        key_issues: [
          {
            question: 'Q?',
            bullish: { text: 'b', sources: [] },
            bearish: { text: 'r', sources: [] },
          },
        ],
        has_narrative: false,
      } as never),
      backgroundRefreshing: true,
    })

    render(<AssetDetailView instrumentId="ko" />)

    const badge = screen.getByRole('status', { name: 'Key issues are generating' })
    expect(badge.textContent).toContain('Generating')
    expect(screen.queryByText('Q?')).toBeNull()
  })

  it('never shows the generating skeleton for a no-ticker instrument (nothing will ever arrive)', () => {
    mockHook({
      data: assetDetail({ news: [{ headline: 'n' }], has_ticker: false } as never),
      backgroundRefreshing: true,
    })

    render(<AssetDetailView instrumentId="ko" />)

    expect(screen.queryByRole('status', { name: 'Key issues are generating' })).toBeNull()
  })
})

describe('AssetDetailView — Key Issues generating placeholder (Analysis tab)', () => {
  it('renders the same generating skeleton on the Analysis tab, not the misleading "click Refresh" empty state', () => {
    mockHook({
      data: assetDetail({ news: [{ headline: 'n' }] } as never),
      backgroundRefreshing: true,
    })

    render(<AssetDetailView instrumentId="ko" />)
    act(() => {
      fireEvent.click(screen.getByRole('tab', { name: 'Analysis' }))
    })

    expect(screen.getByRole('status', { name: 'Key issues are generating' })).toBeTruthy()
    expect(screen.queryByText('No key issues yet')).toBeNull()
  })

  it('falls back to the "No key issues yet" empty state when nothing is generating', () => {
    mockHook({ data: assetDetail({ news: [{ headline: 'n' }] } as never) })

    render(<AssetDetailView instrumentId="ko" />)
    act(() => {
      fireEvent.click(screen.getByRole('tab', { name: 'Analysis' }))
    })

    expect(screen.getByText('No key issues yet')).toBeTruthy()
    expect(
      screen.getByText(
        "Lucius hasn't written the bull and bear case for this asset yet. Refresh to try again.",
      ),
    ).toBeTruthy()
    expect(screen.queryByRole('status', { name: 'Key issues are generating' })).toBeNull()
  })

  // Both client flags lapse while the backend's key_issues pass still runs (a Refresh POST
  // returns before that separate LLM call finishes) — its own stamp keeps Generating up.
  it.each(['analysing', 'retrying'] as const)(
    'shows Generating while key_issues_refresh_stage is %s, with both client flags off',
    (stage) => {
      mockHook({
        data: assetDetail({ news: [{ headline: 'n' }], key_issues_refresh_stage: stage } as never),
      })

      render(<AssetDetailView instrumentId="ko" />)
      act(() => {
        fireEvent.click(screen.getByRole('tab', { name: 'Analysis' }))
      })

      expect(screen.getByRole('status', { name: 'Key issues are generating' })).toBeTruthy()
      expect(screen.queryByText('No key issues yet')).toBeNull()
    },
  )

  it('falls back to the empty state once the stage reads ready', () => {
    mockHook({
      data: assetDetail({ news: [{ headline: 'n' }], key_issues_refresh_stage: 'ready' } as never),
    })

    render(<AssetDetailView instrumentId="ko" />)
    act(() => {
      fireEvent.click(screen.getByRole('tab', { name: 'Analysis' }))
    })

    expect(screen.getByText('No key issues yet')).toBeTruthy()
  })

  it('never shows Generating from the stage for a no-ticker instrument', () => {
    mockHook({
      data: assetDetail({ has_ticker: false, key_issues_refresh_stage: 'analysing' } as never),
    })

    render(<AssetDetailView instrumentId="ko" />)
    act(() => {
      fireEvent.click(screen.getByRole('tab', { name: 'Analysis' }))
    })

    expect(screen.queryByRole('status', { name: 'Key issues are generating' })).toBeNull()
  })
})

describe('AssetDetailView — Overview empty state defers to a running key_issues pass', () => {
  it('hides "No news or analysis yet" while key_issues_refresh_stage is analysing', () => {
    mockHook({ data: assetDetail({ key_issues_refresh_stage: 'analysing' } as never) })

    render(<AssetDetailView instrumentId="ko" />)

    expect(screen.queryByText('No news or analysis yet')).toBeNull()
    expect(screen.getByRole('status', { name: 'Key issues are generating' })).toBeTruthy()
  })
})

describe('AssetDetailView — bottom "No news or analysis yet" empty state', () => {
  it('does NOT show while a background (on-demand) pass is active, even with nothing yet', () => {
    // Before this fix, this condition only excluded `refreshing` (the explicit-POST flag) —
    // a pure on-demand pass (backgroundRefreshing, no POST at all — the held/watched reopen
    // case) still showed the misleading "click Refresh" prompt on top of real in-flight work.
    mockHook({ data: assetDetail(), backgroundRefreshing: true })

    render(<AssetDetailView instrumentId="ko" />)

    expect(screen.queryByText('No news or analysis yet')).toBeNull()
  })

  it('still shows when nothing has landed and nothing is refreshing', () => {
    mockHook({ data: assetDetail() })

    render(<AssetDetailView instrumentId="ko" />)

    expect(screen.getByText('No news or analysis yet')).toBeTruthy()
  })
})

describe('AssetDetailView — page-level RefreshingBanner judges each pipeline stage independently', () => {
  it('stays visible when backgroundRefreshing is the ONLY active pipeline, even if refresh_stage is already ready', () => {
    // The exact live bug: a cold ad-hoc open's news/facts/peers pipeline (refresh_stage)
    // routinely finishes before the separate on-demand key_issues pass
    // (key_issues_refresh_stage). Before the split, refresh_stage reaching "ready" hid the
    // banner outright regardless of what backgroundRefreshing was still doing.
    //
    // has_narrative: true here (unlike this file's default ad-hoc fixture) so
    // generatingLuciusTake stays false — this test isolates the page banner's OWN
    // independent-stage-checking logic from the separate "Lucius's take" card's dedup (R3 fix:
    // that card now ALSO shows its own generating badge for a non-held instrument once
    // generatingLuciusTake is true, and the page banner deliberately defers to it then — a
    // different, correct behavior covered in AssetDetailView.luciusTakeUpdating.test.tsx, not
    // what this test is about).
    mockHook({
      data: assetDetail({
        news: [{ headline: 'n' }],
        refresh_stage: 'ready',
        key_issues_refresh_stage: 'analysing',
        has_narrative: true,
      } as never),
      refreshing: false,
      backgroundRefreshing: true,
    })

    render(<AssetDetailView instrumentId="ko" />)

    expect(
      screen.getByRole('status', { name: 'Refreshing news and analysis in the background' }),
    ).toBeTruthy()
  })

  it('hides once both pipelines individually report ready', () => {
    mockHook({
      data: assetDetail({
        news: [{ headline: 'n' }],
        refresh_stage: 'ready',
        key_issues_refresh_stage: 'ready',
      } as never),
      refreshing: false,
      backgroundRefreshing: false,
    })

    render(<AssetDetailView instrumentId="ko" />)

    expect(
      screen.queryByRole('status', { name: 'Refreshing news and analysis in the background' }),
    ).toBeNull()
  })

  it('stays visible for an explicit refresh whose OWN refresh_stage is not yet ready', () => {
    // has_narrative: true — see the sibling test above for why (isolates the page banner's own
    // logic from the separate "Lucius's take" card dedup, R3 fix).
    mockHook({
      data: assetDetail({
        news: [{ headline: 'n' }],
        refresh_stage: 'searching',
        has_narrative: true,
      } as never),
      refreshing: true,
      backgroundRefreshing: false,
    })

    render(<AssetDetailView instrumentId="ko" />)

    expect(
      screen.getByRole('status', { name: 'Refreshing news and analysis in the background' }),
    ).toBeTruthy()
  })
})

// R5 fix (owner-reproduced live bug, 2026-08-31 — KODK/HPE/XOM): `refreshing` alone turned out
// insufficient — live-verified the facts/peers synthesis call can run 60-120+ real seconds, well
// past whatever kept `refreshing` reliably true in practice. `factsGenerating` now ORs in
// `refresh_stage` (non-null and not yet "ready") as a second, independent, server-truth signal —
// see AssetDetailView.tsx's own comment on `factsGenerating` for the full incident writeup.
describe('AssetDetailView — factsGenerating (KeyFactsCard/PeersCard)', () => {
  it('generating when refreshing is true, regardless of refresh_stage', () => {
    mockHook({
      data: assetDetail({ refresh_stage: 'ready' } as never),
      refreshing: true,
    })
    render(<AssetDetailView instrumentId="ko" />)
    expect(screen.getByTestId('key-facts-card').dataset.generating).toBe('true')
    expect(screen.getByTestId('peers-card').dataset.generating).toBe('true')
  })

  it('generating when refresh_stage is non-ready, even if refreshing is already false — the exact live bug', () => {
    mockHook({
      data: assetDetail({ refresh_stage: 'analysing' } as never),
      refreshing: false,
    })
    render(<AssetDetailView instrumentId="ko" />)
    expect(screen.getByTestId('key-facts-card').dataset.generating).toBe('true')
    expect(screen.getByTestId('peers-card').dataset.generating).toBe('true')
  })

  it('not generating once refresh_stage is ready AND refreshing is false', () => {
    mockHook({
      data: assetDetail({ refresh_stage: 'ready' } as never),
      refreshing: false,
    })
    render(<AssetDetailView instrumentId="ko" />)
    expect(screen.getByTestId('key-facts-card').dataset.generating).toBe('false')
    expect(screen.getByTestId('peers-card').dataset.generating).toBe('false')
  })

  it('not generating for a no-ticker instrument even while refreshing', () => {
    mockHook({
      data: assetDetail({ has_ticker: false, refresh_stage: 'searching' } as never),
      refreshing: true,
    })
    render(<AssetDetailView instrumentId="ko" />)
    expect(screen.getByTestId('key-facts-card').dataset.generating).toBe('false')
    expect(screen.getByTestId('peers-card').dataset.generating).toBe('false')
  })
})

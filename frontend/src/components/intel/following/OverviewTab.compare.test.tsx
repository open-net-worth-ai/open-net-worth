// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { PreviewCompare } from '../../../api/followedPortfolios'
import { clearQueryCache } from '../../../state/queryCache'
import { COPY } from './following.copy'
import { OverviewTab } from './OverviewTab'

const fetchReturns = vi.hoisted(() => vi.fn())
vi.mock('../../../api/portfolio', () => ({ fetchReturns }))
const fetchPreviewCompare = vi.hoisted(() => vi.fn())
vi.mock('../../../api/followedPortfolios', async (orig) => ({
  ...(await orig<typeof import('../../../api/followedPortfolios')>()),
  fetchPreviewCompare,
}))

const LINE = { volatility: 0.2, max_drawdown: -0.1, beta: 1, sharpe: 0.5 }
const SPREAD = { count: 0, top5: 0, largest: null, top: [] }
const COMPARE: PreviewCompare = {
  band: null,
  bands_held: ['equity'],
  overlap: {
    active_share: 0.9,
    shared_count: 0,
    their_count: 1,
    their_weight_held: 0,
    your_weight_shared: 0,
    shared: [],
    unmatched_count: 0,
    unmatched_weight: 0,
  },
  spread: { theirs: SPREAD, yours: SPREAD },
  sectors: { theirs: [], yours: [] },
  risk: {
    since: '2026-01-01',
    benchmark: 'SPY',
    theirs: LINE,
    yours: LINE,
    spy: LINE,
    correlation: null,
    yours_since: '2026-01-01',
  },
}

function renderTab(hasOwnedAssets: boolean) {
  fetchReturns.mockResolvedValue({
    series: [],
    bands_held: ['equity'],
    has_owned_assets: hasOwnedAssets,
  })
  fetchPreviewCompare.mockResolvedValue(COMPARE)
  render(
    <OverviewTab
      perf={{ benchmark: 'SPY', periods: {}, trade_returns: {} }}
      name="ACME Capital"
      kind="fund_13f"
      sourceRef="0000001"
      onRetry={() => {}}
      range="1Y"
      onRange={() => {}}
    />,
  )
}

const compareMenu = () =>
  screen.getByRole('combobox', { name: COPY.compareWith }) as HTMLSelectElement
const optionLabels = () => Array.from(compareMenu().options).map((o) => o.textContent)

afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

describe('"Compare with": All your assets', () => {
  it('is offered when you own an asset, and its risk table carries the caveat', async () => {
    renderTab(true)
    await waitFor(() => expect(optionLabels()).toContain(COPY.compareAllAssets))
    expect(screen.queryByText(COPY.riskOwnedAssetsCaveat)).toBeNull()

    fireEvent.change(compareMenu(), { target: { value: 'all_assets' } })
    await waitFor(() =>
      expect(fetchReturns).toHaveBeenCalledWith('ALL', undefined, { band: 'all_assets' }),
    )
    expect(fetchPreviewCompare).toHaveBeenLastCalledWith('fund_13f', '0000001', null, 'all_assets')
    expect(await screen.findByText(COPY.riskOwnedAssetsCaveat)).toBeTruthy()
  })

  it('is not offered when you own nothing', async () => {
    renderTab(false)
    await waitFor(() => expect(optionLabels()).toContain('Your stocks & ETFs'))
    expect(optionLabels()).not.toContain(COPY.compareAllAssets)
  })
})

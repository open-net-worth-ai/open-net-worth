// @vitest-environment jsdom

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { AssetAlertsCard } from './AssetAlertsCard'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

// The shared PriceAlertsModal self-builds its engine context from usePortfolio; stub it so the
// modal mounts deterministically without the portfolio fetch in this unit test.
vi.mock('../../hooks/useLuciusContext', () => ({
  useLuciusContext: () => ({ holdingsBySymbol: {}, targets: {}, classLabels: {} }),
}))

function props(over: Partial<Parameters<typeof AssetAlertsCard>[0]> = {}) {
  return {
    ticker: 'AAPL',
    assetName: 'Apple Inc.',
    assetClass: 'equity',
    itemId: 'item-1' as string | null,
    alerts: [],
    currency: 'USD',
    currencySymbol: '$',
    livePrice: 200,
    onChanged: vi.fn(),
    ...over,
  }
}

afterEach(cleanup)
beforeEach(() => mockAuthFetch.mockReset())

describe('AssetAlertsCard', () => {
  it('shows the empty state + add button when there are no alerts', () => {
    const { getByText } = render(<AssetAlertsCard {...props()} />)
    expect(getByText(/crosses a price you set/i)).toBeTruthy()
    expect(getByText('Add price alert')).toBeTruthy()
  })

  it('renders alert chips + a manage button when alerts exist', () => {
    const { getByText } = render(
      <AssetAlertsCard {...props({ alerts: [{ op: '>=', value: 150, note: 'trim 20%' }] })} />,
    )
    expect(getByText(/≥ \$150/)).toBeTruthy()
    expect(getByText('trim 20%')).toBeTruthy()
    expect(getByText('Manage', { exact: false })).toBeTruthy()
  })

  it('opens the editor directly when already following (no POST)', async () => {
    const { getByText, findByText } = render(<AssetAlertsCard {...props()} />)
    fireEvent.click(getByText('Add price alert'))
    expect(await findByText('AAPL · Apple Inc.')).toBeTruthy()
    // Opening reads (the monitoring config for the News sources copy) but never writes.
    expect(mockAuthFetch.mock.calls.filter(([, opts]) => opts?.method)).toHaveLength(0)
  })

  it('opens the editor immediately even when itemId is null (no POST)', async () => {
    const { getByText, findByText, queryByText } = render(
      <AssetAlertsCard {...props({ itemId: null })} />,
    )
    fireEvent.click(getByText('Add price alert'))
    expect(await findByText('AAPL · Apple Inc.')).toBeTruthy()
    // No watchlist item → nowhere to store mute/feeds, so the ticker-level settings are hidden.
    expect(queryByText('Mute AAPL')).toBeNull()
    expect(mockAuthFetch).not.toHaveBeenCalled()
  })

  // ALERT-AD-MUTE: a muted ticker (monitor_news=false) opens the modal in the muted state and the
  // mute switch commits itself — guards the prop→toggle→PATCH wiring against silent regressions.
  it('opens muted and un-muting PATCHes monitor_news:true straight away', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
    const { getByText, findByText, getByLabelText } = render(
      <AssetAlertsCard {...props({ monitorNews: false })} />,
    )
    fireEvent.click(getByText('Add price alert'))
    expect(await findByText('Muted')).toBeTruthy()
    expect((getByLabelText('Mute AAPL') as HTMLInputElement).checked).toBe(true)
    fireEvent.click(getByLabelText('Mute AAPL'))
    await waitFor(() => {
      const patch = mockAuthFetch.mock.calls.find(([, opts]) => opts?.method === 'PATCH')
      expect(patch).toBeTruthy()
      expect(patch![0]).toBe('/api/watchlist/items/item-1')
      expect(JSON.parse(patch![1].body)).toMatchObject({ monitor_news: true })
    })
  })

  // Saving an untouched strict-op alert must NOT silently narrow it ('>' must stay '>', not become '>=').
  it('preserves a strict-op alert across an unedited Save', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
    const { getByText } = render(
      <AssetAlertsCard
        {...props({ alerts: [{ id: 'alert-1', op: '>', value: 150, note: 'x' }] })}
      />,
    )
    fireEvent.click(getByText('Manage', { exact: false }))
    fireEvent.click(getByText('Rises above $150.00'))
    fireEvent.click(getByText('Save alert'))
    await waitFor(() => {
      const patch = mockAuthFetch.mock.calls.find(
        ([url, opts]) => url === '/api/alerts/alert-1' && opts?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse(patch![1].body).op).toBe('>')
    })
  })
})

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'

const state = vi.hoisted(() => ({
  hist: {
    data: undefined as unknown,
    error: null as unknown,
    initialLoading: true,
    refresh: vi.fn(),
  },
  since: { stock: null as number | null, bench: null, price: null as number | null },
}))
vi.mock('./useFollowing', () => ({
  usePriceHistory: () => state.hist,
  useSinceFiled: () => state.since,
}))
vi.mock('./TradeChart', () => ({ TradeChart: () => null }))

import { Spotlight } from './ActivityTab'
import { COPY } from './following.copy'
import { fund } from './followingTestKit'

const row = { key: 'k', symbol: 'ACME', name: 'Acme Corp', filedAt: '2026-08-14' } as never

beforeEach(() => {
  vi.useFakeTimers()
  state.hist = {
    data: undefined,
    error: null,
    initialLoading: true,
    refresh: vi.fn(() => Promise.resolve()),
  }
  state.since = { stock: null, bench: null, price: null }
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const show = () => render(<Spotlight row={row} follow={fund()} benchmark="SPY" from="2026-01-01" />)

describe("a picked stock's figures", () => {
  it('are a placeholder while its prices load, never "No price since it was filed"', () => {
    const { container } = show()
    expect(screen.queryByText(COPY.noPriceSince)).toBeNull()
    expect(container.querySelector('.ui-skeleton, [class*="skeleton"]')).toBeTruthy()
  })

  it('stay a placeholder after a failed request, which asks again', () => {
    state.hist = {
      data: undefined,
      error: new Error('busy'),
      initialLoading: false,
      refresh: vi.fn(() => Promise.resolve()),
    }
    show()
    expect(screen.queryByText(COPY.noPriceSince)).toBeNull()
    act(() => void vi.advanceTimersByTime(2000))
    expect(state.hist.refresh).toHaveBeenCalledOnce()
  })

  it('say "No price since it was filed" only once the prices are in and show no move', () => {
    state.hist = { data: { points: [] }, error: null, initialLoading: false, refresh: vi.fn() }
    show()
    expect(screen.getByText(COPY.noPriceSince)).toBeTruthy()
  })

  it('show the move once there is one', () => {
    state.hist = { data: { points: [] }, error: null, initialLoading: false, refresh: vi.fn() }
    state.since = { stock: 0.45, bench: null, price: 219.89 }
    show()
    expect(screen.getByText('+45.0%')).toBeTruthy()
  })
})

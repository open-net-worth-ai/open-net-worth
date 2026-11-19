// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { emitDataChanged } from '../../lucius/bus'
import { usePositionIncome } from './usePositionIncome'

const mockFetch = vi.fn()
vi.mock('../../api/portfolio', () => ({
  fetchPositionIncome: (...args: unknown[]) => mockFetch(...args),
}))

// The holding's Dividends received tile kept the old total after a dividend was corrected on
// the same screen (hand UAT, 2026-11-11).
describe('usePositionIncome', () => {
  it('refetches after a write, so a corrected dividend shows its new total', async () => {
    mockFetch.mockResolvedValueOnce({ trailing_12m_base: 22.95 })
    mockFetch.mockResolvedValueOnce({ trailing_12m_base: 36.45 })
    const { result } = renderHook(() => usePositionIncome('pos-1'))
    await waitFor(() => expect(result.current?.trailing_12m_base).toBe(22.95))

    act(() => emitDataChanged(['portfolio', 'ledger']))

    await waitFor(() => expect(result.current?.trailing_12m_base).toBe(36.45))
    expect(mockFetch).toHaveBeenCalledWith('pos-1')
  })

  it('fetches nothing without a single position', () => {
    mockFetch.mockClear()
    const { result } = renderHook(() => usePositionIncome(null))
    expect(result.current).toBeNull()
    expect(mockFetch).not.toHaveBeenCalled()
  })
})

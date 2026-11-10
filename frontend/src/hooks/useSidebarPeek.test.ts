// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useSidebarPeek } from './useSidebarPeek'

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('useSidebarPeek', () => {
  it('opens on hover and closes after the grace once the pointer leaves', () => {
    const { result } = renderHook(() => useSidebarPeek(true))
    act(() => result.current.enter())
    expect(result.current.open).toBe(true)
    act(() => result.current.leave())
    act(() => void vi.advanceTimersByTime(200))
    expect(result.current.open).toBe(true)
    act(() => void vi.advanceTimersByTime(100))
    expect(result.current.open).toBe(false)
  })

  it('stays open when the pointer moves from the toggle onto the panel', () => {
    const { result } = renderHook(() => useSidebarPeek(true))
    act(() => result.current.enter())
    act(() => result.current.leave())
    act(() => void vi.advanceTimersByTime(100))
    act(() => result.current.enter())
    act(() => void vi.advanceTimersByTime(1000))
    expect(result.current.open).toBe(true)
  })

  it('closes on Escape and on an explicit close (nav choice / pin)', () => {
    const { result } = renderHook(() => useSidebarPeek(true))
    act(() => result.current.enter())
    act(() => void document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
    expect(result.current.open).toBe(false)
    act(() => result.current.enter())
    act(() => result.current.close())
    expect(result.current.open).toBe(false)
  })

  it('never opens while the rail is pinned open', () => {
    const { result, rerender } = renderHook(({ on }) => useSidebarPeek(on), {
      initialProps: { on: false },
    })
    act(() => result.current.enter())
    expect(result.current.open).toBe(false)
    rerender({ on: true })
    expect(result.current.open).toBe(false)
  })

  it('does not reopen by itself when the rail is re-docked and collapsed again', () => {
    const { result, rerender } = renderHook(({ on }) => useSidebarPeek(on), {
      initialProps: { on: true },
    })
    act(() => result.current.enter())
    rerender({ on: false })
    rerender({ on: true })
    expect(result.current.open).toBe(false)
  })
})

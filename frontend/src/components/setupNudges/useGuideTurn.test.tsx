// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { guidedThisVisit, markGuided, resetGuidedForTests, tourShowing } from '../intro/tourTarget'
import { NUDGE_SETTLE_MS, resetNudgeClaimForTests, useGuideTurn } from './useGuideTurn'

vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => '/budget',
}))

const wait = (ms: number) => act(() => void vi.advanceTimersByTime(ms))

const openDialog = () => {
  const el = document.createElement('div')
  el.className = 'modal-overlay'
  document.body.appendChild(el)
  return el
}

describe('useGuideTurn — the nudge keeps the one-guide-per-visit rule', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    resetGuidedForTests()
    resetNudgeClaimForTests()
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it("lets the screen's own intro go first, then claims the visit", () => {
    const { result } = renderHook(() => useGuideTurn(true))
    wait(NUDGE_SETTLE_MS - 800)
    expect(result.current).toBe(false)
    wait(1600)
    expect(result.current).toBe(true)
    expect(guidedThisVisit()).toBe(true)
  })

  it('waits for a later visit when an intro or tour already guided this one', () => {
    markGuided()
    const { result } = renderHook(() => useGuideTurn(true))
    wait(NUDGE_SETTLE_MS * 3)
    expect(result.current).toBe(false)
  })

  it('never claims while a tour is showing or a dialog is open', () => {
    const done = tourShowing()
    const { result } = renderHook(() => useGuideTurn(true))
    wait(NUDGE_SETTLE_MS * 2)
    expect(result.current).toBe(false)
    act(() => done())
    const dialog = openDialog()
    wait(NUDGE_SETTLE_MS * 2)
    expect(result.current).toBe(false)
    dialog.remove()
    wait(NUDGE_SETTLE_MS + 800)
    expect(result.current).toBe(true)
  })

  it('steps out of the way of a dialog opened over it, and comes back after', () => {
    const { result } = renderHook(() => useGuideTurn(true))
    wait(NUDGE_SETTLE_MS + 800)
    const dialog = openDialog()
    wait(800)
    expect(result.current).toBe(false)
    dialog.remove()
    wait(800)
    expect(result.current).toBe(true)
  })

  it('comes straight back after it was unmounted mid-visit (the phone hides it on Lucius)', () => {
    const first = renderHook(() => useGuideTurn(true))
    wait(NUDGE_SETTLE_MS + 800)
    expect(first.result.current).toBe(true)
    first.unmount()
    const again = renderHook(() => useGuideTurn(true))
    expect(again.result.current).toBe(true)
  })

  it('claims nothing when there is no nudge to show', () => {
    renderHook(() => useGuideTurn(false))
    wait(NUDGE_SETTLE_MS * 2)
    expect(guidedThisVisit()).toBe(false)
  })
})

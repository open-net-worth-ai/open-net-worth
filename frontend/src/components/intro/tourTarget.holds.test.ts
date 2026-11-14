// Guides wait while the person is in the middle of something (ADR 2026-11-14): a flow that
// holds them, a moment after it lets go, and a whole visit that arrived by a link.
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  arriveWithPurposeForTests,
  guidesHeld,
  holdGuides,
  resetGuidedForTests,
} from './tourTarget'

afterEach(() => {
  vi.useRealTimers()
  resetGuidedForTests()
})

describe('guidesHeld', () => {
  it('is free on an ordinary visit with nothing going on', () => {
    expect(guidesHeld()).toBe(false)
  })

  it('holds while a flow is mid-way, and a moment after it ends', () => {
    vi.useFakeTimers()
    const release = holdGuides()
    expect(guidesHeld()).toBe(true)
    release()
    // A dialog that opens as the flow ends must come before any guide.
    expect(guidesHeld()).toBe(true)
    vi.advanceTimersByTime(1600)
    expect(guidesHeld()).toBe(false)
  })

  it('holds for as long as any one flow is still going', () => {
    vi.useFakeTimers()
    const first = holdGuides()
    const second = holdGuides()
    first()
    vi.advanceTimersByTime(5000)
    expect(guidesHeld()).toBe(true)
    second()
    vi.advanceTimersByTime(1600)
    expect(guidesHeld()).toBe(false)
  })

  it('holds the whole visit that arrived by a link or a bank return', () => {
    arriveWithPurposeForTests()
    expect(guidesHeld()).toBe(true)
  })
})

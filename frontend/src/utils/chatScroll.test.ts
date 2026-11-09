import { describe, it, expect } from 'vitest'
import { followsBottom, isNearBottom, newScrollIntent, restorePlace } from './chatScroll'

function makeEl(scrollTop: number, scrollHeight: number, clientHeight: number) {
  return { scrollTop, scrollHeight, clientHeight }
}

describe('isNearBottom', () => {
  it('returns true when at the very bottom (dist = 0)', () => {
    expect(isNearBottom(makeEl(900, 1000, 100))).toBe(true)
  })

  it('returns true when within threshold (dist < 80)', () => {
    // dist = 1000 - 925 - 100 = -25 → actually 1000 - 925 = 75; 75 - 100 = no
    // dist = scrollHeight - scrollTop - clientHeight
    // scrollHeight=1000, scrollTop=921, clientHeight=100 → dist=1000-921-100=−21 → clamped? no.
    // dist = 1000 - 921 - 100 = -21 (impossible in practice, scrollTop can't exceed scrollHeight-clientHeight)
    // Let's use realistic numbers:
    // scrollHeight=1000, scrollTop=850, clientHeight=100 → dist=50 < 80 → true
    expect(isNearBottom(makeEl(850, 1000, 100))).toBe(true)
  })

  it('returns true exactly at the threshold boundary (dist = 79)', () => {
    // dist = 1000 - 821 - 100 = 79
    expect(isNearBottom(makeEl(821, 1000, 100))).toBe(true)
  })

  it('returns false when exactly at the threshold (dist = 80)', () => {
    // dist = 1000 - 820 - 100 = 80
    expect(isNearBottom(makeEl(820, 1000, 100))).toBe(false)
  })

  it('returns false when scrolled well above the bottom', () => {
    // dist = 1000 - 100 - 100 = 800
    expect(isNearBottom(makeEl(100, 1000, 100))).toBe(false)
  })

  it('returns false at the top of a long list', () => {
    expect(isNearBottom(makeEl(0, 5000, 400))).toBe(false)
  })

  it('returns true on a short list that fits without scrolling (dist = 0)', () => {
    // No scroll needed: scrollHeight == clientHeight, scrollTop == 0
    expect(isNearBottom(makeEl(0, 400, 400))).toBe(true)
  })

  it('respects a custom threshold', () => {
    // dist = 1000 - 850 - 100 = 50; threshold=40 → false
    expect(isNearBottom(makeEl(850, 1000, 100), 40)).toBe(false)
    // dist = 50; threshold=60 → true
    expect(isNearBottom(makeEl(850, 1000, 100), 60)).toBe(true)
  })
})

describe('followsBottom', () => {
  const NOW = 1_000_000
  const reading = () => ({ readingUp: true, downAt: 0 })

  it('follows when near the bottom and nobody scrolled up', () => {
    expect(followsBottom(makeEl(850, 1000, 100), newScrollIntent(), NOW)).toBe(true)
  })

  it('stops following after the reader scrolled up, even inside the 80px band', () => {
    // 20px up from the bottom: the old rule said "near" and the next token re-pinned.
    expect(followsBottom(makeEl(880, 1000, 100), reading(), NOW)).toBe(false)
  })

  it('does not infer direction from scrollTop: growth moves it as much as the reader does', () => {
    const intent = reading()
    // Two events in a row at nearly the same scrollTop (content grew by what they scrolled).
    expect(followsBottom(makeEl(880, 1000, 100), intent, NOW)).toBe(false)
    expect(followsBottom(makeEl(882, 1010, 100), intent, NOW)).toBe(false)
  })

  it('follows again once the reader is truly back at the bottom', () => {
    const intent = reading()
    expect(followsBottom(makeEl(900, 1000, 100), intent, NOW)).toBe(true)
    expect(intent.readingUp).toBe(false)
  })

  it('re-arms when the reader scrolls DOWN into the band', () => {
    const intent = { readingUp: true, downAt: NOW - 50 }
    expect(followsBottom(makeEl(860, 1000, 100), intent, NOW)).toBe(true)
    expect(intent.readingUp).toBe(false)
  })

  it('does not re-arm from an old downward gesture', () => {
    const intent = { readingUp: true, downAt: NOW - 5_000 }
    expect(followsBottom(makeEl(860, 1000, 100), intent, NOW)).toBe(false)
  })

  it('is not following when far from the bottom', () => {
    expect(followsBottom(makeEl(100, 1000, 100), newScrollIntent(), NOW)).toBe(false)
  })
})

describe('restorePlace', () => {
  function held(top: number, now: { top: number }) {
    const node = { isConnected: true, getBoundingClientRect: () => ({ top: now.top }) }
    return { node: node as unknown as Element, top, last: false }
  }

  it('moves the view by exactly how far the message was pushed down', () => {
    const now = { top: 900 }
    const el = { scrollTop: 100 } as HTMLElement
    const place = held(300, now)
    restorePlace(el, place)
    expect(el.scrollTop).toBe(700)
  })

  it('leaves it alone when the browser already held the place itself', () => {
    const el = { scrollTop: 100 } as HTMLElement
    restorePlace(el, held(300, { top: 300 }))
    expect(el.scrollTop).toBe(100)
  })

  it('does nothing once the message is gone', () => {
    const el = { scrollTop: 100 } as HTMLElement
    const place = held(300, { top: 900 })
    ;(place.node as unknown as { isConnected: boolean }).isConnected = false
    restorePlace(el, place)
    expect(el.scrollTop).toBe(100)
  })
})

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'

// A card whose figures have not arrived: the first look at a fund nobody has opened.
const card = vi.hoisted(() => ({ data: undefined as unknown, error: null as unknown }))
vi.mock('./useFollowing', () => ({ usePreviewSummary: () => card }))

import { PortfolioCard } from './PortfolioCard'
import { SLOW_AFTER_MS } from './useSlow'
import { COPY } from './following.copy'
import { fund, summary } from './followingTestKit'

const entry = {
  kind: 'fund_13f',
  source_ref: '0000000001',
  name: 'Acme Capital',
  subtitle: 'Jane Doe',
} as never

describe('a card that takes a while', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    card.data = undefined
    card.error = null
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('tells the gallery once the wait is real, so its banner can say why', () => {
    const onSlow = vi.fn()
    render(<PortfolioCard entry={entry} onOpen={() => {}} onSlow={onSlow} />)
    expect(onSlow).not.toHaveBeenCalledWith(true)
    act(() => void vi.advanceTimersByTime(SLOW_AFTER_MS))
    expect(onSlow).toHaveBeenLastCalledWith(true)
  })

  it('shows its last figures while they are brought up to date, and counts for the banner', () => {
    // Past its 12 hours or a day old: the server answers at once with the last figures, marked
    // stale, and makes a fresh card behind them (owner, 2026-11-14).
    card.data = { ...summary(fund()), stale: true }
    const onSlow = vi.fn()
    render(<PortfolioCard entry={entry} onOpen={() => {}} onSlow={onSlow} />)
    expect(screen.getByText('+16.3%')).toBeTruthy()
    expect(onSlow).not.toHaveBeenCalledWith(true)
    act(() => void vi.advanceTimersByTime(SLOW_AFTER_MS))
    expect(onSlow).toHaveBeenLastCalledWith(true)
  })

  it('stops counting once the fresh figures arrive', () => {
    card.data = { ...summary(fund()), stale: true }
    const onSlow = vi.fn()
    const view = render(<PortfolioCard entry={entry} onOpen={() => {}} onSlow={onSlow} />)
    act(() => void vi.advanceTimersByTime(SLOW_AFTER_MS))
    expect(onSlow).toHaveBeenLastCalledWith(true)
    card.data = summary(fund())
    view.rerender(<PortfolioCard entry={entry} onOpen={() => {}} onSlow={onSlow} />)
    expect(onSlow).toHaveBeenLastCalledWith(false)
  })

  it('never counts a card whose figures are fresh', () => {
    card.data = summary(fund())
    const onSlow = vi.fn()
    render(<PortfolioCard entry={entry} onOpen={() => {}} onSlow={onSlow} />)
    act(() => void vi.advanceTimersByTime(SLOW_AFTER_MS * 2))
    expect(onSlow).not.toHaveBeenCalledWith(true)
  })

  it('never reports a load that failed as slow', () => {
    card.error = new Error('down')
    const onSlow = vi.fn()
    render(<PortfolioCard entry={entry} onOpen={() => {}} onSlow={onSlow} />)
    act(() => void vi.advanceTimersByTime(SLOW_AFTER_MS))
    expect(onSlow).not.toHaveBeenCalledWith(true)
    expect(screen.getByText(COPY.cardUnavailable)).toBeTruthy()
  })
})

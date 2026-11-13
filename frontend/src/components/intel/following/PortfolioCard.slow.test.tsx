// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'

// A card whose figures have not arrived: the first look at a fund nobody has opened.
const card = vi.hoisted(() => ({ data: undefined as unknown, error: null as unknown }))
vi.mock('./useFollowing', () => ({ usePreviewSummary: () => card }))

import { PortfolioCard } from './PortfolioCard'
import { SLOW_AFTER_MS } from './useSlow'
import { COPY } from './following.copy'

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

  it('never reports a load that failed as slow', () => {
    card.error = new Error('down')
    const onSlow = vi.fn()
    render(<PortfolioCard entry={entry} onOpen={() => {}} onSlow={onSlow} />)
    act(() => void vi.advanceTimersByTime(SLOW_AFTER_MS))
    expect(onSlow).not.toHaveBeenCalledWith(true)
    expect(screen.getByText(COPY.cardUnavailable)).toBeTruthy()
  })
})

// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { RefreshingBanner } from './RefreshingBanner'
import { STAGE_LABELS } from './ResearchingStatus'

afterEach(cleanup)

describe('RefreshingBanner', () => {
  it('shows the real backend stage caption and a single "last saved view" line', () => {
    render(<RefreshingBanner stage="analysing" />)
    expect(screen.getByText(STAGE_LABELS.analysing)).toBeTruthy()
    // The refreshing + stale-view note share one calm line below it.
    expect(screen.getByText('Refreshing. Showing your last saved view')).toBeTruthy()
  })

  it('falls back to a neutral caption when the stage is not yet known', () => {
    render(<RefreshingBanner />)
    expect(screen.getByText('Refreshing')).toBeTruthy()
  })

  it('exposes a polite status role for assistive tech', () => {
    render(<RefreshingBanner stage="searching" />)
    expect(screen.getByRole('status', { name: /Refreshing news and analysis/ })).toBeTruthy()
  })
})

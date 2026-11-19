// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { ResearchingStatus, STAGE_LABELS } from './ResearchingStatus'

afterEach(cleanup)

describe('ResearchingStatus', () => {
  it('shows the real caption for each backend-reported stage', () => {
    const { rerender } = render(<ResearchingStatus stage="searching" />)
    expect(screen.getByText(STAGE_LABELS.searching)).toBeTruthy()

    rerender(<ResearchingStatus stage="analysing" />)
    expect(screen.getByText(STAGE_LABELS.analysing)).toBeTruthy()

    // "ready" should rarely render in practice — AssetDetailView hides the banner entirely
    // once refresh_stage reaches it — but the label still exists so the type stays exhaustive.
    rerender(<ResearchingStatus stage="ready" />)
    expect(screen.getByText(STAGE_LABELS.ready)).toBeTruthy()
  })

  it('shows a neutral, non-cycling caption when no stage is known yet', () => {
    render(<ResearchingStatus />)
    expect(screen.getByText('Refreshing')).toBeTruthy()
    // None of the real stage labels are guessed while the stage is unknown.
    for (const label of Object.values(STAGE_LABELS)) {
      expect(screen.queryByText(label)).toBeFalsy()
    }
  })

  it('treats a null stage the same as unknown', () => {
    render(<ResearchingStatus stage={null} />)
    expect(screen.getByText('Refreshing')).toBeTruthy()
  })
})

// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { PaperAutopilot } from '../../../api/paperTrading'
import { AutopilotTab } from './AutopilotTab'

vi.mock('../../../hooks/useModels', () => ({
  useModels: () => ({ models: [], loading: false, error: null }),
}))

const autopilot: PaperAutopilot = {
  mode: 'auto',
  cadence: 'daily',
  instructions: '',
  model: '',
  effort: 'medium',
  next_run_at: null,
  running: false,
  review_soon: false,
  paused_reason: null,
  runs: [
    {
      id: 'papr_1',
      started_at: '2026-11-20T12:00:00Z',
      finished_at: '2026-11-20T12:01:00Z',
      status: 'done',
      memo: 'Held everything; sold ACME on its stop.',
      watching: 'ACME at 95',
      orders: [],
      lessons: [{ symbol: 'ACME', lesson: 'The stop was set too tight for its swings.' }],
      error: null,
    },
  ],
}

afterEach(cleanup)

describe('AutopilotTab history', () => {
  it("shows a review's lessons on the stocks it fully sold", () => {
    render(
      <AutopilotTab
        autopilot={autopilot}
        mandate={null}
        archived={false}
        onEditMandate={() => {}}
        onNotice={() => {}}
        portfolioId="ppf_1"
      />,
    )
    expect(screen.getByText('Watching: ACME at 95')).toBeTruthy()
    expect(
      screen.getByText('Lesson on ACME: The stop was set too tight for its swings.'),
    ).toBeTruthy()
  })
})

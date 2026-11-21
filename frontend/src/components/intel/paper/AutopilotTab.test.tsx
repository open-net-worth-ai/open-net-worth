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

const withProposals: PaperAutopilot = {
  ...autopilot,
  runs: [
    {
      ...autopilot.runs[0]!,
      orders: [{ id: 'o1', label: 'Paper buy filled: 98 ACME at 100.05', status: 'filled' }],
      proposals: [
        {
          asset: 'ACME',
          symbol: 'ACME',
          action: 'buy',
          conviction: 'high',
          why: 'Leader in its niche.',
          status: 'placed',
          detail: 'to 10% (high conviction)',
          order: 'Paper buy filled: 98 ACME at 100.05',
        },
        {
          asset: 'ACOIN',
          symbol: 'ACOIN',
          action: 'buy',
          conviction: 'low',
          why: 'A small second bet.',
          status: 'skipped',
          detail: 'no cash above the mandate floor',
          order: null,
        },
        {
          asset: 'GC=F',
          symbol: 'GC=F',
          action: 'buy',
          conviction: 'medium',
          why: 'A hedge.',
          status: 'dropped',
          detail: 'Dropped by the challenge: it is already priced for a crisis.',
          order: null,
          challenge: { verdict: 'drop', against: 'It is already priced for a crisis.' },
        },
      ],
    },
  ],
}

describe('AutopilotTab proposals', () => {
  it('shows each proposal with what the app did with it, and its reason', () => {
    render(
      <AutopilotTab
        autopilot={withProposals}
        mandate={null}
        archived={false}
        onEditMandate={() => {}}
        onNotice={() => {}}
        portfolioId="ppf_1"
      />,
    )
    expect(
      screen.getByText(
        /Buy ACME \(high\): placed, to 10% \(high conviction\)\. Paper buy filled: 98 ACME/,
      ),
    ).toBeTruthy()
    expect(
      screen.getByText(/Buy ACOIN \(low\): skipped, no cash above the mandate floor/),
    ).toBeTruthy()
    expect(screen.getByText('Why: A small second bet.')).toBeTruthy()
    // A buy the challenge stopped says so, with its case against.
    expect(screen.getByText(/Buy GC=F \(medium\): dropped, Dropped by the challenge/)).toBeTruthy()
    expect(screen.getByText('Against (drop): It is already priced for a crisis.')).toBeTruthy()
    // The proposals replace the plain order list: each order shows once, inside its proposal.
    expect(screen.queryByText('Paper buy filled: 98 ACME at 100.05')).toBeNull()
  })
})

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

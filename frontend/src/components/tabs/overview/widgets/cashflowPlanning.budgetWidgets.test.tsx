// Spending pulse (an unbudgeted row is "no budget", not a full red bar), Goals (pace badge) and
// Crossover (labelled stat grid) — all rendered from server figures, never recomputed.
// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const summary = vi.hoisted(() => ({ current: null as unknown }))
const projections = vi.hoisted(() => ({ current: null as unknown }))
const fireData = vi.hoisted(() => ({
  current: { progress_pct: 10, years_to_fire: 13 } as {
    progress_pct: number
    years_to_fire: number | null
  },
}))

vi.mock('../../../../hooks/serverState', () => ({
  usePlanningSummary: () => ({ data: summary.current, initialLoading: false, error: null }),
  useCashflowProjections: () => ({
    data: projections.current,
    initialLoading: false,
    error: null,
  }),
}))
vi.mock('../useWidgetData', () => ({
  useAnalytics: () => ({ analytics: null, loading: false, error: false, netWorth: 500_000 }),
}))
vi.mock('../../../../hooks/useFire', () => ({
  useFire: () => ({
    data: fireData.current,
    loading: false,
    error: false,
    retry: vi.fn(),
  }),
}))

import { cashflowPlanningWidgetComponents } from './cashflowPlanning'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

afterEach(() => {
  cleanup()
  fireData.current = { progress_pct: 10, years_to_fire: 13 }
})

function renderWidget(defId: string) {
  const Comp = cashflowPlanningWidgetComponents[defId]!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === defId)!
  const instance: WidgetInstance = {
    instanceId: `w-${defId}`,
    defId,
    x: 0,
    y: 0,
    w: 6,
    h: 260,
    config: {},
  }
  return render(<Comp instance={instance} def={def} onPatch={vi.fn()} onNavigate={vi.fn()} />)
}

const envelope = (key: string, label: string, assigned: number, spent: number) => ({
  key,
  label,
  color: '',
  assigned,
  spent,
  fill_pct: assigned > 0 ? Math.min(100, (spent / assigned) * 100) : 100,
  over: assigned > 0 ? spent > assigned : spent > 0,
  remaining: assigned - spent,
  avg_per_day: 0,
  left_per_day: 0,
})

describe('Spending pulse', () => {
  it('shows an unbudgeted category as "no budget", with no fake $0 budget or red bar', () => {
    const rows = [envelope('food', 'Food', 400, 100), envelope('misc', 'Hobbies', 0, 250)]
    summary.current = {
      pace_pct: 40,
      envelopes: { rows, total_spent: 350, total_budget: 400, pct_of_budget: 88 },
    }
    const { container } = renderWidget('spending')
    const rowEls = Array.from(container.querySelectorAll('.ov-env-row'))
    const hobbies = rowEls.find((el) => el.textContent?.includes('Hobbies'))!
    expect(hobbies.textContent).toContain('no budget')
    expect(hobbies.textContent).not.toContain('/')
    expect(hobbies.querySelector('.ov-env-fill')).toBeNull()
    const food = rowEls.find((el) => el.textContent?.includes('Food'))!
    expect(food.querySelector('.ov-env-fill')).not.toBeNull()
    expect(food.textContent).toContain('/')
  })
})

describe('Goals', () => {
  const goal = (over: object) => ({
    key: 'g',
    label: 'Goal',
    have: 100,
    target: 1000,
    pct: 10,
    by: '2027-06-15',
    risk: false,
    status: 'on_track',
    ...over,
  })

  it('lists the goals with progress and the by-date', () => {
    summary.current = { goals: [goal({ label: 'Emergency fund', pct: 25 })] }
    renderWidget('goals')
    expect(screen.getByText(/Emergency fund/)).toBeTruthy()
    expect(screen.getByText('25%')).toBeTruthy()
    expect(screen.getByText(/by Jun 2027/)).toBeTruthy()
    expect(screen.queryByText(/No savings goals/)).toBeNull()
  })

  it('flags a past-due goal as past due, a behind one as behind', () => {
    summary.current = {
      goals: [
        goal({ key: 'a', label: 'Late', risk: true, status: 'past_due' }),
        goal({ key: 'b', label: 'Slow', risk: true, status: 'behind' }),
      ],
    }
    renderWidget('goals')
    expect(screen.getByText('past due')).toBeTruthy()
    expect(screen.getByText('behind')).toBeTruthy()
  })
})

describe('Crossover', () => {
  it('labels its spending figure as the baseline and lays the stats out as a grid', () => {
    projections.current = {
      crossover: {
        known: true,
        passive_monthly: 77.55,
        expenses_monthly: 3750,
        coverage_pct: 2,
        to_go_monthly: 3672.45,
      },
    }
    const { container } = renderWidget('crossover')
    expect(screen.getByText('Baseline spending')).toBeTruthy()
    expect(screen.getByText('Passive income now')).toBeTruthy()
    expect(screen.getByText('Covered')).toBeTruthy()
    expect(screen.queryByText('Monthly spending')).toBeNull()
    expect(container.querySelectorAll('.ov-cross-stats .ov-cross-stat')).toHaveLength(3)
    expect(container.querySelector('.ov-cross-yr')?.textContent).toMatch(/^\d{4}$/)
  })

  const crossoverAt = (coverage: number) => ({
    crossover: {
      known: true,
      passive_monthly: 77.73,
      expenses_monthly: 4349.91,
      coverage_pct: coverage,
      to_go_monthly: 4272.18,
    },
  })

  // Regression: a book past its FIRE number (most of it a home) read "Now" while passive
  // income covered 2% of spending. The headline must agree with the coverage under it.
  it('does not say Now when passive income covers only part of spending', () => {
    fireData.current = { progress_pct: 116.6, years_to_fire: 0 }
    projections.current = crossoverAt(1.79)
    const { container } = renderWidget('crossover')
    expect(container.querySelector('.ov-cross-yr')?.textContent).toBe('Not yet')
    expect(container.querySelector('.ov-cross-note')).toBeNull()
  })

  it('says Now once passive income covers spending', () => {
    fireData.current = { progress_pct: 40, years_to_fire: 9 }
    projections.current = crossoverAt(104)
    const { container } = renderWidget('crossover')
    expect(container.querySelector('.ov-cross-yr')?.textContent).toBe('Now')
  })
})

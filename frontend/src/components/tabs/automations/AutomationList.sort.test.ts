import { describe, expect, it } from 'vitest'
import { filterAndSortAutomations } from './AutomationList'
import type { Automation } from '../../../api/automations'

// A paused Automation keeps whatever next_run_at it had when it was paused. Under "Next
// run" that stale date is the OLDEST in the list, so the three things that will never run
// led the page. Paused sinks under every sort.
function make(over: Partial<Automation>): Automation {
  return {
    id: over.name ?? 'a',
    name: 'a',
    description: '',
    instructions: '',
    trigger: { kind: 'manual' },
    status: 'active',
    next_run_at: null,
    last_run_at: null,
    ...over,
  } as Automation
}

const names = (rows: Automation[]) => rows.map((r) => r.name)

describe('filterAndSortAutomations — paused sinks', () => {
  const stalePaused = make({
    name: 'paused',
    status: 'paused',
    next_run_at: '2026-07-28T09:00:00Z',
  })
  const soon = make({ name: 'soon', next_run_at: '2026-08-03T09:00:00Z' })
  const later = make({ name: 'later', next_run_at: '2026-08-04T09:00:00Z' })

  it('puts an active run ahead of a paused one with an older next_run_at', () => {
    expect(names(filterAndSortAutomations([stalePaused, soon, later], '', 'next'))).toEqual([
      'soon',
      'later',
      'paused',
    ])
  })

  it('still orders the active ones by next run', () => {
    expect(names(filterAndSortAutomations([later, soon], '', 'next'))).toEqual(['soon', 'later'])
  })

  it('sinks paused under "recent" too', () => {
    const ranJustNow = make({
      name: 'paused',
      status: 'paused',
      last_run_at: '2026-08-03T01:00:00Z',
    })
    const ranEarlier = make({ name: 'active', last_run_at: '2026-08-01T01:00:00Z' })
    expect(names(filterAndSortAutomations([ranJustNow, ranEarlier], '', 'recent'))).toEqual([
      'active',
      'paused',
    ])
  })

  it('sinks paused under "cost" too', () => {
    const pricey = make({
      name: 'paused',
      status: 'paused',
      cost_this_month: 9,
    } as Partial<Automation>)
    const cheap = make({ name: 'active', cost_this_month: 1 } as Partial<Automation>)
    expect(names(filterAndSortAutomations([pricey, cheap], '', 'cost'))).toEqual([
      'active',
      'paused',
    ])
  })

  it('keeps search filtering independent of the paused rule', () => {
    expect(names(filterAndSortAutomations([stalePaused, soon], 'paused', 'next'))).toEqual([
      'paused',
    ])
  })
})

describe('filterAndSortAutomations — name', () => {
  it('orders A→Z, case-insensitive and number-aware, with paused still last', () => {
    const rows = [
      make({ name: 'zeta' }),
      make({ name: 'Report 10' }),
      make({ name: 'alpha', status: 'paused' }),
      make({ name: 'report 2' }),
      make({ name: 'Beta' }),
    ]
    expect(names(filterAndSortAutomations(rows, '', 'name'))).toEqual([
      'Beta',
      'report 2',
      'Report 10',
      'zeta',
      'alpha',
    ])
  })
})

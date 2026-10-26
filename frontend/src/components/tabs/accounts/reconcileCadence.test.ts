// A reconcile is worth doing about once a month: the toast after one names the next check, and
// the header turns to a warning once the last one is older than that (owner, 2026-10-26 —
// "Reconciled — in sync" said nothing useful and nothing nudged a stale account).

import { describe, expect, it } from 'vitest'
import { isReconcileOverdue, reconcileToastMessage, daysSinceReconcile } from './reconcileCadence'

const NOW = new Date(2026, 9, 26, 17, 0)

describe('reconcileToastMessage', () => {
  it('names the next check a month out when the balance already matched', () => {
    expect(reconcileToastMessage(null, 'VND', NOW)).toBe('Reconciled. Next check around Nov 25')
  })

  it('states a booked increase with its sign', () => {
    const msg = reconcileToastMessage({ to_amount: 40000 }, 'VND', NOW)
    expect(msg).toMatch(/^Reconciled with a \+.*40,000.* adjustment\. Next check around Nov 25$/)
  })

  it('states a booked decrease with its sign', () => {
    const msg = reconcileToastMessage({ from_amount: 12.5 }, 'SGD', NOW)
    expect(msg).toMatch(/^Reconciled with a -.*12\.50.* adjustment\./)
  })
})

describe('reconcile staleness', () => {
  it('counts whole days since the last reconcile', () => {
    expect(daysSinceReconcile('2026-08-09', NOW)).toBe(78)
    expect(daysSinceReconcile('2026-10-26', NOW)).toBe(0)
  })

  it('is overdue only past a month', () => {
    expect(isReconcileOverdue('2026-09-26', NOW)).toBe(false)
    expect(isReconcileOverdue('2026-09-25', NOW)).toBe(true)
    expect(isReconcileOverdue('2026-08-09', NOW)).toBe(true)
  })
})

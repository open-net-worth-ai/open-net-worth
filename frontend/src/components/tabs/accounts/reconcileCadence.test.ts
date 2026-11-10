// A reconcile is worth doing at least once a month. The toast after one only confirms what
// happened — a "next check" date read as "don't come back before then" (owner, 2026-10-26); the
// header's overdue warning is what nudges the cadence.

import { describe, expect, it } from 'vitest'
import { isReconcileOverdue, reconcileToastMessage, daysSinceReconcile } from './reconcileCadence'

const NOW = new Date(2026, 9, 26, 17, 0)

describe('reconcileToastMessage', () => {
  it('confirms the balance it was reconciled at when nothing needed adjusting', () => {
    expect(reconcileToastMessage(null, 420204223, 'VND')).toMatch(/^Reconciled at .*420,204,223/)
  })

  it('states a booked increase with its sign', () => {
    expect(reconcileToastMessage({ to_amount: 40000 }, 420244223, 'VND')).toMatch(
      /^Reconciled with a \+.*40,000.* adjustment$/,
    )
  })

  it('states a booked decrease with its sign', () => {
    expect(reconcileToastMessage({ from_amount: 12.5 }, 987.5, 'SGD')).toMatch(
      /^Reconciled with a -.*12\.50.* adjustment$/,
    )
  })

  it('never names a date to come back on', () => {
    expect(reconcileToastMessage(null, 1000, 'SGD')).not.toMatch(/next|check|Nov/i)
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

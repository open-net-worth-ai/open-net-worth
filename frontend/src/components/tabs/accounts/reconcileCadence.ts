/**
 * reconcileCadence.ts — how often an account should be reconciled (at least monthly: long enough
 * to be a habit, short enough that a gap is still easy to trace), and the copy around it.
 */

import { parseDate } from '../../../utils/format'
import { fCur } from './data'

export const RECONCILE_EVERY_DAYS = 30

const DAY_MS = 86_400_000

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

export function daysSinceReconcile(lastReconciled: string, now: Date = new Date()): number {
  const d = parseDate(lastReconciled)
  if (!d) return 0
  return Math.round((startOfDay(now) - startOfDay(d)) / DAY_MS)
}

export function isReconcileOverdue(lastReconciled: string, now: Date = new Date()): boolean {
  return daysSinceReconcile(lastReconciled, now) > RECONCILE_EVERY_DAYS
}

/** The adjustment txn's signed effect on the account: a TO leg raised it, a FROM leg lowered it. */
function adjustmentDelta(adj: { to_amount?: number | null; from_amount?: number | null }): number {
  if (adj.to_amount) return Number(adj.to_amount)
  return -Number(adj.from_amount ?? 0)
}

/** What the reconcile settled — the confirmed figure, or the adjustment it took. No "next
 *  check" date: it read as "don't come back before then"; the header's overdue warning carries
 *  the cadence. */
export function reconcileToastMessage(
  adjustment: { to_amount?: number | null; from_amount?: number | null } | null,
  clearedBalance: number,
  currency: string,
): string {
  if (!adjustment) return `Reconciled at ${fCur(clearedBalance, currency, { cents: true })}`
  const delta = adjustmentDelta(adjustment)
  const signed = (delta > 0 ? '+' : '-') + fCur(Math.abs(delta), currency, { cents: true })
  return `Reconciled with a ${signed} adjustment`
}

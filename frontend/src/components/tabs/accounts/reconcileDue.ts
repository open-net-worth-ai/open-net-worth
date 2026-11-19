/**
 * reconcileDue.ts — which accounts the sidebar flags for reconciling, so the owner sees it
 * without opening each one.
 */

import { daysSinceReconcile, isReconcileOverdue, RECONCILE_EVERY_DAYS } from './reconcileCadence'
import type { Account } from './types'
import { isSyncing } from './valuation'

/** The last reconcile is over a month old, or there has been none and the account has been open
 *  over a month — a newly added account (or a new book) gets that month's grace instead of
 *  turning the whole list amber on day one. Overdue and never are ONE state: both ask the same
 *  thing of the owner (owner, 2026-10-26). */
export function needsReconciling(
  a: Pick<Account, 'lastReconciled' | 'initialBalanceDate'> & Partial<Pick<Account, 'valuation'>>,
  now: Date = new Date(),
): boolean {
  // A Brokerage total account its connector keeps is never due: the next reading replaces any
  // reconcile (ADR 2026-11-10). An unlinked one follows the usual cadence.
  const v = a.valuation
  if (v?.source === 'total' && isSyncing(v)) return false
  if (a.lastReconciled) return isReconcileOverdue(a.lastReconciled, now)
  if (!a.initialBalanceDate) return false
  return daysSinceReconcile(a.initialBalanceDate, now) > RECONCILE_EVERY_DAYS
}

export function countNeedsReconciling(accounts: Account[], now: Date = new Date()): number {
  return accounts.filter((a) => needsReconciling(a, now)).length
}

export function needReconcilingLabel(n: number): string {
  return `${n} ${n === 1 ? 'needs' : 'need'} reconciling`
}

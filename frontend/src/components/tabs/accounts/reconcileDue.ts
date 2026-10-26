/**
 * reconcileDue.ts — which accounts the sidebar flags for reconciling, so the owner sees it
 * without opening each one.
 */

import { daysSinceReconcile, isReconcileOverdue, RECONCILE_EVERY_DAYS } from './reconcileCadence'
import type { Account } from './types'

export type ReconcileDue = 'due' | 'never'

/** `due`: the last reconcile is over a month old. `never`: no reconcile yet, and the account has
 *  been open over a month — a newly added account (or a new book) gets that month's grace
 *  instead of turning the whole list amber on day one. */
export function reconcileDue(
  a: Pick<Account, 'lastReconciled' | 'initialBalanceDate'>,
  now: Date = new Date(),
): ReconcileDue | null {
  if (a.lastReconciled) return isReconcileOverdue(a.lastReconciled, now) ? 'due' : null
  if (!a.initialBalanceDate) return null
  return daysSinceReconcile(a.initialBalanceDate, now) > RECONCILE_EVERY_DAYS ? 'never' : null
}

export function countReconcileDue(accounts: Account[], now: Date = new Date()): number {
  return accounts.filter((a) => reconcileDue(a, now) !== null).length
}

export function needReconcilingLabel(n: number): string {
  return `${n} ${n === 1 ? 'needs' : 'need'} reconciling`
}

/**
 * How an investment or retirement account is valued — Brokerage total or Live holdings
 * (ADR 2026-11-10-an-investment-account-is-valued-by-its-brokerage-total-or-its-live-holdings).
 *
 * Read-only: the server decides the source, books every figure and reports what a linked
 * connector said (`value_source`, `value_sync`, `value_breakdown` on GET /api/accounts). Nothing
 * here computes money — the card's figures are the account's own holdings and balance, the same
 * two numbers every surface adds up (`accountValue`).
 */
import type { Account, ApiAccount, ApiValueSync, LinkStatus, ValueSource } from './types'
import { fDate, getBaseCurrency } from '../../../utils/format'

export interface Valuation {
  source: ValueSource
  /** The connector's last reading, or null when the account isn't linked to one that reports a
   *  balance. */
  sync: ApiValueSync | null
  /** The link's own state: a paused or removed connector keeps nothing up to date. */
  linkStatus: LinkStatus
  sourceId: string
  provider: string
  /** Other accounts at the same institution. */
  siblings: string[]
  /** Brokerage total: the account's total in its own currency, as the server split it. */
  totalNative: number | null
}

export function valuationOf(api: ApiAccount): Valuation | null {
  if (!api.value_source) return null
  const sync = api.value_sync ?? null
  const link = sync ? (api.external_ids ?? []).find((e) => e.source === sync.source) : undefined
  return {
    source: api.value_source,
    sync,
    linkStatus: link?.status ?? 'connected',
    sourceId: sync?.source ?? '',
    provider: sync?.provider_label || link?.provider_label || sync?.source || '',
    siblings: api.value_siblings ?? [],
    totalNative: api.value_breakdown?.total ?? null,
  }
}

/** The connector is actively keeping this account's total. */
export function isSyncing(v: Valuation): boolean {
  return !!v.sync && v.linkStatus === 'connected'
}

export function isWaiting(v: Valuation): boolean {
  return v.source === 'total' && isSyncing(v) && v.sync?.status === 'waiting'
}

/** The figures the card shows. With holdings, every figure is in base currency — the holdings
 *  are, and the sidebar reads the same sum; with none, the account's own balance and currency. */
export function cardFigures(account: Account) {
  const holdings = account.holdingsBase
  if (holdings === 0) {
    return { ccy: account.currency, tracked: 0, untracked: account.balance, total: account.balance }
  }
  return {
    ccy: getBaseCurrency(),
    tracked: holdings,
    untracked: account.balanceBase,
    total: holdings + account.balanceBase,
  }
}

/** An untracked part this small (≤ 2% of the total) is offered a switch to Live holdings — the
 *  same threshold the server keeps it as cash at (`account_value.SMALL_REMAINDER`). */
export const SMALL_REMAINDER = 0.02

export function smallRemainder(account: Account): boolean {
  const f = cardFigures(account)
  return (
    f.tracked > 0 && f.untracked >= 0 && f.total > 0 && f.untracked <= f.total * SMALL_REMAINDER
  )
}

/** When the account's value was last set: the connector's last change, else the last reconcile,
 *  else its starting balance. */
export function valueDate(account: Account): string | null {
  const v = account.valuation
  // A full timestamp (UTC) — shortDate formats it in the user's own day, never UTC's.
  if (v?.sync?.changed_at && v.sync.status !== 'kept') return v.sync.changed_at
  return account.lastReconciled ?? account.initialBalanceDate ?? null
}

export function shortDate(iso: string | null): string {
  if (!iso) return ''
  return fDate(iso.length > 10 ? new Date(iso) : iso)
}

/** The sidebar's one line under the name for a Brokerage total account. */
export function sidebarLine(account: Account): { text: string; warn: boolean } | null {
  const v = account.valuation
  if (!v || v.source !== 'total') return null
  const when = shortDate(valueDate(account))
  if (!isSyncing(v)) return { text: when ? `Entered ${when}` : 'Entered', warn: false }
  switch (v.sync?.status) {
    case 'waiting':
      return { text: 'Waiting for total', warn: false }
    case 'held_back':
      return { text: 'Needs your OK', warn: true }
    case 'reconnect':
      return { text: 'Reconnect', warn: true }
    case 'stale':
    case 'error':
    case 'currency_mismatch':
    case 'no_balance':
    case 'not_found':
      return { text: 'Not updating', warn: true }
    default:
      return { text: when ? `Updated ${when}` : 'Updated', warn: false }
  }
}

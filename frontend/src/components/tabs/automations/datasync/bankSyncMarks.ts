import { useMemo } from 'react'
import { useFlag } from '../../../../hooks/useFlag'
import { fDate, timeAgo } from '../../../../utils/format'
import { useBankSyncs, type BankSyncAccount, type BankSyncStatus } from './bankSync'

// The sync mark on an account the bank sync keeps (ADR 2026-11-13), as YNAB and ZeroSum mark a
// linked account wherever it's listed: trouble shows where you look every day, not only on the
// sync's own page. Green while it syncs, amber when the bank's access ends soon, red when it has
// stopped (access ended, the bank wants a sign-in, the last sync failed).

export type SyncTone = 'ok' | 'warn' | 'bad'

export interface SyncMark {
  tone: SyncTone
  /** What hovering it says. */
  text: string
}

export const STOPPED = new Set([
  'auth_error',
  'reconnect_needed',
  'failed',
  'error',
  'connect_error',
])
export const SIGN_IN = new Set(['auth_error', 'reconnect_needed'])

/** How this account's last read went. One connector can hold several banks, so a bank that
 *  failed shows on its own accounts only (UAT 2026-11-14: a failed bank's error sat on the
 *  other bank's account too). An account no run has read yet falls back to the sync's own. */
export function readState(
  s: BankSyncStatus,
  a: BankSyncAccount,
): { status: string | null; error: string | null } {
  if (a.read_status) return { status: a.read_status, error: a.read_error ?? null }
  return { status: s.last_status, error: s.last_error }
}

export function markFor(s: BankSyncStatus, a: BankSyncAccount): SyncMark | null {
  if (s.mode !== 'data' || !a.sync) return null
  if (a.access_ended)
    return { tone: 'bad', text: `${s.label}: access ended. Renew it to keep syncing.` }
  const read = readState(s, a)
  if (read.status && SIGN_IN.has(read.status))
    return { tone: 'bad', text: `${s.label}: the bank needs you to sign in again.` }
  if (read.status && STOPPED.has(read.status))
    return { tone: 'bad', text: `${s.label}: the last sync failed. ${read.error ?? ''}`.trim() }
  if (a.access_renew_soon && a.access_ends_at)
    return { tone: 'warn', text: `${s.label}: access ends ${fDate(a.access_ends_at)}. Renew it.` }
  const run = s.last_run
  return {
    tone: 'ok',
    text: run
      ? `Synced by ${s.label} ${timeAgo(run.finished_at || run.started_at).replace(/^\d+s ago$/, 'just now')}`
      : `Synced by ${s.label}`,
  }
}

/** Account id → its sync mark, for every account a bank sync keeps. Empty with the flag off. */
export function useBankSyncMarks(): Map<string, SyncMark> {
  const on = useFlag('bank_data_sync') === true
  const syncs = useBankSyncs(on).data
  return useMemo(() => {
    const marks = new Map<string, SyncMark>()
    for (const s of syncs ?? [])
      for (const a of s.accounts ?? []) {
        const mark = markFor(s, a)
        if (mark && a.account_id) marks.set(a.account_id, mark)
      }
    return marks
  }, [syncs])
}

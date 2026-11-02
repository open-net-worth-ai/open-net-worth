import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { invalidateQueryCache, useQueryCache } from '../../../state/queryCache'
import { detailOf } from './runs'

// The scheduled data sync with the migrated YNAB plan (one way or both) (/api/ynab/sync). The server owns the
// schedule and the run; this is its status (shared by the Sync panel, a sync report and the
// connector card's status line) and the one call that settles a review item.

export interface Version {
  date?: string
  account?: string
  payee?: string
  memo?: string
  category?: string | null
  amount: number | null
  cleared?: boolean
}
export interface ReviewItem {
  id: string
  kind: 'conflict' | 'unsupported' | 'failed' | 'before_start' | 'choose'
  entity?: 'txn' | 'assigned' | 'start' | 'category' | 'group' | 'new_category' | 'budget'
  title: string
  reason: string
  onw: Version | null
  ynab: Version | null
  // entity 'category' / 'group': what differs, in words
  fields?: { label: string; onw: string; ynab: string }[]
  // entity 'new_category': what YNAB's category could go with (a 'choose' item) ...
  options?: { ref: string; name: string }[]
  // ... or a category here YNAB would not take (a 'failed' item has no YNAB side)
  category?: string
  // entity 'start': synced rows dated before the account's Starting Balance
  start?: string
  earliest?: string
  count?: number
  balance_now?: number
  balance_counted?: number
  currency?: string
}
export interface LastRun {
  trigger: 'scheduled' | 'manual'
  started_at: string
  finished_at: string
  ok: boolean
  error: string | null
  requests: number
  summary: string
  run_id: string | null
}
export interface SyncStatus {
  token_saved: boolean
  enabled: boolean
  interval: string
  intervals: string[]
  direction: SyncDirection
  directions: SyncDirection[]
  plan_id: string | null
  plan_name: string | null
  running: boolean
  run_id: string | null
  last_run: LastRun | null
  next_run_at: string | null
  conflicts: ReviewItem[]
}

export type SyncDirection = 'both' | 'from_ynab' | 'to_ynab'
export const DIRECTION_LABELS: Record<SyncDirection, string> = {
  both: 'Both ways',
  from_ynab: 'From YNAB',
  to_ynab: 'To YNAB',
}

export const SYNC_ENDPOINT = '/api/ynab/sync'
export const INTERVAL_LABELS: Record<string, string> = {
  '15m': 'Every 15 minutes',
  '30m': 'Every 30 minutes',
  '1h': 'Every hour',
  '6h': 'Every 6 hours',
  daily: 'Once a day',
  manual: 'Manual only',
}

export function useSyncStatus(enabled = true) {
  const [polling, setPolling] = useState(false)
  const status = useQueryCache<SyncStatus>(
    SYNC_ENDPOINT,
    async () => {
      const r = await authFetch(SYNC_ENDPOINT)
      if (!r.ok) throw new Error(await detailOf(r, 'Could not read the sync status.'))
      return (await r.json()) as SyncStatus
    },
    { enabled, refetchInterval: enabled && polling ? 1500 : undefined },
  )
  const running = !!status.data?.running
  if (running !== polling) setPolling(running)
  return status
}

/** A write to the sync; resolves to an error message, or null when it worked. */
export async function sendSync(
  path: string,
  method: string,
  body?: unknown,
): Promise<string | null> {
  try {
    const r = await authFetch(`${SYNC_ENDPOINT}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!r.ok) return await detailOf(r, 'That did not work.')
    invalidateQueryCache('/api/imports/runs')
    invalidateQueryCache(SYNC_ENDPOINT)
    return null
  } catch {
    return 'Could not reach the server.'
  }
}

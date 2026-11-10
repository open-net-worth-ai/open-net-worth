import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { invalidateQueryCache, useQueryCache } from '../../../state/queryCache'
import { YNAB_APP, type BudgetAppInfo } from '../automations/datasync/budgetApps'
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
  /** A first comparison (new pairing, changed envelope): the sides differ, nobody changed both. */
  fresh?: boolean
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
  // entity 'assigned': the month the figure is for
  month?: string
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
  /** Actual only: what the last read said, and what the page keeps saying while it holds. */
  budget_mode?: 'envelope' | 'tracking' | null
  warnings?: SyncWarning[]
  /** Its connector is paused: nothing talks to the server, whatever the sync's own switch says. */
  connector_paused?: boolean
}
export interface SyncWarning {
  kind:
    | 'connector_paused'
    | 'income_in_envelope'
    | 'not_in_this_budget'
    | 'waiting_for_both_ways'
    | 'import_first'
    | 'budget_differs'
  text: string
  /** budget_differs: the budget the connector is set to, which an import would move the sync to. */
  plan_id?: string
}

export type SyncDirection = 'both' | 'from_ynab' | 'to_ynab'
/** What an import does to the sync when it ends: turn it on this way, or leave it. */
export type SyncChoice = SyncDirection | 'off'
export const directionLabels = (app: BudgetAppInfo): Record<SyncDirection, string> => ({
  both: 'Both ways',
  from_ynab: `From ${app.name}`,
  to_ynab: `To ${app.name}`,
})
export const DIRECTION_LABELS = directionLabels(YNAB_APP)

export const SYNC_ENDPOINT = '/api/ynab/sync'
export const INTERVAL_LABELS: Record<string, string> = {
  '15m': 'Every 15 minutes',
  '30m': 'Every 30 minutes',
  '1h': 'Every hour',
  '6h': 'Every 6 hours',
  daily: 'Once a day',
  manual: 'Manual only',
}

export function useSyncStatus(enabled = true, app: BudgetAppInfo = YNAB_APP) {
  const [polling, setPolling] = useState(false)
  const endpoint = `${app.apiPrefix}/sync`
  const status = useQueryCache<SyncStatus>(
    endpoint,
    async () => {
      // Every write revalidates every subscriber, so this is what keeps an app that's off quiet.
      if (!enabled) return undefined as unknown as SyncStatus
      const r = await authFetch(endpoint)
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
  app: BudgetAppInfo = YNAB_APP,
): Promise<string | null> {
  const endpoint = `${app.apiPrefix}/sync`
  try {
    const r = await authFetch(`${endpoint}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!r.ok) return await detailOf(r, 'That did not work.')
    invalidateQueryCache('/api/imports/runs')
    invalidateQueryCache(endpoint)
    return null
  } catch {
    return 'Could not reach the server.'
  }
}

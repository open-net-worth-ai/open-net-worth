import { useRuns, type RunSummary } from '../../imports/runs'
import { INTERVAL_LABELS, useSyncStatus, type SyncStatus } from '../../imports/sync'

// The two-way YNAB sync, seen as an Automation. It is a data sync (fixed rules, no AI model), so
// it has no row in the Automations table: its schedule and runs live on /api/ynab/sync and
// /api/imports/runs, and this reads them into the shape the Automations list and detail need.

export const YNAB_SYNC_KEY = 'ynab'
export const YNAB_SYNC_NAME = 'Two-way sync with YNAB'
export const YNAB_SYNC_BLURB =
  'Keeps your YNAB plan and this book in step, both directions: transactions and each month’s Assigned.'

/** Where setup stands: each step needs the one before it. `enable` is imported but never turned
 * on (no plan chosen yet); `off` is turned off after that, so it keeps its plan. */
export type YnabSyncStage = 'connect' | 'import' | 'enable' | 'off' | 'on'

export interface YnabSyncSummary {
  stage: YnabSyncStage
  /** Shown in the Automations list: set up at least as far as an import. */
  adopted: boolean
  status: SyncStatus | undefined
  runs: RunSummary[] | undefined
  /** The plan the sync pairs with: the one it is set to, else the last one imported. */
  planId: string | null
  planName: string | null
  lastImport: RunSummary | null
  toReview: number
  running: boolean
  loading: boolean
}

export function useYnabSync(includeArchived = false): YnabSyncSummary & {
  refresh: () => void
  error: unknown
} {
  const sync = useSyncStatus()
  const runs = useRuns('ynab', includeArchived)
  const status = sync.data
  const lastImport =
    runs.data?.find((r) => r.kind === 'import' && r.status === 'done' && !r.archived_at) ?? null
  const stage: YnabSyncStage = !status?.token_saved
    ? 'connect'
    : status.enabled
      ? 'on'
      : status.plan_id
        ? 'off'
        : lastImport
          ? 'enable'
          : 'import'
  return {
    stage,
    adopted: stage === 'on' || stage === 'off' || stage === 'enable',
    status,
    runs: runs.data,
    planId: status?.plan_id ?? lastImport?.plan_id ?? null,
    planName: status?.plan_name ?? lastImport?.plan_name ?? null,
    lastImport,
    toReview: status?.conflicts.length ?? 0,
    running: !!status?.running || !!runs.data?.some((r) => r.status === 'running'),
    loading: sync.initialLoading || runs.initialLoading,
    error: sync.error ?? runs.error,
    refresh: () => {
      void sync.refresh()
      void runs.refresh()
    },
  }
}

export function scheduleLabel(status: SyncStatus | undefined): string {
  if (!status?.enabled) return 'Off'
  return INTERVAL_LABELS[status.interval] ?? status.interval
}

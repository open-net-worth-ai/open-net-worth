import { useEffect, useRef } from 'react'
import { useRuns, type RunSummary } from '../../imports/runs'
import { INTERVAL_LABELS, useSyncStatus, type SyncStatus } from '../../imports/sync'
import { YNAB_APP, type BudgetAppInfo } from './budgetApps'

// A budget app's data sync, seen as an Automation. It is a data sync (fixed rules, no AI model),
// so it has no row in the Automations table: its schedule and runs live on the app's /sync and
// /api/imports/runs, and this reads them into the shape the Automations list and detail need.

export const YNAB_SYNC_KEY = YNAB_APP.key
/** The feature flag the plan import and the sync ship behind (core/feature_flags.py). */
export const YNAB_FLAG = YNAB_APP.flag
export const YNAB_SYNC_NAME = YNAB_APP.name
export const YNAB_SYNC_BLURB = YNAB_APP.blurb

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
  /** The import to name beside the plan the sync is on. */
  planImport: RunSummary | null
  toReview: number
  running: boolean
  loading: boolean
}

export type BudgetSync = YnabSyncSummary & {
  refresh: () => void
  error: unknown
}

export function useYnabSync(includeArchived = false, enabled = true): BudgetSync {
  return useBudgetSync(YNAB_APP, includeArchived, enabled)
}

const isImport = (r: RunSummary) => r.kind === 'import' && r.status === 'done' && !r.archived_at

export function useBudgetSync(
  app: BudgetAppInfo,
  includeArchived = false,
  enabled = true,
): BudgetSync {
  const sync = useSyncStatus(enabled, app)
  const runs = useRuns(app.key, includeArchived, enabled)
  const status = sync.data
  // An import turns the sync on and answers "import first" with no write of the page's own, so
  // what the status says goes stale the moment the import ends.
  const importing = !!runs.data?.some((r) => r.kind === 'import' && r.status === 'running')
  const wasImporting = useRef(false)
  const refreshStatus = sync.refresh
  useEffect(() => {
    if (app.refreshesAfterImport && wasImporting.current && !importing) void refreshStatus()
    wasImporting.current = importing
  }, [app.refreshesAfterImport, importing, refreshStatus])
  const lastImport = runs.data?.find(isImport) ?? null
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
    planImport: app.importsPerPlan
      ? (runs.data?.find((r) => isImport(r) && r.plan_id === status?.plan_id) ?? null)
      : lastImport,
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

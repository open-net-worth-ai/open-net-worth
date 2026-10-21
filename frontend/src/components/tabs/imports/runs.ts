import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { invalidateQueryCache, useQueryCache } from '../../../state/queryCache'
import { fDateTime } from '../../../utils/format'
import type { BadgeTone } from '../../ui'
import type { Version } from './sync'

// Import runs: every Import / Verify / Preview of a provider's importer is a record on the
// server (GET /api/imports/runs), so a reload, another device or a restart finds it again. The
// list carries summaries; a run opened as a report carries the whole record.

export type RunKind = 'import' | 'verify' | 'preview' | 'sync'
export type RunStatus = 'running' | 'done' | 'failed' | 'interrupted'

export interface RunSummary {
  id: string
  provider: string
  kind: RunKind
  trigger: 'manual' | 'scheduled'
  status: RunStatus
  started_at: string
  finished_at: string | null
  plan_id: string
  plan_name: string
  outcome: string
  archived_at: string | null
  phase: string
  progress: { done: number; total: number }
  error: string | null
}

export interface Counts {
  created: number
  existing: number
  refused: number
  failed: number
}
export interface Problem {
  item: string
  reason: string
  hint: string
}
export interface Check {
  check: string
  unit: 'money' | 'count' | 'text'
  ynab_value: number | string
  onw_value: number | string
  difference: number | null
  note: string
}
export interface Verification {
  ok: boolean
  checks_total: number
  checks_matching: number
  mismatches: Check[]
  matches: Check[]
}
export interface PreviewAccount {
  name: string
  ynab_type: string
  type: string
  closed: boolean
  opening_balance: number
  transactions: number
}
export interface Preview {
  plan_name: string
  currency: string
  accounts: PreviewAccount[]
  categories: number
  groups: number
  transactions: number
  splits: number
  transfers: number
  budget_months: number
  warnings: string[]
  book_state: 'fresh' | 'rerun' | 'foreign'
  base_currency: string
  currency_change: boolean
  currency_blocked: boolean
  money_data: string[]
  prune: { categories: string[]; groups: string[] }
}
export interface FieldChange {
  name: string
  before: string
  after: string
}
export interface TxnChange {
  direction: 'sent' | 'pulled'
  txn_id: string | null
  date: string | null
  payee: string | null
  account: string | null
  amount: number | null
  currency: string
  action: 'created' | 'updated' | 'deleted'
  fields: FieldChange[]
}
export interface AssignedChange {
  direction: 'sent' | 'pulled'
  category: string
  month: string
  before: number
  after: number
}
export interface SyncConflict {
  id: string
  description: string
  reason: string
  onw_value: Version | null
  ynab_value: Version | null
  status: 'open'
}
export interface Note {
  item: string
  reason: string
}
export interface SyncTotals {
  sent: number
  pulled: number
  assigned: number
  conflicts: number
  deferred: number
  errors: number
}
export interface RunRecord extends RunSummary {
  // a sync run's itemized record (kind === 'sync')
  requests_used?: number
  totals?: SyncTotals
  txn_changes?: TxnChange[]
  assigned_changes?: AssignedChange[]
  conflicts?: SyncConflict[]
  deferred?: Note[]
  errors?: Note[]
  not_stored?: Record<string, number>
  log: { at: string; line: string }[]
  counts: Record<string, Counts>
  problems: Problem[]
  warnings: Problem[]
  book_state: Preview['book_state'] | null
  backup_name: string | null
  preview: Preview | null
  verification: Verification | null
}

export const KIND_LABEL: Record<RunKind, string> = {
  import: 'Import',
  verify: 'Verify',
  preview: 'Preview',
  sync: 'Sync',
}
export const STATUS_LABEL: Record<RunStatus, string> = {
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
  interrupted: 'Interrupted',
}
export const STATUS_TONE: Record<RunStatus, BadgeTone> = {
  running: 'neutral',
  done: 'pos',
  failed: 'neg',
  interrupted: 'warn',
}

const POLL_MS = 2000

export function messageOf(body: { detail?: unknown } | null, fallback: string): string {
  const detail = body?.detail as string | { message?: string } | undefined
  if (typeof detail === 'string') return detail
  return detail?.message || fallback
}

export async function detailOf(r: Response, fallback: string): Promise<string> {
  return messageOf(await r.json().catch(() => null), fallback)
}

export function useRuns(provider: string, includeArchived: boolean, enabled = true) {
  const [polling, setPolling] = useState(false)
  const runs = useQueryCache<RunSummary[]>(
    `/api/imports/runs?provider=${provider}&archived=${includeArchived}`,
    async () => {
      const r = await authFetch(
        `/api/imports/runs?provider=${provider}&archived=${includeArchived}`,
      )
      if (!r.ok) throw new Error(await detailOf(r, 'Could not load the run history.'))
      return ((await r.json()) as { runs: RunSummary[] }).runs
    },
    { enabled, refetchInterval: enabled && polling ? POLL_MS : undefined },
  )
  const running = !!runs.data?.some((r) => r.status === 'running')
  if (running !== polling) setPolling(running)
  return runs
}

export function useRun(id: string | undefined) {
  const [polling, setPolling] = useState(false)
  const run = useQueryCache<RunRecord | null>(
    `/api/imports/runs/${id ?? ''}`,
    async () => {
      if (!id) return null
      const r = await authFetch(`/api/imports/runs/${id}`)
      if (!r.ok) throw new Error(await detailOf(r, 'Could not load that run.'))
      return (await r.json()) as RunRecord
    },
    { enabled: !!id, refetchInterval: polling ? POLL_MS : undefined },
  )
  const running = run.data?.status === 'running'
  if (running !== polling) setPolling(running)
  return run
}

export async function setArchived(id: string, archived: boolean): Promise<string | null> {
  try {
    const r = await authFetch(`/api/imports/runs/${id}/${archived ? 'archive' : 'unarchive'}`, {
      method: 'POST',
    })
    if (!r.ok) return await detailOf(r, 'Could not update the run.')
    invalidateQueryCache('/api/imports/runs')
    return null
  } catch {
    return 'Could not reach the server.'
  }
}

export function formatWhen(iso: string | null): string {
  if (!iso) return '—'
  return fDateTime(iso, 'short', 'always')
}

export function formatDuration(run: Pick<RunSummary, 'started_at' | 'finished_at'>): string {
  if (!run.finished_at) return '—'
  const seconds = Math.max(
    0,
    Math.round((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000),
  )
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

export function runTitle(run: Pick<RunSummary, 'kind' | 'plan_name' | 'plan_id'>): string {
  return `${KIND_LABEL[run.kind]} · ${run.plan_name || run.plan_id}`
}

// TradingAgents reads and writes (flag `trading_agents`). Every read goes through
// `useQueryCache`, which revalidates on every tool write (`data_changed`), so a run that
// Lucius starts or finishes reaches an open asset page without any local fetched state.
import { authFetch } from '../api/client'
import { invalidateQueryCache, useQueryCache } from '../state/queryCache'
import type { QueryCacheResult } from '../state/queryCache'
import { TA_ACTIVE_STATUSES } from '../types/tradingAgentsConstants'
import type {
  TaAnalyst,
  TaConfig,
  TaConfigPatch,
  TaContext,
  TaContextLevel,
  TaCostSummary,
  TaEstimate,
  TaRun,
  TaRunList,
  TaStatus,
} from '../types/tradingAgents'

const SEGMENT = 'trading-agents'
const BASE = `/api/${SEGMENT}`
export const TA_STATUS_KEY = `${BASE}/status`
export const TA_CONFIG_KEY = `${BASE}/config`
export const TA_COST_KEY = '/api/cost/trading-agents'

/** A typed refusal (`{code, message, context}` in `detail`): the server's own sentence. */
export class TaError extends Error {
  readonly code: string | null
  readonly field: string | null

  constructor(message: string, code: string | null = null, field: string | null = null) {
    super(message)
    this.name = 'TaError'
    this.code = code
    this.field = field
  }
}

async function failure(res: Response): Promise<TaError> {
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    // A body that is not JSON falls through to the status line.
  }
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string' && detail) return new TaError(detail)
  if (detail && typeof detail === 'object') {
    const d = detail as { message?: unknown; code?: unknown; context?: { field?: unknown } }
    const field = typeof d.context?.field === 'string' ? d.context.field : null
    if (typeof d.message === 'string' && d.message) {
      return new TaError(d.message, typeof d.code === 'string' ? d.code : null, field)
    }
  }
  return new TaError(`The request failed (HTTP ${res.status}).`)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await authFetch(path, init)
  if (!res.ok) throw await failure(res)
  return (res.status === 204 ? undefined : await res.json()) as T
}

const postJson = (path: string, body?: unknown): Promise<unknown> =>
  request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

// ── reads ───────────────────────────────────────────────────────────────────

/** The status card. It polls while the environment installs; every other change reaches it
 *  through the data event. */
export function useTaStatus(): QueryCacheResult<TaStatus> {
  return useQueryCache(TA_STATUS_KEY, () => request<TaStatus>(TA_STATUS_KEY), {
    staleTime: 15_000,
    refetchInterval: (status) => (status?.install.status === 'installing' ? 3000 : undefined),
  })
}

export function useTaRuns(
  ticker: string,
  limit: number,
  enabled: boolean,
): QueryCacheResult<TaRunList> {
  const key = `${BASE}/runs?ticker=${encodeURIComponent(ticker)}&limit=${limit}`
  return useQueryCache(key, () => request<TaRunList>(key), { enabled })
}

/** One run in full. It polls only while the run is queued or running. */
export function useTaRun(id: string | null): QueryCacheResult<TaRun> {
  const key = `${BASE}/runs/${id ?? ''}`
  return useQueryCache(key, () => request<TaRun>(key), {
    enabled: id !== null,
    refetchInterval: (run) => (run && TA_ACTIVE_STATUSES.includes(run.status) ? 4000 : undefined),
  })
}

// The estimate (a POST that reads run history) and the context preview are priced reads of the
// book, not live views of a run: a run's progress and usage events must not refetch them, so they
// opt out of the data-event sweep (`liveRefresh: false`). They still reload when stale, on mount
// and after any TradingAgents write (`refreshAll`).
export function useTaEstimate(
  ticker: string,
  analysts: TaAnalyst[],
  enabled: boolean,
): QueryCacheResult<TaEstimate> {
  const chosen = [...analysts].sort()
  const key = `${BASE}/estimate?ticker=${encodeURIComponent(ticker)}&analysts=${chosen.join(',')}`
  return useQueryCache(
    key,
    () => postJson(`${BASE}/estimate`, { ticker, analysts: chosen }) as Promise<TaEstimate>,
    { enabled: enabled && chosen.length > 0, staleTime: 60_000, liveRefresh: false },
  )
}

export function useTaContextPreview(
  ticker: string,
  level: TaContextLevel,
  enabled: boolean,
): QueryCacheResult<TaContext> {
  const key = `${BASE}/context-preview?ticker=${encodeURIComponent(ticker)}&context_level=${level}`
  return useQueryCache(key, () => request<TaContext>(key), { enabled, liveRefresh: false })
}

export function useTaConfig(): QueryCacheResult<TaConfig> {
  return useQueryCache(TA_CONFIG_KEY, () => request<TaConfig>(TA_CONFIG_KEY))
}

/** `query` is the window's query string (`start=…&end=…&tz=…`), built like the AI spend panel's. */
export function useTaCost(query: string, enabled: boolean): QueryCacheResult<TaCostSummary> {
  const key = `${TA_COST_KEY}?${query}`
  return useQueryCache(key, () => request<TaCostSummary>(key), { staleTime: 30_000, enabled })
}

// ── writes ──────────────────────────────────────────────────────────────────

/** The write is done; every TradingAgents read (and the cost panel's) reloads. */
function refreshAll(): void {
  invalidateQueryCache(BASE)
  invalidateQueryCache(TA_COST_KEY)
}

export async function startTaRun(
  ticker: string,
  analysts: TaAnalyst[],
  contextLevel: TaContextLevel,
): Promise<string> {
  const body = (await postJson(`${BASE}/runs`, {
    ticker,
    analysts,
    context_level: contextLevel,
  })) as { run_id: string }
  refreshAll()
  return body.run_id
}

export async function cancelTaRun(id: string): Promise<void> {
  await postJson(`${BASE}/runs/${id}/cancel`)
  refreshAll()
}

export async function deleteTaRun(id: string): Promise<void> {
  await request<void>(`${BASE}/runs/${id}`, { method: 'DELETE' })
  refreshAll()
}

export async function acceptTaDisclaimer(): Promise<void> {
  await postJson(`${BASE}/disclaimer`, { accepted: true })
  refreshAll()
}

export async function installTa(): Promise<void> {
  await postJson(`${BASE}/install`)
  refreshAll()
}

export async function saveTaConfig(patch: TaConfigPatch): Promise<TaConfig> {
  const saved = await request<TaConfig>(TA_CONFIG_KEY, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  refreshAll()
  return saved
}

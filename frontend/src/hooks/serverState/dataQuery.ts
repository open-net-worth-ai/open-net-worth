// One request for a screen's heavy reads — `GET /api/query` (backend/services/data_query.py).
//
// Each hook below still owns its cache key (the GET URL it always used), so widgets stay
// standalone and every screen shares the same entries. What changes is the FETCHER: instead of
// its own GET, it queues `{refId, source, params}` here. Everything queued in the same tick —
// a Dashboard's mount, or the post-write revalidation that refreshes every mounted read at once
// — goes out as ONE request, which the server answers from one read of the book, streaming each
// result back as soon as it is ready.
//
// If the batch itself fails (network, an older server without the route), every query falls
// back to its own GET — the batch is a faster way to ask, never a new way to fail.
//
// The `screen_query_batch` flag (a kill switch, on by default) is the ONE switch: off, every
// query with a GET of its own is fetched from it directly, as before the batch existed.
//
// A Dashboard PANEL (`panel_*`, backend/services/dashboard_panels.py) has no GET of its own:
// the query is the only way to ask, so it has no fallback.

import { authFetch } from '../../api/client'
import { getCachedQueryData } from '../../state/queryCache'
import { FEATURE_FLAGS_KEY, type FeatureFlagList } from '../useFlag'
import { fetchJson } from './fetchJson'

/** The sources the server answers (`SOURCES` in services/data_query.py). */
export type DataSource =
  | 'net_worth'
  | 'net_worth_stats'
  | 'portfolio_analytics'
  | 'commitments_summary'
  | 'commitments_coverage'
  | 'planning_summary'
  | 'investments_brief'
  | 'portfolio_xray'
  | 'reports_rollup'
  | 'cashflow_flow'
  | 'cashflow_projections'
  | 'dividends'
  | 'panel_upcoming'
  | 'panel_fx'
  | 'panel_report'
  | 'panel_crossover'
  | 'panel_fire'
  | 'panel_spending_pace'

export type QueryParams = Record<string, string | number | boolean | string[] | undefined>
type Params = QueryParams

/** Sources that wait on the network (live quotes): asked in a request of their own, so the
 *  batch's in-order answers never queue behind them. */
const SOLO_SOURCES: ReadonlySet<DataSource> = new Set(['panel_fx'])

interface Pending {
  refId: string
  source: DataSource
  params: Params
  /** The GET this query stands for: its error label and its fallback. None for a panel. */
  url?: string
  resolve: (value: unknown) => void
  reject: (error: unknown) => void
}

/** Mirrors the server's MAX_QUERIES; a bigger tick is split. */
const MAX_PER_REQUEST = 40

let queue: Pending[] = []
let scheduled = false
let nextRef = 0

/**
 * Ask for one source. Resolves with exactly what `url` (its own GET route) would have returned.
 * Calls in the same tick share one request.
 */
export function querySource<T>(source: DataSource, params: Params, url?: string): Promise<T> {
  if (url && !batchOn()) return fetchJson<T>(url)
  return new Promise<T>((resolve, reject) => {
    const pending: Pending = {
      refId: `${source}#${++nextRef}`,
      source,
      params: definedOnly(params),
      url,
      resolve: resolve as (v: unknown) => void,
      reject,
    }
    if (SOLO_SOURCES.has(source)) {
      void send([pending])
      return
    }
    queue.push(pending)
    if (!scheduled) {
      scheduled = true
      // A macrotask, not a microtask: React runs a commit's effects across more than one
      // microtask, and the point is to catch the whole commit in one request.
      setTimeout(flush, 0)
    }
  })
}

/** `screen_query_batch`, read from the flag list already in the cache. Not loaded yet (or a
 *  list without it) reads as on — the kill switch's default. */
function batchOn(): boolean {
  const list = getCachedQueryData<FeatureFlagList>(FEATURE_FLAGS_KEY)
  const flag = Array.isArray(list?.flags)
    ? list.flags.find((f) => f.name === 'screen_query_batch')
    : undefined
  return flag?.enabled !== false
}

function definedOnly(params: Params): Params {
  const out: Params = {}
  for (const [k, v] of Object.entries(params)) if (v !== undefined) out[k] = v
  return out
}

function flush(): void {
  scheduled = false
  const batch = queue
  queue = []
  for (let i = 0; i < batch.length; i += MAX_PER_REQUEST) {
    void send(batch.slice(i, i + MAX_PER_REQUEST))
  }
}

async function send(batch: Pending[]): Promise<void> {
  const byRef = new Map(batch.map((p) => [p.refId, p]))
  const queries = batch.map(({ refId, source, params }) => ({ refId, source, params }))
  const url = `/api/query?q=${encodeURIComponent(JSON.stringify({ queries }))}`
  try {
    const res = await authFetch(url)
    if (!res.ok) throw new Error(`/api/query → HTTP ${res.status}`)
    await readLines(res, (line) => settle(byRef, line))
  } catch {
    // The batch as a whole failed: ask each still-waiting query the old way.
  }
  for (const p of byRef.values()) {
    if (p.url) fetchJson(p.url).then(p.resolve, p.reject)
    else p.reject(new Error(`/api/query ${p.source} → no answer`))
  }
}

interface Line {
  refId: string
  status: number
  data?: unknown
  error?: string
}

function settle(byRef: Map<string, Pending>, raw: string): void {
  const line = JSON.parse(raw) as Line
  const p = byRef.get(line.refId)
  if (!p) return
  byRef.delete(line.refId)
  if (line.status >= 200 && line.status < 300) p.resolve(line.data)
  // Same shape fetchJson throws, so an entry's error reads the same either way.
  else p.reject(new Error(`${p.url ?? `/api/query ${p.source}`} → HTTP ${line.status}`))
}

/** Feed each complete NDJSON line to `onLine` as it arrives. */
async function readLines(res: Response, onLine: (line: string) => void): Promise<void> {
  if (!res.body) {
    for (const line of (await res.text()).split('\n')) if (line.trim()) onLine(line)
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffered = ''
  for (;;) {
    const { done, value } = await reader.read()
    buffered += decoder.decode(value, { stream: !done })
    let nl = buffered.indexOf('\n')
    while (nl >= 0) {
      const line = buffered.slice(0, nl)
      buffered = buffered.slice(nl + 1)
      if (line.trim()) onLine(line)
      nl = buffered.indexOf('\n')
    }
    if (done) break
  }
  if (buffered.trim()) onLine(buffered)
}
